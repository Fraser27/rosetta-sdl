"""LLM-based SQL generator — for analytical queries that don't match governed metrics."""

from __future__ import annotations

import logging

import boto3

from src.graph.client import GraphClient
from src.text_utils import extract_sql, retry_bedrock

logger = logging.getLogger(__name__)


class NoSchemaError(RuntimeError):
    """Raised when the graph has no tables to ground SQL generation against.

    Without any schema, the LLM has nothing to build a query from and would
    otherwise fabricate a meaningless placeholder (e.g. SELECT 0). Fail loudly
    instead of returning a wrong answer.
    """


def generate_sql(
    question: str,
    graph: GraphClient,
    model_id: str,
) -> str:
    """Generate Athena-compatible SQL from a natural language question using LLM.

    Grounds the model in the FULL catalog schema (every table/column across all
    datasources) rather than a pre-filtered subset, so the LLM chooses the tables
    and joins itself. Raises NoSchemaError if the catalog is empty.
    """
    schema_context = _build_schema_context(graph)
    join_context = _build_join_context(graph)

    prompt = (
        "You are an expert SQL analyst. Generate a single Athena-compatible SQL query "
        "(Presto/Trino dialect) to answer the user's question.\n\n"
        f"Available tables and columns:\n{schema_context}\n"
        f"{join_context}\n"
        f"User question: {question}\n\n"
        "Rules:\n"
        "- Use ONLY the tables and columns listed above\n"
        "- Choose the appropriate table(s) and joins for the question\n"
        "- Use Presto/Trino SQL syntax (Athena-compatible)\n"
        "- Always include a LIMIT clause (default 100)\n"
        "- For date filtering use DATE literals: DATE '2025-01-01'\n"
        "- Return ONLY the SQL query, no explanation\n"
    )

    # Converse API is provider-agnostic (Anthropic, Amazon Nova, etc.), so the
    # query model is freely configurable without per-provider request formats.
    bedrock = boto3.client("bedrock-runtime")
    response = retry_bedrock(lambda: bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024},
    ))
    text = response["output"]["message"]["content"][0]["text"]

    # Robustly extract SQL (handles fenced ```sql, generic ```, or bare SQL).
    sql = extract_sql(text)

    logger.info("Generated SQL for question: %s", question[:80])
    return sql


def _build_schema_context(graph: GraphClient) -> str:
    """Build a compact schema description of the entire catalog for the LLM prompt.

    Raises NoSchemaError if there are no tables — grounding is impossible and any
    generated SQL would be fabricated.
    """
    results = graph.query(
        "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column) "
        "RETURN t.full_name AS table, c.name AS name, c.data_type AS type, "
        "c.description AS desc, coalesce(c.is_deprecated, false) AS deprecated "
        "ORDER BY t.full_name, c.name",
    )
    if not results:
        raise NoSchemaError(
            "No tables in the catalog to generate SQL against. "
            "Scan a datasource to load schema before running ungoverned queries."
        )

    # Group columns by table, preserving the ordered scan.
    by_table: dict[str, list[str]] = {}
    for r in results:
        # A deprecated column's marker takes precedence over its description
        # so the model is steered away from selecting it.
        s = f"{r['name']} ({r['type']})"
        if r.get("deprecated"):
            s += " -- DEPRECATED: avoid using this column"
        elif r.get("desc"):
            s += f" -- {r['desc']}"
        by_table.setdefault(r["table"], []).append(s)

    return "\n".join(f"  {table}: {', '.join(cols)}" for table, cols in by_table.items())


def _build_join_context(graph: GraphClient) -> str:
    """Describe every known join path in the catalog so the LLM can join correctly."""
    rows = graph.query(
        "MATCH (t1:Table)-[j:JOINS_TO]->(t2:Table) "
        "RETURN t1.full_name AS source, t2.full_name AS target, j.on_column AS on_column "
        "ORDER BY source, target",
    )
    if not rows:
        return ""
    lines = "\n".join(
        f"  {r['source']} -> {r['target']} ON {r.get('on_column', '')}" for r in rows
    )
    return f"\nJoin paths:\n{lines}\n"

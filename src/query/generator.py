"""LLM-based SQL generator — for analytical queries that don't match governed metrics."""

from __future__ import annotations

import json
import logging

import boto3

from src.constants import BEDROCK_ANTHROPIC_VERSION
from src.graph.client import GraphClient
from src.query.disambiguator import DisambiguationResult
from src.text_utils import extract_sql, retry_bedrock

logger = logging.getLogger(__name__)


def generate_sql(
    question: str,
    disambiguation: DisambiguationResult,
    graph: GraphClient,
    model_id: str,
) -> str:
    """Generate Athena-compatible SQL from a natural language question using LLM.

    Uses the disambiguation result to provide focused schema context.
    """
    # Gather schema context for matched tables
    schema_context = _build_schema_context(disambiguation.tables, graph)

    # Build join path context
    join_context = ""
    if disambiguation.join_paths:
        join_context = "\nJoin paths:\n"
        for jp in disambiguation.join_paths:
            tables = jp.get("tables", [])
            columns = jp.get("join_columns", [])
            join_context += f"  {' -> '.join(tables)} ON {', '.join(columns)}\n"

    prompt = (
        "You are an expert SQL analyst. Generate a single Athena-compatible SQL query "
        "(Presto/Trino dialect) to answer the user's question.\n\n"
        f"Available tables and columns:\n{schema_context}\n"
        f"{join_context}\n"
        f"User question: {question}\n\n"
        "Rules:\n"
        "- Use ONLY the tables and columns listed above\n"
        "- Use Presto/Trino SQL syntax (Athena-compatible)\n"
        "- Always include a LIMIT clause (default 100)\n"
        "- For date filtering use DATE literals: DATE '2025-01-01'\n"
        "- Return ONLY the SQL query, no explanation\n"
    )

    bedrock = boto3.client("bedrock-runtime")
    response = retry_bedrock(lambda: bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": BEDROCK_ANTHROPIC_VERSION,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }),
    ))
    result = json.loads(response["body"].read())
    text = result["content"][0]["text"]

    # Robustly extract SQL (handles fenced ```sql, generic ```, or bare SQL).
    sql = extract_sql(text)

    logger.info("Generated SQL for question: %s", question[:80])
    return sql


def _build_schema_context(tables: list[str], graph: GraphClient) -> str:
    """Build a compact schema description for the LLM prompt."""
    parts = []
    for table_name in tables:
        results = graph.query(
            "MATCH (t:Table {full_name: $fn})-[:HAS_COLUMN]->(c:Column) "
            "RETURN c.name AS name, c.data_type AS type, c.description AS desc, "
            "coalesce(c.is_deprecated, false) AS deprecated "
            "ORDER BY c.name",
            {"fn": table_name},
        )
        if results:
            # A deprecated column's marker takes precedence over its description
            # so the model is steered away from selecting it.
            col_strs = []
            for r in results:
                s = f"{r['name']} ({r['type']})"
                if r.get("deprecated"):
                    s += " -- DEPRECATED: avoid using this column"
                elif r.get("desc"):
                    s += f" -- {r['desc']}"
                col_strs.append(s)
            parts.append(f"  {table_name}: {', '.join(col_strs)}")

    return "\n".join(parts) if parts else "  No tables found"

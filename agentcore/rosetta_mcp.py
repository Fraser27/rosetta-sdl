"""
Rosetta SDL MCP Server for AgentCore Runtime

Exposes 8 tools that proxy to the Rosetta SDL FastAPI service on EC2.
Runs as a stateless HTTP server inside AgentCore Runtime.

Configuration (environment variables):
  API_URL  — Base URL of the Rosetta SDL FastAPI on EC2 (e.g., http://54.x.x.x:8000)
"""

import json
import logging
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
logger.info(f"Rosetta SDL MCP Server starting — API_URL: {API_URL}")

_headers = {"X-API-Key": INTERNAL_API_KEY} if INTERNAL_API_KEY else {}
_client = httpx.Client(base_url=API_URL, timeout=60.0, headers=_headers)

mcp = FastMCP(
    name="rosetta-sdl",
    instructions="""
    # Rosetta SDL — Semantic Data Layer
    Translate business language into data insights.

    This server provides tools to discover data assets, explore table schemas,
    query governed metrics, execute natural language queries, and search documents
    — all powered by a Neo4j ontology graph over your AWS data lake.

    Start with discover_data_assets to find relevant tables, metrics, or documents.
    Use governed metrics (query_metric) when available — they produce deterministic SQL.
    Use execute_query for ad-hoc natural language questions.
""",
    host="0.0.0.0",
    stateless_http=True,
)


def _get(path: str, params: dict | None = None) -> dict:
    resp = _client.get(path, params=params)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict | None = None) -> dict:
    resp = _client.post(path, json=body)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def discover_data_assets(query: str, limit: int = 20) -> str:
    """Search for tables, metrics, and documents in the data catalog.

    Use this as the FIRST step when a user asks about data.
    Returns ranked results across structured and unstructured data.

    Args:
        query: Natural language search (e.g., "customer transactions", "revenue")
        limit: Max results (1-100, default 20)
    """
    logger.info(f"discover_data_assets: query='{query}', limit={limit}")
    data = _get("/catalog/search", params={"q": query, "limit": min(limit, 100)})
    if not data:
        return "No data assets found matching your query."

    lines = [f"Found {len(data)} results for '{query}':\n"]
    for r in data:
        lines.append(f"  [{r['type']}] {r['name']} (score: {r['score']:.2f})")
        if r.get("description"):
            lines.append(f"    {r['description'][:120]}")
        lines.append(f"    ID: {r['id']}")
    return "\n".join(lines)


@mcp.tool()
async def get_table_details(table_name: str) -> str:
    """Get full schema for a table — columns, types, descriptions, and joins.

    Args:
        table_name: Fully-qualified table name (e.g., "ecommerce.orders")
    """
    logger.info(f"get_table_details: table_name='{table_name}'")
    try:
        data = _get(f"/catalog/tables/{table_name}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Table '{table_name}' not found."
        raise

    lines = [
        f"Table: {data['full_name']}",
        f"Database: {data.get('database', 'N/A')}",
        f"Description: {data.get('description', 'N/A')}",
        f"\nColumns ({len(data.get('columns', []))}):",
    ]
    for col in data.get("columns", []):
        if col.get("name"):
            line = f"  - {col['name']} ({col.get('data_type', '?')})"
            if col.get("description"):
                line += f" — {col['description'][:80]}"
            if col.get("is_partition"):
                line += " [partition]"
            lines.append(line)

    joins = data.get("joins", [])
    if joins:
        lines.append(f"\nJoins ({len(joins)}):")
        for j in joins:
            lines.append(f"  → {j['related_table']} ON {j['on_column']} ({j['join_type']})")

    return "\n".join(lines)


@mcp.tool()
async def find_join_path(source_table: str, target_table: str) -> str:
    """Find the shortest join path between two tables.

    Args:
        source_table: Starting table (e.g., "ecommerce.orders")
        target_table: Target table (e.g., "ecommerce.customers")
    """
    logger.info(f"find_join_path: {source_table} → {target_table}")
    data = _get(f"/catalog/tables/{source_table}/related")
    related = data.get("related", [])

    for r in related:
        if r.get("related_table") == target_table:
            return f"Direct join: {source_table} → {target_table} ON {r['on_column']} ({r['join_type']})"

    if related:
        lines = [f"Tables related to {source_table}:"]
        for r in related:
            lines.append(f"  → {r['related_table']} ON {r['on_column']}")
        return "\n".join(lines)

    return f"No join path found between '{source_table}' and '{target_table}'."


@mcp.tool()
async def list_metrics() -> str:
    """List all governed metrics — pre-approved business measures with SQL definitions.

    Always prefer using a governed metric over writing custom SQL for the same measure.
    """
    logger.info("list_metrics")
    data = _get("/metrics")
    if not data:
        return "No governed metrics found."

    lines = [f"Governed Metrics ({len(data)}):\n"]
    for m in data:
        mtype = m.get('type', 'simple')
        lines.append(f"  {m['name']} (ID: {m['metric_id']}, type: {mtype})")
        lines.append(f"    Definition: {m.get('definition', 'N/A')[:100]}")
        lines.append(f"    Expression: {m.get('expression', 'N/A')}")
        if mtype == 'derived' and m.get('base_metrics'):
            lines.append(f"    Composes: {', '.join(m['base_metrics'])}")
        else:
            lines.append(f"    Source: {m.get('source_table', 'N/A')}")
        if m.get("synonyms"):
            lines.append(f"    Also known as: {', '.join(m['synonyms'])}")
    return "\n".join(lines)


@mcp.tool()
async def get_metric_definition(metric_id: str) -> str:
    """Get full definition of a governed metric including SQL expression.

    Args:
        metric_id: Metric ID (from list_metrics results)
    """
    logger.info(f"get_metric_definition: metric_id='{metric_id}'")
    try:
        m = _get(f"/metrics/{metric_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Metric '{metric_id}' not found."
        raise

    mtype = m.get('type', 'simple')
    lines = [
        f"Metric: {m['name']}",
        f"ID: {m['metric_id']}",
        f"Type: {mtype}",
        f"Definition: {m.get('definition', 'N/A')}",
        f"SQL Expression: {m.get('expression', 'N/A')}",
    ]
    if mtype == 'derived' and m.get('base_metrics'):
        lines.append(f"Base Metrics: {', '.join(m['base_metrics'])}")
    else:
        lines.append(f"Source Table: {m.get('source_table', 'N/A')}")
    if m.get("joins"):
        for j in m["joins"]:
            lines.append(f"  Join: {j.get('join_type','INNER')} {j['table']} ON {j['source_column']}={j['target_column']}")
    if m.get("filters"):
        lines.append(f"Filters: {m['filters']}")
    if m.get("grain"):
        lines.append(f"Grain: {', '.join(m['grain'])}")
    if m.get("synonyms"):
        lines.append(f"Synonyms: {', '.join(m['synonyms'])}")
    return "\n".join(lines)


@mcp.tool()
async def query_metric(
    metric_id: str,
    dimensions: str = "",
    filters: str = "",
    limit: int = 100,
) -> str:
    """Execute a governed metric with optional dimensions and filters.

    Args:
        metric_id: Metric ID
        dimensions: Comma-separated dimension columns (e.g., "order_date,category")
        filters: Comma-separated filters (e.g., "status=completed,year=2025")
        limit: Max rows (default 100)
    """
    logger.info(f"query_metric: metric_id='{metric_id}', dims='{dimensions}', filters='{filters}'")
    body: dict = {"limit": limit}
    if dimensions:
        body["dimensions"] = [d.strip() for d in dimensions.split(",")]
    if filters:
        parsed_filters = []
        for f in filters.split(","):
            if "=" in f:
                col, val = f.split("=", 1)
                parsed_filters.append({"column": col.strip(), "operator": "=", "value": val.strip()})
        body["filters"] = parsed_filters

    try:
        data = _post(f"/metrics/{metric_id}/query", body=body)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            return f"Access denied: {e.response.json().get('detail', 'Unauthorized table')}"
        if e.response.status_code == 400:
            return f"Error: {e.response.json().get('detail', 'Query error')}"
        raise

    results = data.get("results", {})
    columns = results.get("columns", [])
    rows = results.get("rows", [])

    lines = [
        f"Metric: {data.get('metric', 'N/A')}",
        f"SQL: {data.get('sql', 'N/A')}",
        f"Results ({results.get('row_count', 0)} rows, {results.get('duration_ms', 0):.0f}ms):",
        "",
        " | ".join(columns),
        "-" * max(len(" | ".join(columns)), 1),
    ]
    for row in rows[:50]:
        lines.append(" | ".join(str(v) for v in row))
    if len(rows) > 50:
        lines.append(f"... {len(rows) - 50} more rows")
    return "\n".join(lines)


@mcp.tool()
async def execute_query(question: str) -> str:
    """Run a natural language query against the data lake.

    Routes automatically to structured data (Athena) or unstructured data (S3 Vectors)
    based on the question content. Uses governed metrics when available.

    Args:
        question: Natural language question (e.g., "What was total revenue last month?")
    """
    logger.info(f"execute_query: question='{question}'")
    try:
        data = _post("/query/natural-language", body={"question": question})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            return f"Access denied: {e.response.json().get('detail', '')}"
        raise

    lines = [
        f"Route: {data.get('route', 'unknown')}",
        f"Query Type: {data.get('query_type', 'unknown')}",
    ]

    if data.get("metric_name"):
        lines.append(f"Metric: {data['metric_name']}")
    if data.get("sql"):
        lines.append(f"SQL: {data['sql']}")
    if data.get("error"):
        lines.append(f"Error: {data['error']}")

    results = data.get("results")
    if results:
        columns = results.get("columns", [])
        rows = results.get("rows", [])
        lines.extend([
            f"\nResults ({results.get('row_count', 0)} rows):",
            " | ".join(columns),
            "-" * max(len(" | ".join(columns)), 1),
        ])
        for row in rows[:50]:
            lines.append(" | ".join(str(v) for v in row))

    vector_results = data.get("vector_results")
    if vector_results:
        lines.append(f"\nDocument results ({len(vector_results)}):")
        for vr in vector_results:
            lines.append(f"  - [{vr.get('source', '?')}] score: {vr.get('score', 0):.4f}")
            if vr.get("metadata"):
                lines.append(f"    {str(vr['metadata'])[:100]}")

    return "\n".join(lines)


@mcp.tool()
async def search_documents(query: str) -> str:
    """Search unstructured documents (PDFs, policies, manuals) via semantic search.

    Uses S3 Vectors for embedding-based similarity search.

    Args:
        query: Natural language search query
    """
    logger.info(f"search_documents: query='{query}'")
    data = _post("/query/natural-language", body={"question": query})
    vector_results = data.get("vector_results", [])

    if not vector_results:
        return "No documents found matching your query."

    lines = [f"Document search results for '{query}':\n"]
    for vr in vector_results:
        lines.append(f"  Source: {vr.get('source', 'unknown')}")
        lines.append(f"  Score: {vr.get('score', 0):.4f}")
        if vr.get("metadata"):
            lines.append(f"  Metadata: {str(vr['metadata'])[:200]}")
        if vr.get("data"):
            lines.append(f"  Content: {str(vr['data'])[:200]}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def compose_metrics(
    metric_ids: str,
    dimensions: str = "",
    limit: int = 100,
    execute: bool = False,
) -> str:
    """Compose multiple governed metrics into a single CTE query.

    Each metric becomes a WITH clause, joined on shared dimensions.
    Fully governed — no LLM involved.

    Args:
        metric_ids: Comma-separated metric IDs (e.g., "m_001,m_002")
        dimensions: Comma-separated shared dimension columns (e.g., "region,order_date")
        limit: Max rows (default 100)
        execute: If True, also execute the query on Athena
    """
    logger.info(f"compose_metrics: ids='{metric_ids}', dims='{dimensions}', execute={execute}")
    ids = [s.strip() for s in metric_ids.split(",") if s.strip()]
    if len(ids) < 2:
        return "Error: At least 2 metric IDs required for composition."

    dims = [d.strip() for d in dimensions.split(",") if d.strip()] if dimensions else []

    try:
        data = _post("/query/compose", body={
            "metric_ids": ids,
            "dimensions": dims,
            "limit": limit,
            "execute": execute,
        })
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 403):
            return f"Error: {e.response.json().get('detail', 'Query error')}"
        raise

    lines = [
        f"Composed Metric: {data.get('metric', 'N/A')}",
        f"Query Type: {data.get('query_type', 'governed')}",
        f"SQL:\n{data.get('sql', 'N/A')}",
    ]

    results = data.get("results")
    if results:
        columns = results.get("columns", [])
        rows = results.get("rows", [])
        lines.extend([
            f"\nResults ({results.get('row_count', 0)} rows, {results.get('duration_ms', 0):.0f}ms):",
            " | ".join(columns),
            "-" * max(len(" | ".join(columns)), 1),
        ])
        for row in rows[:50]:
            lines.append(" | ".join(str(v) for v in row))
        if len(rows) > 50:
            lines.append(f"... {len(rows) - 50} more rows")

    return "\n".join(lines)


@mcp.tool()
async def plan_query(question: str) -> str:
    """Plan a query WITHOUT executing it — returns SQL and/or vector search params.

    Use this when you want to get the SQL or search parameters from Rosetta SDL,
    then execute them yourself via separate Athena or S3Vectors MCP servers.

    Returns:
      - route: structured | unstructured | both
      - sql: The firewall-validated SQL (for Athena execution)
      - tables: Matched tables from the graph
      - join_paths: Join paths between tables
      - vector_searches: [{bucket, index}] for S3 Vectors execution
      - metric_name: If a governed metric was matched (deterministic SQL)

    Args:
        question: Natural language question (e.g., "What was total revenue last month?")
    """
    logger.info(f"plan_query: question='{question}'")
    data = _post("/query/plan", body={"question": question})

    lines = [
        f"Route: {data.get('route', 'unknown')}",
        f"Query Type: {data.get('query_type', 'unknown')}",
    ]

    if data.get("metric_name"):
        lines.append(f"Metric: {data['metric_name']} (governed — deterministic SQL)")
    if data.get("sql"):
        lines.append(f"SQL: {data['sql']}")
    if data.get("tables"):
        lines.append(f"Tables: {', '.join(data['tables'])}")
    if data.get("join_paths"):
        for jp in data["join_paths"]:
            tables = jp.get("tables", [])
            cols = jp.get("join_columns", [])
            lines.append(f"Join: {' -> '.join(tables)} ON {', '.join(cols)}")
    if data.get("vector_searches"):
        lines.append(f"Vector searches ({len(data['vector_searches'])}):")
        for vs in data["vector_searches"]:
            lines.append(f"  bucket: {vs['bucket']}, index: {vs['index']}")
    if data.get("error"):
        lines.append(f"Error: {data['error']}")

    return "\n".join(lines)


def main():
    logger.info("Starting Rosetta SDL MCP Server with Streamable HTTP transport")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()

"""Catalog API routes — table discovery and schema exploration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.catalog.models import SearchResult, TableSummary
from src.graph import queries
from src.graph.client import GraphClient

router = APIRouter(prefix="/catalog", tags=["catalog"])

# Injected at startup
_graph: GraphClient | None = None


def init(graph: GraphClient) -> None:
    global _graph
    _graph = graph


def _get_graph() -> GraphClient:
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    return _graph


@router.get("/tables", response_model=list[TableSummary])
async def list_tables():
    """List all tables in the semantic layer."""
    results = _get_graph().query(queries.LIST_TABLES)
    return [TableSummary(**r) for r in results]


@router.get("/tables/{table_name:path}")
async def get_table_details(table_name: str):
    """Get full schema details for a table."""
    results = _get_graph().query(queries.GET_TABLE_DETAILS, {"full_name": table_name})
    if not results:
        raise HTTPException(404, f"Table '{table_name}' not found")

    table = results[0]
    # Get join relationships
    joins = _get_graph().query(queries.GET_TABLE_JOINS, {"full_name": table_name})
    table["joins"] = joins
    return table


@router.get("/tables/{table_name:path}/related")
async def get_related_tables(table_name: str):
    """Find tables that can be joined to the given table."""
    joins = _get_graph().query(queries.GET_TABLE_JOINS, {"full_name": table_name})
    if not joins:
        return {"table": table_name, "related": [], "message": "No join paths found"}
    return {"table": table_name, "related": joins}


@router.get("/search", response_model=list[SearchResult])
async def search_catalog(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search tables, metrics, and documents by keyword."""
    results = _get_graph().query(queries.SEARCH_ALL, {
        "query": q,
        "min_score": 0.3,
        "limit": limit,
    })
    return [SearchResult(**r) for r in results]


@router.get("/graph")
async def graph_summary():
    """Get a summary of the graph (node/edge counts by type)."""
    results = _get_graph().query(queries.GRAPH_SUMMARY)
    return {"nodes": {r["label"]: r["cnt"] for r in results}}


@router.get("/graph/data")
async def graph_data():
    """Get all nodes and edges for graph visualization."""
    graph = _get_graph()
    node_results = graph.query(queries.GRAPH_DATA)
    edge_results = graph.query(queries.GRAPH_EDGES)
    nodes = node_results[0]["nodes"] if node_results else []
    edges = edge_results[0]["edges"] if edge_results else []
    return {"nodes": nodes, "edges": edges}

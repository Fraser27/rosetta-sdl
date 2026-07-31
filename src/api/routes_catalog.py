"""Catalog API routes — table discovery and schema exploration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator

from src.catalog.models import SearchResult, TableSummary
from src.config import SemanticLayerConfig
from src.graph import queries
from src.graph.client import GraphClient
from src.query.embeddings import get_embedding
from src.text_utils import MAX_DESCRIPTION_WORDS, exceeds_word_limit, truncate_words

# Uploaded document metadata is capped before embedding (feeds a 1024-dim index).
MAX_METADATA_WORDS = 1000

router = APIRouter(prefix="/catalog", tags=["catalog"])

# Injected at startup
_graph: GraphClient | None = None
_config: SemanticLayerConfig | None = None


def init(graph: GraphClient, config: SemanticLayerConfig | None = None) -> None:
    global _graph, _config
    _graph = graph
    _config = config


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


class DescriptionUpdate(BaseModel):
    description: str

    @field_validator("description")
    @classmethod
    def _cap_words(cls, v: str) -> str:
        # Manual edits are rejected (not truncated) so the user can fix them.
        if exceeds_word_limit(v):
            raise ValueError(f"Description must be {MAX_DESCRIPTION_WORDS} words or fewer")
        return v


class DeprecationUpdate(BaseModel):
    is_deprecated: bool


# NOTE: column routes MUST be registered before the table-description route.
# The table route's greedy `{table_name:path}` converter otherwise swallows
# `.../columns/{col}/description` URLs and shadows these handlers.
@router.patch("/tables/{table_name:path}/columns/{column_name}/description")
async def update_column_description(table_name: str, column_name: str, req: DescriptionUpdate):
    """Update a column's description."""
    graph = _get_graph()
    results = graph.query(
        "MATCH (c:Column {name: $name, table: $table}) RETURN c",
        {"name": column_name, "table": table_name},
    )
    if not results:
        raise HTTPException(404, f"Column '{column_name}' not found in '{table_name}'")
    graph.write(
        "MATCH (c:Column {name: $name, table: $table}) SET c.description = $desc",
        {"name": column_name, "table": table_name, "desc": req.description},
    )
    return {"ok": True}


@router.patch("/tables/{table_name:path}/columns/{column_name}/deprecation")
async def update_column_deprecation(table_name: str, column_name: str, req: DeprecationUpdate):
    """Mark a column deprecated (or not). Deprecation takes precedence over the
    description in the UI and steers the LLM SQL generator away from the column."""
    graph = _get_graph()
    results = graph.query(
        "MATCH (c:Column {name: $name, table: $table}) RETURN c",
        {"name": column_name, "table": table_name},
    )
    if not results:
        raise HTTPException(404, f"Column '{column_name}' not found in '{table_name}'")
    graph.write(
        "MATCH (c:Column {name: $name, table: $table}) SET c.is_deprecated = $dep",
        {"name": column_name, "table": table_name, "dep": req.is_deprecated},
    )
    return {"ok": True}


@router.patch("/tables/{table_name:path}/description")
async def update_table_description(table_name: str, req: DescriptionUpdate):
    """Update a table's description."""
    graph = _get_graph()
    results = graph.query("MATCH (t:Table {full_name: $fn}) RETURN t", {"fn": table_name})
    if not results:
        raise HTTPException(404, f"Table '{table_name}' not found")
    graph.write(
        "MATCH (t:Table {full_name: $fn}) SET t.description = $desc",
        {"fn": table_name, "desc": req.description},
    )
    return {"ok": True}


@router.get("/tables/{table_name:path}/related")
async def get_related_tables(table_name: str):
    """Find tables that can be joined to the given table."""
    joins = _get_graph().query(queries.GET_TABLE_JOINS, {"full_name": table_name})
    if not joins:
        return {"table": table_name, "related": [], "message": "No join paths found"}
    return {"table": table_name, "related": joins}


@router.get("/documents")
async def list_documents():
    """List all documents in the semantic layer."""
    results = _get_graph().query(queries.LIST_DOCUMENTS)
    return results


@router.get("/documents/{s3_key:path}")
async def get_document(s3_key: str):
    """Get full details for a document including metadata keys and relationships."""
    results = _get_graph().query(queries.GET_DOCUMENT, {"s3_key": s3_key})
    if not results:
        raise HTTPException(404, f"Document '{s3_key}' not found")
    return results[0]


@router.patch("/documents/{s3_key:path}/description")
async def update_document_description(s3_key: str, req: DescriptionUpdate):
    """Update a document's description."""
    graph = _get_graph()
    results = graph.query("MATCH (d:Document {s3_key: $key}) RETURN d", {"key": s3_key})
    if not results:
        raise HTTPException(404, f"Document '{s3_key}' not found")
    graph.write(
        "MATCH (d:Document {s3_key: $key}) SET d.description = $desc",
        {"key": s3_key, "desc": req.description},
    )
    return {"ok": True}


@router.post("/documents/{s3_key:path}/metadata-file")
async def upload_document_metadata(s3_key: str, file: UploadFile):
    """Upload a txt/md metadata file for a document; vectorize it for routing.

    The file text is capped at MAX_METADATA_WORDS, embedded with the configured
    S3 Vectors embedding model, and stored on the Document node so the router's
    semantic fallback can match questions to this document/index.
    """
    graph = _get_graph()
    existing = graph.query("MATCH (d:Document {s3_key: $key}) RETURN d", {"key": s3_key})
    if not existing:
        raise HTTPException(404, f"Document '{s3_key}' not found")

    name = (file.filename or "").lower()
    if not (name.endswith(".txt") or name.endswith(".md")):
        raise HTTPException(400, "Only .txt or .md files are supported")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 text")
    text = truncate_words(text.strip(), MAX_METADATA_WORDS)
    if not text:
        raise HTTPException(400, "File is empty")

    model_id = _config.embedding.s3vectors_model_id if _config else "amazon.titan-embed-text-v2:0"
    embedding = get_embedding(text, model_id)
    if not embedding:
        raise HTTPException(502, "Failed to generate embedding for the uploaded metadata")

    graph.write(queries.SET_DOCUMENT_METADATA_EMBEDDING, {
        "s3_key": s3_key,
        "embedding": embedding,
        "metadata_text": text,
    })
    return {"ok": True, "s3_key": s3_key, "words": len(text.split())}


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

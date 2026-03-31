"""Admin API routes — scanning, enrichment, and graph management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from src.config import SemanticLayerConfig
from src.discovery.enrichment import enrich_documents, enrich_tables
from src.discovery.glue_scanner import scan_databases
from src.discovery.s3vectors_scanner import scan_vector_buckets
from src.graph.client import GraphClient
from src.graph.loader import load_documents, load_metrics, load_structured
from src.metrics.loader import load_metrics as load_metrics_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_graph: GraphClient | None = None
_config: SemanticLayerConfig | None = None


def init(graph: GraphClient, config: SemanticLayerConfig) -> None:
    global _graph, _config
    _graph = graph
    _config = config


def _get_graph() -> GraphClient:
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    return _graph


@router.post("/scan")
async def scan_and_load():
    """Scan Glue catalogs + S3 Vector buckets and load into graph."""
    graph = _get_graph()
    summary = {"tables": 0, "columns": 0, "metrics": 0, "documents": 0, "joins": 0}

    # 1. Scan Glue databases
    if _config.databases:
        tables = scan_databases(_config.databases)
        summary["tables"] = len(tables)
        summary["columns"] = sum(len(t.columns) for t in tables)

        # Load metrics + join paths from YAML
        metrics, joins = load_metrics_yaml(_config.metrics_file)
        summary["joins"] = len(joins)

        # Load into graph
        load_structured(graph, tables, joins)
        load_metrics(graph, metrics)
        summary["metrics"] = len(metrics)

    # 2. Scan S3 Vector buckets
    if _config.vector_buckets:
        documents = scan_vector_buckets(_config.vector_buckets)
        load_documents(graph, documents)
        summary["documents"] = len(documents)

    logger.info("Scan complete: %s", summary)
    return {"status": "ok", "summary": summary}


@router.post("/enrich")
async def enrich_metadata():
    """Trigger LLM-based metadata enrichment for tables and documents."""
    graph = _get_graph()
    model_id = _config.bedrock.enrichment_model

    table_result = enrich_tables(graph, model_id)
    doc_result = enrich_documents(graph, model_id)

    return {
        "status": "ok",
        "tables": table_result,
        "documents": doc_result,
    }


@router.post("/clear")
async def clear_graph():
    """Clear all nodes and relationships from the graph. Use with caution."""
    graph = _get_graph()
    graph.write("MATCH (n) DETACH DELETE n")
    return {"status": "ok", "message": "Graph cleared"}


@router.get("/config")
async def get_config():
    """Return current configuration (sanitized)."""
    return {
        "databases": [{"name": db.name, "glue_database": db.glue_database} for db in _config.databases],
        "vector_buckets": [{"name": vb.name, "bucket": vb.bucket} for vb in _config.vector_buckets],
        "athena_workgroup": _config.athena.workgroup,
        "metrics_file": _config.metrics_file,
        "max_query_rows": _config.max_query_rows,
    }

"""Admin API routes — scanning, enrichment, and graph management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import SemanticLayerConfig
from src.discovery.enrichment import (
    enrich_documents,
    enrich_tables,
    get_job,
    list_jobs,
    start_enrichment,
)
from src.discovery.glue_scanner import discover_all_databases, scan_databases
from src.discovery.s3vectors_scanner import discover_all_vector_buckets, scan_vector_buckets
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

    # 1. Scan Glue databases (auto-discover all if none configured)
    if _config.databases:
        tables = scan_databases(_config.databases)
    else:
        tables = discover_all_databases()

    if tables:
        summary["tables"] = len(tables)
        summary["columns"] = sum(len(t.columns) for t in tables)

        # Load metrics + join paths from YAML (user-curated)
        metrics, joins = load_metrics_yaml(_config.metrics_file)
        summary["joins"] = len(joins)

        # Load into graph
        load_structured(graph, tables, joins)
        load_metrics(graph, metrics)
        summary["metrics"] = len(metrics)

    # 2. Scan S3 Vector buckets (auto-discover all if none configured)
    if _config.vector_buckets:
        documents = scan_vector_buckets(_config.vector_buckets)
    else:
        documents = discover_all_vector_buckets()

    if documents:
        load_documents(graph, documents)
        summary["documents"] = len(documents)
        summary["metadata_keys"] = sum(len(d.metadata_keys) for d in documents)

    logger.info("Scan complete: %s", summary)
    return {"status": "ok", "summary": summary}


class EnrichRequest(BaseModel):
    datasources: list[str] = Field(default_factory=list)
    force: bool = False
    model_id: str = ""  # empty = use default from config


@router.post("/enrich")
async def enrich_metadata(request: EnrichRequest | None = None):
    """Start async LLM enrichment. Returns a job ID for polling."""
    graph = _get_graph()
    req = request or EnrichRequest()
    model_id = req.model_id.strip() or _config.bedrock.enrichment_model

    job = start_enrichment(
        graph=graph,
        model_id=model_id,
        datasources=req.datasources if req.datasources else None,
        force=req.force,
    )

    return {"status": "started", "job_id": job.job_id}


@router.get("/enrich/{job_id}")
async def get_enrichment_status(job_id: str):
    """Poll enrichment job status."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return job.to_dict()


@router.get("/enrich/jobs/list")
async def list_enrichment_jobs():
    """List recent enrichment jobs."""
    return list_jobs()


@router.get("/datasources")
async def list_datasources():
    """List all datasources in the graph (for enrichment UI picker)."""
    graph = _get_graph()
    results = graph.query(
        "MATCH (ds:DataSource) "
        "OPTIONAL MATCH (ds)-[:CONTAINS]->(t:Table) "
        "RETURN ds.name AS name, count(t) AS table_count "
        "ORDER BY ds.name"
    )
    return results


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
        "enrichment_model": _config.bedrock.enrichment_model,
        "query_model": _config.bedrock.query_model,
    }

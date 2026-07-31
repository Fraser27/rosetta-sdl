"""Admin API routes — scanning, enrichment, and graph management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import SemanticLayerConfig
from src.constants import (
    AVAILABLE_EMBEDDING_MODELS,
    AVAILABLE_QUERY_MODELS,
    SYSTEM_CONFIG_KEY,
)
from src.graph import queries
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

        # Glue-scanned tables are queried via Athena — tag them with the
        # Athena executor datasource so metrics can filter tables by datasource.
        from src.executors.registry import registry
        load_structured(graph, tables, joins, datasource_id=registry.default_athena_id())
        load_metrics(graph, metrics, embedding_config=_config.embedding)
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


@router.get("/sample-data/status")
async def sample_data_status():
    """Check if sample data is loaded in the graph."""
    graph = _get_graph()
    result = graph.query(
        "OPTIONAL MATCH (ds:DataSource {name: 'ecommerce'}) "
        "OPTIONAL MATCH (m:Metric) WHERE m.source = 'sample' "
        "RETURN count(DISTINCT ds) AS datasources, count(DISTINCT m) AS metrics"
    )
    row = result[0] if result else {"datasources": 0, "metrics": 0}
    loaded = (row.get("datasources", 0) or 0) > 0 or (row.get("metrics", 0) or 0) > 0
    return {"loaded": loaded, "datasources": row.get("datasources", 0) or 0, "metrics": row.get("metrics", 0) or 0}


@router.post("/sample-data/load")
async def load_sample_data():
    """Load sample ecommerce data (seed cypher + metrics YAML) into the graph."""
    graph = _get_graph()
    from pathlib import Path

    # 1. Execute seed_graph.cypher
    cypher_path = Path("sample/seed_graph.cypher")
    if not cypher_path.exists():
        raise HTTPException(404, "sample/seed_graph.cypher not found")

    cypher_text = cypher_path.read_text()
    # Split by semicolons, strip comment lines from each block, execute non-empty ones
    executed = 0
    for raw_stmt in cypher_text.split(";"):
        lines = [l for l in raw_stmt.splitlines() if not l.strip().startswith("//")]
        clean = "\n".join(lines).strip()
        if clean:
            graph.write(clean)
            executed += 1

    # Tag seeded tables with the default Athena executor datasource so the
    # metric form can filter tables by datasource (sample data is Athena-backed).
    from src.executors.registry import registry
    default_ds = registry.default_athena_id()
    if default_ds:
        graph.write(
            "MATCH (t:Table) WHERE t.datasource_id IS NULL OR t.datasource_id = '' "
            "SET t.datasource_id = $ds "
            "WITH t MATCH (eds:DataSource {datasource_id: $ds}) "
            "MERGE (eds)-[:PROVIDES]->(t)",
            {"ds": default_ds},
        )

    # 2. Load sample metrics YAML
    metrics, joins = load_metrics_yaml("sample/metrics.yaml")
    if metrics:
        load_metrics(graph, metrics, embedding_config=_config.embedding)

    # 3. Load join paths from YAML (seed cypher already creates joins, but be safe)
    for jp in joins:
        graph.write(
            "MATCH (t1:Table {full_name: $source_table}), (t2:Table {full_name: $target_table}) "
            "MERGE (t1)-[:JOINS_TO {on_column: $on_column, join_type: $join_type}]->(t2)",
            {"source_table": jp.source_table, "target_table": jp.target_table,
             "on_column": jp.on_column, "join_type": jp.join_type},
        )

    return {"status": "ok", "message": f"Loaded sample data: {executed} cypher statements, {len(metrics)} metrics, {len(joins)} joins"}


@router.delete("/sample-data")
async def delete_sample_data():
    """Delete all sample/ecommerce data from the graph."""
    graph = _get_graph()

    # Delete sample metrics and their relationships
    graph.write("MATCH (m:Metric) WHERE m.source = 'sample' DETACH DELETE m")

    # Delete business terms that were linked only to sample metrics (orphaned)
    graph.write(
        "MATCH (bt:BusinessTerm) "
        "WHERE NOT exists((bt)-[:MAPS_TO]->(:Metric)) AND NOT exists((bt)-[:MAPS_TO]->(:Column)) "
        "DETACH DELETE bt"
    )

    # Delete ecommerce datasource and all its tables/columns
    graph.write(
        "MATCH (ds:DataSource {name: 'ecommerce'})-[:CONTAINS]->(t:Table) "
        "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column) "
        "DETACH DELETE c"
    )
    graph.write(
        "MATCH (ds:DataSource {name: 'ecommerce'})-[:CONTAINS]->(t:Table) "
        "DETACH DELETE t"
    )
    graph.write("MATCH (ds:DataSource {name: 'ecommerce'}) DETACH DELETE ds")

    # Delete YAML-sourced metrics too (from metrics.yaml)
    graph.write("MATCH (m:Metric) WHERE m.source = 'yaml' AND m.source_table STARTS WITH 'ecommerce.' DETACH DELETE m")

    return {"status": "ok", "message": "Sample data deleted"}


@router.get("/embedding-stats")
async def embedding_stats():
    """Get embedding coverage statistics for metrics."""
    graph = _get_graph()
    from src.graph import queries as q
    results = graph.query(q.EMBEDDING_STATS)
    row = results[0] if results else {"total": 0, "embedded": 0}
    return {
        "total": row.get("total", 0),
        "embedded": row.get("embedded", 0),
        "enabled": _config.embedding.enabled,
        "model_id": _config.embedding.model_id,
        "dimensions": _config.embedding.dimensions,
    }


@router.post("/reembed")
async def reembed_all():
    """Recompute all metric embeddings. Use after changing embedding model or enrichment."""
    graph = _get_graph()

    if not _config.embedding.enabled:
        return {"status": "skipped", "reason": "Embedding is disabled"}

    from src.graph import queries as q
    from src.query.embeddings import build_metric_embedding_text, get_embeddings_batch

    metrics = graph.query(q.LIST_METRICS)
    if not metrics:
        return {"status": "ok", "embedded": 0}

    texts = [
        build_metric_embedding_text(
            m["name"], m.get("definition", ""), m.get("synonyms", [])
        )
        for m in metrics
    ]
    embeddings = get_embeddings_batch(
        texts, _config.embedding.model_id, _config.embedding.dimensions
    )

    embedded_count = 0
    for m, embedding in zip(metrics, embeddings):
        if embedding:
            graph.write(q.SET_METRIC_EMBEDDING, {
                "metric_id": m["metric_id"],
                "embedding": embedding,
            })
            embedded_count += 1

    return {"status": "ok", "embedded": embedded_count, "total": len(metrics)}


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
        "embedding": {
            "enabled": _config.embedding.enabled,
            "model_id": _config.embedding.model_id,
            "dimensions": _config.embedding.dimensions,
            "fulltext_confidence_threshold": _config.embedding.fulltext_confidence_threshold,
            "vector_min_score": _config.embedding.vector_min_score,
        },
    }


@router.get("/config/query-model")
async def get_query_model():
    """Return the current ungoverned-query LLM model plus suggested options.

    `query_model` is free-text (any Converse-capable Bedrock modelId); `available`
    is a non-exhaustive suggestion list for the UI, not an allowlist.
    """
    return {
        "query_model": _config.bedrock.query_model,
        "available": AVAILABLE_QUERY_MODELS,
    }


class QueryModelUpdate(BaseModel):
    query_model: str


@router.put("/config/query-model")
async def set_query_model(req: QueryModelUpdate):
    """Set the LLM model used for ungoverned (LLM-generated) SQL.

    Free-text Bedrock modelId (invoked via Converse, so any provider works — the
    account just needs access to it). Persisted to Neo4j and applied in-memory
    immediately (config is shared by reference across routers).
    """
    model_id = req.query_model.strip()
    if not model_id:
        raise HTTPException(400, "query_model must not be empty")

    _get_graph().write(queries.UPSERT_SYSTEM_CONFIG, {
        "key": SYSTEM_CONFIG_KEY,
        "query_model": model_id,
    })
    _config.bedrock.query_model = model_id
    logger.info("Ungoverned query model set to %s", model_id)
    return {"ok": True, "query_model": model_id}


@router.get("/config/s3vectors-model")
async def get_s3vectors_model():
    """Return the current S3 Vectors search embedding model and the options."""
    return {
        "s3vectors_model": _config.embedding.s3vectors_model_id,
        "available": AVAILABLE_EMBEDDING_MODELS,
    }


class S3VectorsModelUpdate(BaseModel):
    s3vectors_model: str


@router.put("/config/s3vectors-model")
async def set_s3vectors_model(req: S3VectorsModelUpdate):
    """Set the embedding model used to embed queries before S3 Vectors search.

    Persists to Neo4j and mutates the shared in-memory config. Should match the
    model the vectors were ingested with, or similarity scores are meaningless.
    """
    model_id = req.s3vectors_model.strip()
    allowed_ids = {m["id"] for m in AVAILABLE_EMBEDDING_MODELS}
    if model_id not in allowed_ids:
        raise HTTPException(400, f"Unknown embedding model '{model_id}'. Choose one of the available models.")

    _get_graph().write(queries.UPSERT_S3VECTORS_EMBEDDING_MODEL, {
        "key": SYSTEM_CONFIG_KEY,
        "s3vectors_embedding_model": model_id,
    })
    _config.embedding.s3vectors_model_id = model_id
    logger.info("S3 Vectors embedding model set to %s", model_id)
    return {"ok": True, "s3vectors_model": model_id}


@router.get("/config/enrichment-model")
async def get_enrichment_model():
    """Return the current metadata-enrichment model (free-text Bedrock modelId)."""
    return {"enrichment_model": _config.bedrock.enrichment_model}


class EnrichmentModelUpdate(BaseModel):
    enrichment_model: str


@router.put("/config/enrichment-model")
async def set_enrichment_model(req: EnrichmentModelUpdate):
    """Set the Bedrock model used for LLM metadata enrichment (free-text modelId).

    Invoked via the Converse API, so any Converse-capable model works (Nova,
    Claude, etc.). Persisted to Neo4j and applied in-memory immediately.
    """
    model_id = req.enrichment_model.strip()
    if not model_id:
        raise HTTPException(400, "enrichment_model must not be empty")

    _get_graph().write(queries.UPSERT_ENRICHMENT_MODEL, {
        "key": SYSTEM_CONFIG_KEY,
        "enrichment_model": model_id,
    })
    _config.bedrock.enrichment_model = model_id
    logger.info("Enrichment model set to %s", model_id)
    return {"ok": True, "enrichment_model": model_id}

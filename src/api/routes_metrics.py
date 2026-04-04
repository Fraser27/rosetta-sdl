"""Metrics API routes — governed metric definitions and execution."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.catalog.models import MetricJoin, MetricSummary
from src.config import SemanticLayerConfig
from src.graph import queries
from src.graph.client import GraphClient
from src.metrics.compiler import FilterClause, compile_metric
from src.query.athena_executor import execute_query
from src.query.firewall import SQLFirewall

router = APIRouter(prefix="/metrics", tags=["metrics"])

_graph: GraphClient | None = None
_config: SemanticLayerConfig | None = None
_firewall: SQLFirewall | None = None


def init(graph: GraphClient, config: SemanticLayerConfig, firewall: SQLFirewall) -> None:
    global _graph, _config, _firewall
    _graph = graph
    _config = config
    _firewall = firewall


def _get_graph() -> GraphClient:
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    return _graph


class MetricCreateRequest(BaseModel):
    metric_id: str
    name: str
    definition: str = ""
    expression: str
    type: str = "simple"
    source_table: str = ""
    joins: list[MetricJoin] = Field(default_factory=list)
    base_metrics: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    time_grains: list[str] = Field(default_factory=list)
    source: str = "user"


class MetricQueryRequest(BaseModel):
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


def _parse_joins(raw: str | list | None) -> list[dict]:
    """Parse joins_json from Neo4j into a list of dicts."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return raw


@router.get("", response_model=list[MetricSummary])
async def list_metrics():
    """List all governed metrics."""
    results = _get_graph().query(queries.LIST_METRICS)
    for r in results:
        r["joins"] = _parse_joins(r.pop("joins_json", None))
    return [MetricSummary(**r) for r in results]


@router.get("/{metric_id}")
async def get_metric(metric_id: str):
    """Get full definition of a governed metric."""
    results = _get_graph().query(queries.GET_METRIC, {"metric_id": metric_id})
    if not results:
        raise HTTPException(404, f"Metric '{metric_id}' not found")
    result = results[0]
    result["joins"] = _parse_joins(result.pop("joins_json", None))
    return result


@router.post("/{metric_id}/query")
async def query_metric(metric_id: str, request: MetricQueryRequest):
    """Execute a governed metric with optional dimensions and filters."""
    graph = _get_graph()

    # Parse filters
    filter_clauses = [
        FilterClause(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in request.filters
    ]

    # Compile metric to SQL
    compiled = compile_metric(
        metric_id=metric_id,
        graph=graph,
        dimensions=request.dimensions,
        filters=filter_clauses,
        order_by=request.order_by,
        limit=request.limit or _config.max_query_rows,
    )

    if not compiled.is_valid:
        raise HTTPException(400, f"Compilation error: {'; '.join(compiled.errors)}")

    # Firewall check
    if _firewall:
        fw_result = _firewall.validate(compiled.sql)
        if not fw_result.allowed:
            raise HTTPException(403, fw_result.reason)

    # Execute via Athena
    result = execute_query(
        sql=compiled.sql,
        workgroup=request.workgroup or _config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        max_rows=request.limit or _config.max_query_rows,
    )

    return {
        "metric": compiled.metric_name,
        "sql": compiled.sql,
        "results": result,
    }


@router.post("/{metric_id}/compile")
async def compile_metric_endpoint(metric_id: str, request: MetricQueryRequest | None = None):
    """Compile a governed metric to SQL without executing it."""
    graph = _get_graph()
    req = request or MetricQueryRequest()

    filter_clauses = [
        FilterClause(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in req.filters
    ]

    compiled = compile_metric(
        metric_id=metric_id,
        graph=graph,
        dimensions=req.dimensions,
        filters=filter_clauses,
        order_by=req.order_by,
        limit=req.limit or _config.max_query_rows,
    )

    if not compiled.is_valid:
        raise HTTPException(400, f"Compilation error: {'; '.join(compiled.errors)}")

    return {
        "metric": compiled.metric_name,
        "sql": compiled.sql,
        "source_table": compiled.source_table,
    }


def _save_metric(graph: GraphClient, metric_id: str, req: MetricCreateRequest) -> None:
    """Shared logic for creating/updating a metric node and its relationships."""
    joins_json = json.dumps([j.model_dump() for j in req.joins]) if req.joins else "[]"
    graph.write(queries.MERGE_METRIC, {
        "metric_id": metric_id,
        "name": req.name,
        "definition": req.definition,
        "expression": req.expression,
        "type": req.type,
        "source_table": req.source_table,
        "synonyms": req.synonyms,
        "synonyms_text": " ".join(req.synonyms),
        "grain": req.grain,
        "filters": req.filters,
        "time_grains": req.time_grains,
        "joins_json": joins_json,
        "base_metrics": req.base_metrics,
        "source": req.source,
    })
    # Manage DERIVES_FROM relationships for derived metrics
    graph.write(queries.CLEAR_DERIVED_LINKS, {"metric_id": metric_id})
    if req.type == "derived" and req.base_metrics:
        for base_id in req.base_metrics:
            graph.write(queries.LINK_DERIVED_METRIC, {
                "derived_id": metric_id,
                "base_id": base_id,
            })


@router.post("")
async def create_metric(req: MetricCreateRequest):
    """Create a new governed metric in the graph."""
    graph = _get_graph()
    existing = graph.query(queries.GET_METRIC, {"metric_id": req.metric_id})
    if existing and existing[0].get("name"):
        raise HTTPException(409, f"Metric '{req.metric_id}' already exists")
    _save_metric(graph, req.metric_id, req)
    return {"ok": True, "metric_id": req.metric_id}


@router.put("/{metric_id}")
async def update_metric(metric_id: str, req: MetricCreateRequest):
    """Update an existing governed metric."""
    graph = _get_graph()
    _save_metric(graph, metric_id, req)
    return {"ok": True, "metric_id": metric_id}


@router.delete("/{metric_id}")
async def delete_metric(metric_id: str):
    """Delete a governed metric from the graph."""
    graph = _get_graph()
    existing = graph.query(queries.GET_METRIC, {"metric_id": metric_id})
    if not existing or not existing[0].get("name"):
        raise HTTPException(404, f"Metric '{metric_id}' not found")
    graph.write(queries.DELETE_METRIC, {"metric_id": metric_id})
    return {"ok": True}

"""Metrics API routes — governed metric definitions and execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from neo4j.exceptions import ConstraintError

from src.audit import record_mutation, record_query, user_from_request
from src.catalog.models import MetricJoin, MetricParameter, MetricSummary
from src.config import SemanticLayerConfig
from src.graph import queries
from src.graph.client import GraphClient
from src.executors.registry import registry
from src.metrics.compiler import FilterClause, compile_metric
from src.query.athena_executor import execute_query
from src.query.embeddings import build_metric_embedding_text, get_embedding
from src.query.firewall import SQLFirewall

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Tiny existence-check queries used only for input validation at this boundary.
# (Kept inline to avoid editing queries.py; see report note for follow-up.)
_EXISTS_DATASOURCE = (
    "MATCH (ds:DataSource {datasource_id: $datasource_id}) RETURN ds.datasource_id AS id LIMIT 1"
)
_EXISTS_TABLE = "MATCH (t:Table {full_name: $full_name}) RETURN t.full_name AS id LIMIT 1"
_EXISTS_METRIC = "MATCH (m:Metric {metric_id: $metric_id}) RETURN m.metric_id AS id LIMIT 1"

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


def _resolve_datasource_id_for_metric(metric_id: str, graph: GraphClient) -> str:
    """Resolve the datasource_id a metric executes on (via EXECUTES_ON), or '' if none."""
    results = graph.query(
        "MATCH (m:Metric {metric_id: $mid})-[:EXECUTES_ON]->(ds:DataSource) "
        "RETURN ds.datasource_id AS datasource_id",
        {"mid": metric_id},
    )
    if results and results[0].get("datasource_id"):
        return results[0]["datasource_id"]
    return ""


def _resolve_executor_for_metric(metric_id: str, graph: GraphClient):
    """Resolve the executor for a metric via its EXECUTES_ON relationship.

    Falls back to the default Athena executor when the metric has no datasource
    link. Returns None if no executor is registered (legacy Athena path used).
    """
    ds_id = _resolve_datasource_id_for_metric(metric_id, graph)
    if ds_id:
        return registry.get(ds_id)
    return registry.get(registry.default_athena_id())


class MetricCreateRequest(BaseModel):
    metric_id: str
    name: str
    definition: str = ""
    expression: str
    type: str = "simple"
    source_table: str = ""
    datasource_id: str = ""
    joins: list[MetricJoin] = Field(default_factory=list)
    base_metrics: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    parameters: list[MetricParameter] = Field(default_factory=list)
    time_grains: list[str] = Field(default_factory=list)
    time_grain_column: str = Field(default="", description="The date/timestamp column time_grains applies to; empty falls back to the first temporal column in grain")
    aggregation: str = "additive"  # additive | semi_additive | non_additive
    value_type: str = "number"  # number | currency | percent | ratio | count | duration
    unit: str = ""
    format: str = ""
    source: str = "user"


class MetricQueryRequest(BaseModel):
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    time_grain: str | None = Field(default=None, description="Roll the temporal dimension up to this grain (e.g. 'month'); must be one of the metric's declared time_grains")
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


def _parse_parameters(raw: str | list | None) -> list[dict]:
    """Parse parameters_json from Neo4j into a list of dicts."""
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
        r["parameters"] = _parse_parameters(r.pop("parameters_json", None))
    return [MetricSummary(**r) for r in results]


@router.get("/{metric_id}")
async def get_metric(metric_id: str):
    """Get full definition of a governed metric."""
    results = _get_graph().query(queries.GET_METRIC, {"metric_id": metric_id})
    if not results:
        raise HTTPException(404, f"Metric '{metric_id}' not found")
    result = results[0]
    result["joins"] = _parse_joins(result.pop("joins_json", None))
    result["parameters"] = _parse_parameters(result.pop("parameters_json", None))
    return result


@router.post("/{metric_id}/query")
async def query_metric(metric_id: str, request: MetricQueryRequest, http_request: Request):
    """Execute a governed metric with optional dimensions and filters."""
    graph = _get_graph()
    user = user_from_request(http_request)
    datasource_id = _resolve_datasource_id_for_metric(metric_id, graph)

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
        time_grain=request.time_grain,
    )

    if not compiled.is_valid:
        record_query(
            action="metric_query", user=user, query_type="governed",
            metric_id=metric_id, datasource_id=datasource_id,
            error="; ".join(compiled.errors),
        )
        raise HTTPException(400, f"Compilation error: {'; '.join(compiled.errors)}")

    # Firewall check
    if _firewall:
        fw_result = _firewall.validate(compiled.sql)
        if not fw_result.allowed:
            record_query(
                action="metric_query", user=user, query_type="governed",
                metric_id=metric_id, datasource_id=datasource_id, sql=compiled.sql,
                firewall_verdict="blocked", error=fw_result.reason,
            )
            raise HTTPException(403, fw_result.reason)

    # Execute on the metric's datasource engine (via EXECUTES_ON), falling back
    # to the legacy Athena executor when the metric has no datasource link.
    max_rows = request.limit or _config.max_query_rows
    executor = _resolve_executor_for_metric(metric_id, graph)
    if executor:
        exec_result = await executor.execute(compiled.sql, max_rows=max_rows)
        result = {
            "columns": exec_result.columns,
            "rows": exec_result.rows,
            "row_count": exec_result.row_count,
            "duration_ms": exec_result.duration_ms,
            "query_execution_id": exec_result.query_execution_id,
            "error": exec_result.error,
        }
    else:
        result = execute_query(
            sql=compiled.sql,
            workgroup=request.workgroup or _config.athena.workgroup,
            output_location=_config.athena.output_bucket,
            max_rows=max_rows,
        )

    record_query(
        action="metric_query", user=user, query_type="governed",
        metric_id=metric_id, datasource_id=datasource_id, sql=compiled.sql,
        firewall_verdict="allowed",
        row_count=int(result.get("row_count") or 0),
        duration_ms=int(result.get("duration_ms") or 0),
        error=str(result.get("error") or ""),
    )

    return {
        "metric": compiled.metric_name,
        "sql": compiled.sql,
        "results": result,
        "warnings": compiled.warnings,
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

    # Preview mode: no filters provided → show placeholders for declared parameters
    is_preview = len(filter_clauses) == 0

    compiled = compile_metric(
        metric_id=metric_id,
        graph=graph,
        dimensions=req.dimensions,
        filters=filter_clauses,
        order_by=req.order_by,
        limit=req.limit or _config.max_query_rows,
        preview=is_preview,
        time_grain=req.time_grain,
    )

    if not compiled.is_valid:
        raise HTTPException(400, f"Compilation error: {'; '.join(compiled.errors)}")

    return {
        "metric": compiled.metric_name,
        "sql": compiled.sql,
        "source_table": compiled.source_table,
        "warnings": compiled.warnings,
    }


def _exists(graph: GraphClient, query: str, params: dict) -> bool:
    """Existence check that fails CLOSED.

    Returns True only when the node is confirmed to exist. A graph error means we
    cannot confirm the reference is valid, so we must NOT let the write proceed —
    a transient Neo4j blip must not become a hole through which invalid references
    (bad datasource/table/base-metric ids) get persisted. Raises 503 on graph error.
    """
    try:
        return bool(graph.query(query, params))
    except Exception as e:
        logger.error("Existence check failed (%s): %s", query, e)
        raise HTTPException(503, "Could not validate references (graph unavailable)") from e


def _validate_references(graph: GraphClient, req: MetricCreateRequest) -> None:
    """Validate user-supplied references exist before persisting the metric.

    Raises HTTPException(400) with a clear message on the first failure.
    """
    # Aggregation class must be one of the known additivity types.
    if req.aggregation not in {"additive", "semi_additive", "non_additive"}:
        raise HTTPException(
            400,
            f"Invalid aggregation '{req.aggregation}' "
            f"(expected additive | semi_additive | non_additive)",
        )

    # Value type must be one of the known presentation classes.
    if req.value_type not in {"number", "currency", "percent", "ratio", "count", "duration"}:
        raise HTTPException(
            400,
            f"Invalid value_type '{req.value_type}' "
            f"(expected number | currency | percent | ratio | count | duration)",
        )

    # Datasource (EXECUTES_ON target) must exist when provided.
    if req.datasource_id and not _exists(
        graph, _EXISTS_DATASOURCE, {"datasource_id": req.datasource_id}
    ):
        raise HTTPException(400, f"Datasource '{req.datasource_id}' not found")

    # Simple metric: source_table (if given) must exist in the catalog.
    # Empty source_table for a simple metric is left to existing logic.
    if req.type == "simple" and req.source_table and not _exists(
        graph, _EXISTS_TABLE, {"full_name": req.source_table}
    ):
        raise HTTPException(400, f"Source table '{req.source_table}' not found in catalog")

    # Derived metric: every base metric id must exist.
    if req.type == "derived" and req.base_metrics:
        missing = [
            bid
            for bid in req.base_metrics
            if not _exists(graph, _EXISTS_METRIC, {"metric_id": bid})
        ]
        if missing:
            raise HTTPException(400, f"Base metric(s) not found: {', '.join(missing)}")

    # Join tables must exist in the catalog.
    missing_joins = [
        j.table
        for j in req.joins
        if j.table and not _exists(graph, _EXISTS_TABLE, {"full_name": j.table})
    ]
    if missing_joins:
        raise HTTPException(
            400, f"Join table(s) not found in catalog: {', '.join(missing_joins)}"
        )


def _embed_metric(metric_id: str, name: str, definition: str, synonyms: list[str]) -> None:
    """Compute + store a metric's embedding. Safe to run off the request path."""
    if not (_config and _config.embedding.enabled):
        return
    graph = _graph
    if graph is None:
        return
    try:
        text = build_metric_embedding_text(name, definition, synonyms)
        embedding = get_embedding(text, _config.embedding.model_id, _config.embedding.dimensions)
        if embedding:
            graph.write(queries.SET_METRIC_EMBEDDING, {"metric_id": metric_id, "embedding": embedding})
    except Exception as e:
        logger.warning("Failed to embed metric %s: %s", metric_id, e)


def _save_metric(
    graph: GraphClient,
    metric_id: str,
    req: MetricCreateRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    status: str = "draft",
    version: int = 1,
    updated_by: str = "",
) -> None:
    """Shared logic for creating/updating a metric node and its relationships."""
    _validate_references(graph, req)
    joins_json = json.dumps([j.model_dump() for j in req.joins]) if req.joins else "[]"
    parameters_json = json.dumps([p.model_dump() for p in req.parameters]) if req.parameters else "[]"
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
        "time_grain_column": req.time_grain_column,
        "aggregation": req.aggregation,
        "value_type": req.value_type,
        "unit": req.unit,
        "format": req.format,
        "status": status,
        "version": version,
        "updated_by": updated_by,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "joins_json": joins_json,
        "parameters_json": parameters_json,
        "base_metrics": req.base_metrics,
        "source": req.source,
    })
    # Manage EXECUTES_ON relationship (which datasource engine runs this metric).
    # No link = falls back to default Athena at query time.
    graph.write(queries.UNLINK_METRIC_FROM_DATASOURCE, {"metric_id": metric_id})
    if req.datasource_id:
        graph.write(queries.LINK_METRIC_TO_DATASOURCE, {
            "metric_id": metric_id,
            "datasource_id": req.datasource_id,
        })

    # Manage DERIVES_FROM relationships for derived metrics
    graph.write(queries.CLEAR_DERIVED_LINKS, {"metric_id": metric_id})
    if req.type == "derived" and req.base_metrics:
        for base_id in req.base_metrics:
            graph.write(queries.LINK_DERIVED_METRIC, {
                "derived_id": metric_id,
                "base_id": base_id,
            })

    # Compute and store embedding for vector search — off the request path when a
    # BackgroundTasks is supplied (create/update), so slow Bedrock calls don't block
    # the response. Falls back to synchronous when none provided.
    if _config and _config.embedding.enabled:
        if background_tasks is not None:
            background_tasks.add_task(
                _embed_metric, metric_id, req.name, req.definition, req.synonyms
            )
        else:
            _embed_metric(metric_id, req.name, req.definition, req.synonyms)


@router.post("")
async def create_metric(req: MetricCreateRequest, http_request: Request, background_tasks: BackgroundTasks):
    """Create a new governed metric in the graph."""
    graph = _get_graph()
    user = user_from_request(http_request)
    # Validate references BEFORE reserving the id, so a bad request doesn't leave
    # an empty node behind.
    _validate_references(graph, req)
    # Atomically reserve the metric_id via the unique constraint — closes the
    # check-then-write race where two concurrent creates could both pass a prior
    # existence check.
    try:
        graph.write(queries.CREATE_METRIC_NODE, {"metric_id": req.metric_id})
    except ConstraintError:
        raise HTTPException(409, f"Metric '{req.metric_id}' already exists")
    # New metrics start as draft v1 — must be explicitly approved before NL routing serves them.
    _save_metric(
        graph, req.metric_id, req, background_tasks=background_tasks,
        status="draft", version=1, updated_by=user,
    )
    record_mutation(action="metric_create", entity_type="metric", entity_id=req.metric_id, user=user)
    return {"ok": True, "metric_id": req.metric_id, "status": "draft", "version": 1}


@router.put("/{metric_id}")
async def update_metric(metric_id: str, req: MetricCreateRequest, http_request: Request, background_tasks: BackgroundTasks):
    """Update an existing governed metric.

    A definition change bumps the version and resets status to draft — an edited
    metric must be re-approved before NL routing will serve it again.
    """
    graph = _get_graph()
    user = user_from_request(http_request)
    gov = graph.query(queries.GET_METRIC_GOVERNANCE, {"metric_id": metric_id})
    prev_version = int(gov[0]["version"]) if gov else 0
    new_version = prev_version + 1
    # Snapshot the current state as a historical version BEFORE it's overwritten,
    # then prune to the last 10. No-op on a metric that doesn't exist yet.
    graph.write(queries.SNAPSHOT_METRIC_VERSION, {
        "metric_id": metric_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_metric(
        graph, metric_id, req, background_tasks=background_tasks,
        status="draft", version=new_version, updated_by=user,
    )
    record_mutation(action="metric_update", entity_type="metric", entity_id=metric_id, user=user)
    return {"ok": True, "metric_id": metric_id, "status": "draft", "version": new_version}


@router.get("/{metric_id}/versions")
async def list_metric_versions(metric_id: str):
    """List historical version snapshots of a metric (newest first, up to 10)."""
    return _get_graph().query(queries.LIST_METRIC_VERSIONS, {"metric_id": metric_id})


@router.get("/{metric_id}/versions/{version}")
async def get_metric_version(metric_id: str, version: int):
    """Get the full definition of one historical metric version."""
    rows = _get_graph().query(
        queries.GET_METRIC_VERSION, {"metric_id": metric_id, "version": version}
    )
    if not rows:
        raise HTTPException(404, f"Version {version} of metric '{metric_id}' not found")
    r = rows[0]
    r["joins"] = _parse_joins(r.pop("joins_json", None))
    r["parameters"] = _parse_parameters(r.pop("parameters_json", None))
    return r


@router.delete("/{metric_id}/versions/{version}")
async def delete_metric_version(metric_id: str, version: int, http_request: Request):
    """Delete a single historical version snapshot."""
    graph = _get_graph()
    user = user_from_request(http_request)
    rows = graph.query(queries.GET_METRIC_VERSION, {"metric_id": metric_id, "version": version})
    if not rows:
        raise HTTPException(404, f"Version {version} of metric '{metric_id}' not found")
    graph.write(queries.DELETE_METRIC_VERSION, {"metric_id": metric_id, "version": version})
    record_mutation(action="metric_version_delete", entity_type="metric", entity_id=metric_id, user=user)
    return {"ok": True}


@router.post("/{metric_id}/versions/{version}/restore")
async def restore_metric_version(metric_id: str, version: int, http_request: Request, background_tasks: BackgroundTasks):
    """Restore a historical version by re-applying its definition as a NEW version.

    The current state is first snapshotted (so a restore is itself reversible),
    then the old definition is written with a bumped version and draft status.
    """
    graph = _get_graph()
    user = user_from_request(http_request)
    rows = graph.query(queries.GET_METRIC_VERSION, {"metric_id": metric_id, "version": version})
    if not rows:
        raise HTTPException(404, f"Version {version} of metric '{metric_id}' not found")
    v = rows[0]

    gov = graph.query(queries.GET_METRIC_GOVERNANCE, {"metric_id": metric_id})
    new_version = (int(gov[0]["version"]) if gov else 0) + 1

    # Snapshot current state before overwriting (restore is reversible), prune to 10.
    graph.write(queries.SNAPSHOT_METRIC_VERSION, {
        "metric_id": metric_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    })

    req = MetricCreateRequest(
        metric_id=metric_id,
        name=v["name"],
        definition=v.get("definition") or "",
        expression=v.get("expression") or "",
        type=v.get("type") or "simple",
        source_table=v.get("source_table") or "",
        datasource_id=_resolve_datasource_id_for_metric(metric_id, graph),
        joins=[MetricJoin(**j) for j in _parse_joins(v.get("joins_json"))],
        base_metrics=v.get("base_metrics") or [],
        synonyms=v.get("synonyms") or [],
        grain=v.get("grain") or [],
        filters=v.get("filters") or [],
        parameters=[MetricParameter(**p) for p in _parse_parameters(v.get("parameters_json"))],
        time_grains=v.get("time_grains") or [],
        time_grain_column=v.get("time_grain_column") or "",
        aggregation=v.get("aggregation") or "additive",
        value_type=v.get("value_type") or "number",
        unit=v.get("unit") or "",
        format=v.get("format") or "",
        source=v.get("source") or "user",
    )
    _save_metric(
        graph, metric_id, req, background_tasks=background_tasks,
        status="draft", version=new_version, updated_by=user,
    )
    record_mutation(action="metric_version_restore", entity_type="metric", entity_id=metric_id, user=user)
    return {"ok": True, "metric_id": metric_id, "restored_from": version, "version": new_version, "status": "draft"}


class MetricStatusRequest(BaseModel):
    status: str  # approved | deprecated | draft


@router.post("/{metric_id}/status")
async def set_metric_status(metric_id: str, req: MetricStatusRequest, http_request: Request):
    """Transition a metric's lifecycle status (approve / deprecate / return to draft)."""
    graph = _get_graph()
    user = user_from_request(http_request)
    if req.status not in {"draft", "approved", "deprecated"}:
        raise HTTPException(400, f"Invalid status '{req.status}' (expected draft | approved | deprecated)")
    existing = graph.query(queries.GET_METRIC, {"metric_id": metric_id})
    if not existing or not existing[0].get("name"):
        raise HTTPException(404, f"Metric '{metric_id}' not found")
    result = graph.query(queries.SET_METRIC_STATUS, {
        "metric_id": metric_id,
        "status": req.status,
        "updated_by": user,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    record_mutation(
        action=f"metric_status_{req.status}", entity_type="metric",
        entity_id=metric_id, user=user,
    )
    return {"ok": True, "metric_id": metric_id, "status": req.status,
            "version": (result[0]["version"] if result else None)}


@router.delete("/{metric_id}")
async def delete_metric(metric_id: str, http_request: Request):
    """Delete a governed metric from the graph."""
    graph = _get_graph()
    user = user_from_request(http_request)
    existing = graph.query(queries.GET_METRIC, {"metric_id": metric_id})
    if not existing or not existing[0].get("name"):
        raise HTTPException(404, f"Metric '{metric_id}' not found")
    graph.write(queries.DELETE_METRIC, {"metric_id": metric_id})
    record_mutation(action="metric_delete", entity_type="metric", entity_id=metric_id, user=user)
    return {"ok": True}

"""Datasource management API routes — CRUD, test-connection, and health status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.executors.athena import AthenaExecutor
from src.executors.base import HealthStatus
from src.executors.redshift import RedshiftServerlessExecutor
from src.executors.registry import registry
from src.graph.client import GraphClient
from src.graph.queries import (
    CASCADE_DISABLE_METRICS,
    CASCADE_ENABLE_METRICS,
    DELETE_DATASOURCE,
    GET_DATASOURCE,
    GET_METRICS_FOR_DATASOURCE,
    LINK_METRIC_TO_DATASOURCE,
    LIST_DATASOURCES_FULL,
    SET_DATASOURCE_ENABLED,
    UPSERT_DATASOURCE_FULL,
)
from src.health.test_connection import get_job, start_test

logger = logging.getLogger(__name__)

# Sentinel disabled_reason for metrics turned off by a datasource-disable cascade,
# so re-enabling the datasource restores only these (not individually-disabled ones).
DATASOURCE_DISABLED_REASON = "datasource_disabled"

router = APIRouter(prefix="/datasources", tags=["datasources"])

_graph: GraphClient | None = None
_poller = None  # HealthPoller reference, set at init


def init(graph: GraphClient, poller=None) -> None:
    global _graph, _poller
    _graph = graph
    _poller = poller


def _get_graph() -> GraphClient:
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    return _graph


# -- Request/Response models --

class DataSourceRequest(BaseModel):
    name: str
    # Enum-validated at the API boundary: FastAPI returns 422 for unknown types
    # before any executor is created. Keys must match EXECUTOR_TYPES below.
    type: Literal["athena", "redshift_serverless"] = Field(
        description="Executor type: 'athena' or 'redshift_serverless'"
    )
    endpoint: str = Field(description="Workgroup name")
    database: str = ""
    region: str = "us-east-1"
    secret_arn: str | None = None
    output_location: str | None = Field(default=None, description="S3 output location (Athena only)")


class DataSourceResponse(BaseModel):
    datasource_id: str
    name: str
    type: str
    endpoint: str
    database: str
    region: str
    status: str
    enabled: bool
    metric_count: int = 0
    last_health_check: str | None = None
    created_at: str | None = None


# -- Executor factory --

EXECUTOR_TYPES = {
    "athena": AthenaExecutor,
    "redshift_serverless": RedshiftServerlessExecutor,
}


def _create_executor(datasource_id: str, name: str, ds_type: str, config: dict):
    """Create and register an executor instance."""
    cls = EXECUTOR_TYPES.get(ds_type)
    if cls is None:
        raise HTTPException(400, f"Unsupported datasource type: '{ds_type}'. Supported: {list(EXECUTOR_TYPES.keys())}")
    executor = cls(datasource_id=datasource_id, datasource_name=name, config=config)
    registry.register(datasource_id, executor)
    return executor


# -- Endpoints --

@router.get("")
async def list_datasources():
    """List all managed datasources with health status and metric counts."""
    graph = _get_graph()
    results = graph.query(LIST_DATASOURCES_FULL)
    datasources = []
    for r in results:
        # SECURITY: never surface r["secret_arn"] — it is intentionally omitted
        # from the response dict below (queries return it for internal use only).
        ds = {
            "datasource_id": r["datasource_id"],
            "name": r["name"],
            "type": r.get("type", "athena"),
            "endpoint": r.get("endpoint", ""),
            "database": r.get("database", ""),
            "region": r.get("region", "us-east-1"),
            "status": r.get("status", "unknown"),
            "enabled": r.get("enabled", True),
            "metric_count": r.get("metric_count", 0),
            "last_health_check": r.get("last_health_check"),
            "created_at": r.get("created_at"),
        }
        # Override with live cached status if poller is running
        if _poller:
            cached = _poller.get_status(r["datasource_id"])
            if cached != HealthStatus.UNKNOWN:
                ds["status"] = cached.value
        datasources.append(ds)
    return datasources


@router.post("")
async def create_datasource(request: DataSourceRequest):
    """Create a new datasource and register its executor."""
    graph = _get_graph()

    # Generate ID
    import uuid
    datasource_id = f"ds_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Persist to graph
    graph.write(UPSERT_DATASOURCE_FULL, {
        "datasource_id": datasource_id,
        "name": request.name,
        "type": request.type,
        "endpoint": request.endpoint,
        "database": request.database,
        "region": request.region,
        "secret_arn": request.secret_arn,
        "status": "unknown",
        "enabled": True,
        "created_at": now,
        "last_health_check": None,
    })

    # Create and register executor
    config = {
        "endpoint": request.endpoint,
        "database": request.database,
        "region": request.region,
        "secret_arn": request.secret_arn,
        "output_location": request.output_location or "",
    }
    _create_executor(datasource_id, request.name, request.type, config)

    return {
        "datasource_id": datasource_id,
        "name": request.name,
        "type": request.type,
        "endpoint": request.endpoint,
        "database": request.database,
        "region": request.region,
        "status": "unknown",
        "enabled": True,
        "created_at": now,
    }


@router.get("/{datasource_id}")
async def get_datasource(datasource_id: str):
    """Get datasource details."""
    graph = _get_graph()
    results = graph.query(GET_DATASOURCE, {"datasource_id": datasource_id})
    if not results:
        raise HTTPException(404, f"Datasource '{datasource_id}' not found")
    r = results[0]
    # SECURITY: never surface r["secret_arn"] — intentionally omitted from the
    # response dict below (the query returns it for internal use only).
    return {
        "datasource_id": r["datasource_id"],
        "name": r["name"],
        "type": r.get("type", "athena"),
        "endpoint": r.get("endpoint", ""),
        "database": r.get("database", ""),
        "region": r.get("region", "us-east-1"),
        "status": r.get("status", "unknown"),
        "enabled": r.get("enabled", True),
        "metric_count": r.get("metric_count", 0),
        "last_health_check": r.get("last_health_check"),
        "created_at": r.get("created_at"),
    }


@router.put("/{datasource_id}")
async def update_datasource(datasource_id: str, request: DataSourceRequest):
    """Update datasource configuration."""
    graph = _get_graph()

    # Verify exists
    results = graph.query(GET_DATASOURCE, {"datasource_id": datasource_id})
    if not results:
        raise HTTPException(404, f"Datasource '{datasource_id}' not found")

    now = datetime.now(timezone.utc).isoformat()
    # Preserve the stored secret when the client omits it or leaves it blank
    # (the secret is never returned to the client, so a blank field on edit
    # means "keep existing", not "clear it").
    if "secret_arn" in request.model_fields_set and request.secret_arn:
        secret_arn = request.secret_arn
    else:
        secret_arn = results[0].get("secret_arn")
    graph.write(UPSERT_DATASOURCE_FULL, {
        "datasource_id": datasource_id,
        "name": request.name,
        "type": request.type,
        "endpoint": request.endpoint,
        "database": request.database,
        "region": request.region,
        "secret_arn": secret_arn,
        "status": results[0].get("status", "unknown"),
        "enabled": results[0].get("enabled", True),
        "created_at": results[0].get("created_at", now),
        "last_health_check": results[0].get("last_health_check"),
    })

    # Re-register executor
    registry.remove(datasource_id)
    config = {
        "endpoint": request.endpoint,
        "database": request.database,
        "region": request.region,
        "secret_arn": secret_arn,
        "output_location": request.output_location or "",
    }
    _create_executor(datasource_id, request.name, request.type, config)

    return {"ok": True, "datasource_id": datasource_id}


class EnabledUpdate(BaseModel):
    enabled: bool


@router.patch("/{datasource_id}/enabled")
async def set_datasource_enabled(datasource_id: str, req: EnabledUpdate):
    """Enable or disable a datasource, cascading to its metrics.

    Disabling turns off every currently-enabled metric on this datasource and
    tags them with a sentinel reason. Re-enabling restores ONLY those metrics
    (metrics disabled individually for other reasons stay off).
    """
    graph = _get_graph()
    existing = graph.query(GET_DATASOURCE, {"datasource_id": datasource_id})
    if not existing:
        raise HTTPException(404, f"Datasource '{datasource_id}' not found")

    graph.write(SET_DATASOURCE_ENABLED, {"datasource_id": datasource_id, "enabled": req.enabled})

    cascade_query = CASCADE_ENABLE_METRICS if req.enabled else CASCADE_DISABLE_METRICS
    rows = graph.query(cascade_query, {
        "datasource_id": datasource_id,
        "reason": DATASOURCE_DISABLED_REASON,
    })
    affected = rows[0]["affected"] if rows else 0
    logger.info(
        "Datasource %s %s — %d metric(s) %s",
        datasource_id, "enabled" if req.enabled else "disabled",
        affected, "restored" if req.enabled else "disabled",
    )
    return {"ok": True, "datasource_id": datasource_id, "enabled": req.enabled, "metrics_affected": affected}


@router.delete("/{datasource_id}")
async def delete_datasource(datasource_id: str):
    """Delete a datasource. Fails if metrics are bound to it."""
    graph = _get_graph()

    # Check for bound metrics
    metrics = graph.query(GET_METRICS_FOR_DATASOURCE, {"datasource_id": datasource_id})
    if metrics:
        raise HTTPException(
            409,
            f"Cannot delete — {len(metrics)} metric(s) bound to this datasource. "
            f"Unbind them first.",
        )

    # Remove from registry
    registry.remove(datasource_id)

    # Remove from graph
    graph.write(DELETE_DATASOURCE, {"datasource_id": datasource_id})
    return {"ok": True}


@router.get("/{datasource_id}/metrics")
async def list_datasource_metrics(datasource_id: str):
    """List metrics bound to this datasource."""
    graph = _get_graph()
    results = graph.query(GET_METRICS_FOR_DATASOURCE, {"datasource_id": datasource_id})
    return results


@router.post("/{datasource_id}/test")
async def trigger_test_connection(datasource_id: str):
    """Start an async connection test. Returns job_id for polling."""
    if datasource_id not in registry:
        raise HTTPException(404, f"No executor registered for datasource '{datasource_id}'")

    job_id = start_test(datasource_id)
    return {"job_id": job_id, "datasource_id": datasource_id}


@router.get("/{datasource_id}/test/{job_id}")
async def poll_test_connection(datasource_id: str, job_id: str):
    """Poll test-connection job status."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job '{job_id}' not found or expired")
    return job

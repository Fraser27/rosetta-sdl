"""Audit API — read-only access to the durable audit trail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.audit import query_events
from src.graph.client import GraphClient

router = APIRouter(prefix="/audit", tags=["audit"])

_graph: GraphClient | None = None


def init(graph: GraphClient) -> None:
    global _graph
    _graph = graph


@router.get("/events")
async def list_audit_events(
    category: str | None = Query(default=None, description="query | mutation"),
    user: str | None = Query(default=None),
    metric_id: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List audit events, most recent first, with optional filters + pagination."""
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    events = query_events(
        category=category,
        user=user,
        metric_id=metric_id,
        entity_id=entity_id,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events), "limit": limit, "offset": offset}

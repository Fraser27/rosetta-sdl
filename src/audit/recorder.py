"""Audit event recorder — writes immutable :AuditEvent nodes to Neo4j.

Design:
- Append-only: events are CREATEd, never updated or deleted by application code.
- Fail-safe for the caller: a recording failure must never break the request it
  is auditing, so write errors are logged and swallowed (the audit sink being
  down should not take down governed queries). The loud warning is the signal.
- Not sampled: every query/mutation is recorded (contrast with OTEL/Langfuse).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_graph: GraphClient | None = None


def init(graph: GraphClient) -> None:
    """Wire the audit recorder to the graph client (called at app startup)."""
    global _graph
    _graph = graph


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_from_request(request: Any) -> str:
    """Best-effort extraction of the acting user from a FastAPI Request.

    The auth middleware sets request.state.user_email; falls back to 'unknown'
    (e.g. local dev with ALLOW_INSECURE_NO_AUTH, where no identity is attached).
    """
    try:
        return getattr(request.state, "user_email", None) or "unknown"
    except Exception:  # pragma: no cover - defensive
        return "unknown"


# Event categories.
CATEGORY_QUERY = "query"
CATEGORY_MUTATION = "mutation"


@dataclass
class AuditEvent:
    """A single audit record. Stored 1:1 as an :AuditEvent node."""

    category: str  # query | mutation
    action: str  # e.g. "metric_query", "nl_query", "compose", "metric_create"
    user: str = "unknown"
    # Query context
    query_type: str = ""  # governed | ungoverned | document | ""
    metric_id: str = ""
    metric_version: int = 0  # which metric version compiled (0 = n/a / unversioned)
    datasource_id: str = ""
    sql: str = ""
    firewall_verdict: str = ""  # allowed | blocked | ""
    row_count: int = 0
    duration_ms: int = 0
    error: str = ""
    # Mutation context
    entity_type: str = ""  # metric | datasource
    entity_id: str = ""
    # Common
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)

    def to_params(self) -> dict[str, Any]:
        return asdict(self)


_CREATE_AUDIT_EVENT = """
CREATE (a:AuditEvent {
    event_id: $event_id,
    timestamp: $timestamp,
    category: $category,
    action: $action,
    user: $user,
    query_type: $query_type,
    metric_id: $metric_id,
    metric_version: $metric_version,
    datasource_id: $datasource_id,
    sql: $sql,
    firewall_verdict: $firewall_verdict,
    row_count: $row_count,
    duration_ms: $duration_ms,
    error: $error,
    entity_type: $entity_type,
    entity_id: $entity_id
})
"""


def record_event(event: AuditEvent) -> None:
    """Persist an audit event. Never raises — logs and swallows on failure."""
    if _graph is None:
        logger.warning("Audit recorder not initialized; dropping event %s", event.action)
        return
    try:
        _graph.write(_CREATE_AUDIT_EVENT, event.to_params())
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to record audit event %s: %s", event.action, e)


def record_query(
    *,
    action: str,
    user: str = "unknown",
    query_type: str = "",
    metric_id: str = "",
    metric_version: int = 0,
    datasource_id: str = "",
    sql: str = "",
    firewall_verdict: str = "",
    row_count: int = 0,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    """Record an executed (or attempted) query."""
    record_event(
        AuditEvent(
            category=CATEGORY_QUERY,
            action=action,
            user=user,
            query_type=query_type,
            metric_id=metric_id,
            metric_version=metric_version,
            datasource_id=datasource_id,
            sql=sql,
            firewall_verdict=firewall_verdict,
            row_count=row_count,
            duration_ms=duration_ms,
            error=error,
        )
    )


def record_mutation(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    user: str = "unknown",
    error: str = "",
) -> None:
    """Record a create/update/delete of a governed entity (metric/datasource)."""
    record_event(
        AuditEvent(
            category=CATEGORY_MUTATION,
            action=action,
            user=user,
            entity_type=entity_type,
            entity_id=entity_id,
            error=error,
        )
    )


_QUERY_EVENTS = """
MATCH (a:AuditEvent)
WHERE ($category IS NULL OR a.category = $category)
  AND ($user IS NULL OR a.user = $user)
  AND ($metric_id IS NULL OR a.metric_id = $metric_id)
  AND ($entity_id IS NULL OR a.entity_id = $entity_id)
RETURN a.event_id AS event_id, a.timestamp AS timestamp, a.category AS category,
       a.action AS action, a.user AS user, a.query_type AS query_type,
       a.metric_id AS metric_id, a.metric_version AS metric_version,
       a.datasource_id AS datasource_id, a.sql AS sql,
       a.firewall_verdict AS firewall_verdict, a.row_count AS row_count,
       a.duration_ms AS duration_ms, a.error AS error,
       a.entity_type AS entity_type, a.entity_id AS entity_id
ORDER BY a.timestamp DESC
SKIP $offset LIMIT $limit
"""


def query_events(
    *,
    category: str | None = None,
    user: str | None = None,
    metric_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Read back audit events, most recent first, with optional filters + paging."""
    if _graph is None:
        return []
    return _graph.query(
        _QUERY_EVENTS,
        {
            "category": category,
            "user": user,
            "metric_id": metric_id,
            "entity_id": entity_id,
            "limit": max(1, min(limit, 1000)),
            "offset": max(0, offset),
        },
    )

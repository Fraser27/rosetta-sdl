"""Durable, append-only audit trail for the governed semantic layer.

Every executed query and every metric/datasource mutation is recorded as an
immutable :AuditEvent node in Neo4j. This is the compliance record — distinct
from operational observability (OTEL/Langfuse): it is never sampled and is
queryable ("show every query user X ran against metric Y").
"""

from src.audit.recorder import (
    AuditEvent,
    init as init_audit,
    record_event,
    record_mutation,
    record_query,
    query_events,
    user_from_request,
)

__all__ = [
    "AuditEvent",
    "init_audit",
    "record_event",
    "record_mutation",
    "record_query",
    "query_events",
    "user_from_request",
]

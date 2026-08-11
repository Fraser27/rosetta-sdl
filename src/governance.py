"""Governance controls — the ungoverned-query kill switch and its block history.

When `block_ungoverned_queries` is on, a question that matches no approved
governed metric is refused rather than answered with LLM-generated SQL. Each
refusal is recorded as a :BlockedQuery node so admins can see what users were
asking — that list is the backlog of metrics worth governing.

Unlike :AuditEvent (append-only compliance record, kept forever), this is a
capped ring buffer: it exists to be read in a UI, not to be a legal record, so
each write trims back to the most recent BLOCKED_QUERY_HISTORY_LIMIT.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

BLOCKED_QUERY_HISTORY_LIMIT = 10

# CREATE then trim in one transaction. The aggregation (`WITH count(*)`) forces
# the CREATE to complete before the MATCH runs, so the new node is included in
# the ordering and the oldest overflow row is the one deleted.
_RECORD_BLOCKED_QUERY = """
CREATE (:BlockedQuery {
    event_id: $event_id,
    timestamp: $timestamp,
    user: $user,
    question: $question,
    route: $route,
    reason: $reason
})
WITH count(*) AS created
MATCH (b:BlockedQuery)
WITH b ORDER BY b.timestamp DESC, b.event_id DESC SKIP $keep
DETACH DELETE b
"""

_LIST_BLOCKED_QUERIES = """
MATCH (b:BlockedQuery)
RETURN b.event_id AS event_id, b.timestamp AS timestamp, b.user AS user,
       b.question AS question, b.route AS route, b.reason AS reason
ORDER BY b.timestamp DESC, b.event_id DESC
LIMIT $limit
"""

BLOCK_REASON = (
    "No approved governed metric matched this question and ungoverned "
    "(LLM-generated) SQL is blocked."
)


def record_blocked_query(
    graph: GraphClient,
    *,
    question: str,
    user: str = "unknown",
    route: str = "",
    reason: str = BLOCK_REASON,
) -> None:
    """Record a refused ungoverned query, trimming history to the cap.

    Never raises: failing to log a block must not change the block itself into a
    500, which would misreport a governance decision as a server fault.
    """
    try:
        graph.write(
            _RECORD_BLOCKED_QUERY,
            {
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": user,
                "question": question,
                "route": route,
                "reason": reason,
                "keep": BLOCKED_QUERY_HISTORY_LIMIT,
            },
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to record blocked query: %s", e)


def list_blocked_queries(
    graph: GraphClient, limit: int = BLOCKED_QUERY_HISTORY_LIMIT
) -> list[dict]:
    """Return the most recently blocked ungoverned queries, newest first."""
    return graph.query(
        _LIST_BLOCKED_QUERIES,
        {"limit": max(1, min(limit, BLOCKED_QUERY_HISTORY_LIMIT))},
    )

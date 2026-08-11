"""Tests for the ungoverned-query kill switch and its capped block history."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src import governance
from src.api import routes_query
from src.config import SemanticLayerConfig


@pytest.fixture
def config():
    """A config wired into routes_query, restored afterwards."""
    cfg = SemanticLayerConfig()
    cfg.metrics_file = ""
    previous = routes_query._config
    routes_query._config = cfg
    yield cfg
    routes_query._config = previous


def _no_metric_match():
    """Disambiguation result where nothing governed matched."""
    return SimpleNamespace(metrics=[], tables=[], join_paths=[])


@pytest.mark.asyncio
async def test_blocks_ungoverned_when_switch_on(config):
    config.block_ungoverned_queries = True
    graph = MagicMock()

    with patch.object(routes_query, "disambiguate", return_value=_no_metric_match()), \
         patch.object(routes_query, "generate_sql") as gen_sql, \
         patch.object(routes_query, "record_blocked_query") as rec:
        with pytest.raises(HTTPException) as exc:
            await routes_query._handle_structured(
                "what is our fraud rate?", SimpleNamespace(route="structured"),
                graph, user="alice@example.com",
            )

    assert exc.value.status_code == 403
    # The LLM must never be invoked for a blocked question — that is the point.
    gen_sql.assert_not_called()
    assert rec.call_args.kwargs["question"] == "what is our fraud rate?"
    assert rec.call_args.kwargs["user"] == "alice@example.com"


@pytest.mark.asyncio
async def test_allows_ungoverned_when_switch_off(config):
    config.block_ungoverned_queries = False
    graph = MagicMock()

    with patch.object(routes_query, "disambiguate", return_value=_no_metric_match()), \
         patch.object(routes_query, "generate_sql", return_value="SELECT 1") as gen_sql, \
         patch.object(routes_query, "_resolve_datasource_for_sql", return_value=None), \
         patch.object(routes_query, "_execute_ungoverned", return_value={"row_count": 1}), \
         patch.object(routes_query, "_unapproved_metric_hint", return_value=None), \
         patch.object(routes_query, "record_blocked_query") as rec:
        result = await routes_query._handle_structured(
            "what is our fraud rate?", SimpleNamespace(route="structured"), graph,
        )

    assert result["query_type"] == "ungoverned"
    gen_sql.assert_called_once()
    rec.assert_not_called()


@pytest.mark.asyncio
async def test_governed_metric_unaffected_by_block(config):
    """The switch must only gate the ungoverned fallback, never governed metrics."""
    config.block_ungoverned_queries = True
    graph = MagicMock()
    graph.query.return_value = [{"enabled": True, "reason": None}]
    compiled = SimpleNamespace(
        is_valid=True, sql="SELECT sum(x) FROM t", metric_name="revenue",
        errors=[], warnings=[],
    )

    with patch.object(
        routes_query, "disambiguate",
        return_value=SimpleNamespace(metrics=[{"metric_id": "m1", "name": "revenue"}],
                                     tables=[], join_paths=[]),
    ), \
         patch.object(routes_query, "compile_metric", return_value=compiled), \
         patch.object(routes_query, "_execute_on_datasource", return_value={"row_count": 1}), \
         patch.object(routes_query, "_resolve_datasource_id_for_metric", return_value="ds_a"):
        result = await routes_query._handle_structured(
            "total revenue", SimpleNamespace(route="structured"), graph,
        )

    assert result["query_type"] == "governed"


def test_record_blocked_query_trims_to_limit():
    graph = MagicMock()
    governance.record_blocked_query(graph, question="q", user="bob", route="structured")

    cypher, params = graph.write.call_args[0]
    assert "CREATE (:BlockedQuery" in cypher
    assert "DETACH DELETE b" in cypher
    assert params["keep"] == governance.BLOCKED_QUERY_HISTORY_LIMIT
    assert params["question"] == "q"
    assert params["user"] == "bob"
    assert params["event_id"] and params["timestamp"]


def test_record_blocked_query_never_raises():
    graph = MagicMock()
    graph.write.side_effect = RuntimeError("neo4j down")
    # A logging failure must not turn a governance decision into a 500.
    governance.record_blocked_query(graph, question="q")


def test_list_blocked_queries_caps_limit():
    graph = MagicMock()
    graph.query.return_value = []
    governance.list_blocked_queries(graph, limit=9999)
    _, params = graph.query.call_args[0]
    assert params["limit"] == governance.BLOCKED_QUERY_HISTORY_LIMIT

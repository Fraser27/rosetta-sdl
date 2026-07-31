"""Tests for the durable audit recorder."""

from unittest.mock import MagicMock

from src.audit import recorder


def _wire_graph():
    graph = MagicMock()
    recorder.init(graph)
    return graph


def test_record_query_writes_event():
    graph = _wire_graph()
    recorder.record_query(
        action="metric_query", user="alice@example.com", query_type="governed",
        metric_id="m_001", datasource_id="ds_default_athena", sql="SELECT 1",
        firewall_verdict="allowed", row_count=5, duration_ms=42,
    )
    assert graph.write.called
    _, params = graph.write.call_args[0]
    assert params["category"] == "query"
    assert params["action"] == "metric_query"
    assert params["user"] == "alice@example.com"
    assert params["metric_id"] == "m_001"
    assert params["row_count"] == 5
    assert params["event_id"]  # uuid generated
    assert params["timestamp"]  # iso timestamp generated


def test_record_mutation_writes_event():
    graph = _wire_graph()
    recorder.record_mutation(
        action="metric_create", entity_type="metric", entity_id="m_x", user="bob",
    )
    _, params = graph.write.call_args[0]
    assert params["category"] == "mutation"
    assert params["entity_type"] == "metric"
    assert params["entity_id"] == "m_x"
    assert params["user"] == "bob"


def test_record_never_raises_on_write_failure():
    graph = MagicMock()
    graph.write.side_effect = RuntimeError("neo4j down")
    recorder.init(graph)
    # Must not raise — auditing a request must never break the request.
    recorder.record_query(action="metric_query", user="x")


def test_user_from_request_reads_state():
    req = MagicMock()
    req.state.user_email = "carol@example.com"
    assert recorder.user_from_request(req) == "carol@example.com"


def test_user_from_request_defaults_unknown():
    class NoEmail:
        pass

    req = MagicMock()
    req.state = NoEmail()
    assert recorder.user_from_request(req) == "unknown"

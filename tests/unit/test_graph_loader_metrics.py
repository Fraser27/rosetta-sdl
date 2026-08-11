"""Tests for loading metric definitions into the graph.

Regression cover for a MERGE_METRIC/loader parameter mismatch: the Cypher gained
governance + semantics fields but this caller kept passing the old param set, so
Neo4j rejected every write with ParameterMissing. That broke any scan with a
METRICS_FILE configured, and went unnoticed because nothing exercised this path.
"""

import re
from unittest.mock import MagicMock

from src.catalog.models import MetricDefinition
from src.graph import queries
from src.graph.loader import load_metrics


def _cypher_params(cypher: str) -> set[str]:
    """Every $param referenced by a Cypher statement."""
    return set(re.findall(r"\$(\w+)", cypher))


def _metric(**overrides) -> MetricDefinition:
    base = {
        "metric_id": "m_001",
        "name": "total_revenue",
        "expression": "SUM(total_amount)",
        "source_table": "ecommerce.orders",
    }
    return MetricDefinition(**{**base, **overrides})


def _merge_metric_call(graph: MagicMock) -> dict:
    for call in graph.write.call_args_list:
        if call[0][0] is queries.MERGE_METRIC:
            return call[0][1]
    raise AssertionError("MERGE_METRIC was never written")


def _graph(governance: dict | None = None) -> MagicMock:
    graph = MagicMock()
    graph.query.return_value = [governance] if governance else []
    return graph


def test_supplies_every_param_merge_metric_declares():
    """The guard that would have caught the original bug."""
    graph = _graph()
    load_metrics(graph, [_metric()])

    params = _merge_metric_call(graph)
    required = _cypher_params(queries.MERGE_METRIC)
    assert not required - set(params), f"MERGE_METRIC params missing: {required - set(params)}"


def test_new_metric_defaults_to_approved():
    """NL routing only serves approved metrics — a YAML metric must be reachable."""
    graph = _graph()
    load_metrics(graph, [_metric()])

    params = _merge_metric_call(graph)
    assert params["status"] == "approved"
    assert params["version"] == 1
    assert params["updated_at"]


def test_rescan_preserves_existing_governance_state():
    """Re-running a scan must not resurrect a deprecated metric or reset its version."""
    graph = _graph({"status": "deprecated", "version": 7})
    load_metrics(graph, [_metric()])

    params = _merge_metric_call(graph)
    assert params["status"] == "deprecated"
    assert params["version"] == 7


def test_carries_semantics_fields_from_definition():
    graph = _graph()
    load_metrics(graph, [
        _metric(
            aggregation="non_additive", value_type="currency",
            unit="AUD/MWh", format="$#,##0.00", owner="analytics-team",
        )
    ])

    params = _merge_metric_call(graph)
    assert params["aggregation"] == "non_additive"
    assert params["value_type"] == "currency"
    assert params["unit"] == "AUD/MWh"
    assert params["format"] == "$#,##0.00"
    assert params["updated_by"] == "analytics-team"


def test_defaults_applied_when_definition_omits_semantics():
    graph = _graph()
    load_metrics(graph, [_metric()])

    params = _merge_metric_call(graph)
    assert params["aggregation"] == "additive"
    assert params["value_type"] == "number"
    assert params["unit"] == ""
    assert params["updated_by"] == "yaml"

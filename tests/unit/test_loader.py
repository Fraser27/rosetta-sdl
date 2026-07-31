"""Tests for the hardened YAML metric loader."""

import logging

import pytest

from src.metrics.loader import load_metrics

LOGGER_NAME = "src.metrics.loader"


def _write(tmp_path, text):
    path = tmp_path / "metrics.yaml"
    path.write_text(text)
    return str(path)


def test_valid_file_loads_all_entries(tmp_path):
    yaml_text = """
metrics:
  - metric_id: total_revenue
    name: Total Revenue
    expression: SUM(total_amount)
    source_table: ecommerce.orders
  - metric_id: order_count
    name: Order Count
    expression: COUNT(*)
    source_table: ecommerce.orders
    time_grains: [day, month]
    aggregation: additive
    value_type: count
"""
    metrics, joins = load_metrics(_write(tmp_path, yaml_text))

    assert len(metrics) == 2
    # Loader returns MetricDefinition objects, not dicts.
    assert metrics[0].__class__.__name__ == "MetricDefinition"
    assert {m.metric_id for m in metrics} == {"total_revenue", "order_count"}
    assert joins == []


def test_metric_without_optional_fields(tmp_path):
    """Metrics lacking the newer optional fields still load with defaults."""
    yaml_text = """
metrics:
  - metric_id: minimal
    name: Minimal
    expression: COUNT(*)
"""
    metrics, _ = load_metrics(_write(tmp_path, yaml_text))

    assert len(metrics) == 1
    assert metrics[0].aggregation == "additive"
    assert metrics[0].value_type == "number"


def test_invalid_entry_is_skipped(tmp_path, caplog):
    """A bad entry (missing required expression) is skipped; good ones load."""
    yaml_text = """
metrics:
  - metric_id: good_metric
    name: Good
    expression: SUM(x)
  - metric_id: bad_metric
    name: Bad
"""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        metrics, _ = load_metrics(_write(tmp_path, yaml_text))

    assert [m.metric_id for m in metrics] == ["good_metric"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("bad_metric" in r.getMessage() for r in warnings)


def test_duplicate_metric_ids_keeps_first(tmp_path, caplog):
    yaml_text = """
metrics:
  - metric_id: dup
    name: First
    expression: SUM(a)
  - metric_id: dup
    name: Second
    expression: SUM(b)
"""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        metrics, _ = load_metrics(_write(tmp_path, yaml_text))

    assert len(metrics) == 1
    assert metrics[0].name == "First"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("dup" in r.getMessage() for r in warnings)


def test_missing_file_warns_and_returns_empty(tmp_path, caplog):
    missing = str(tmp_path / "does_not_exist.yaml")
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        metrics, joins = load_metrics(missing)

    assert metrics == []
    assert joins == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not found" in r.getMessage().lower() for r in warnings)


def test_malformed_yaml_errors_and_returns_empty(tmp_path, caplog):
    # Unbalanced bracket -> yaml.YAMLError
    yaml_text = "metrics: [ - metric_id: x\n  name: broken\n"
    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        metrics, joins = load_metrics(_write(tmp_path, yaml_text))

    assert metrics == []
    assert joins == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR log for malformed YAML"
    assert any("malformed" in r.getMessage().lower() for r in errors)


def test_empty_metrics_file_returns_empty(tmp_path):
    metrics, joins = load_metrics(_write(tmp_path, "metrics: []\n"))
    assert metrics == []
    assert joins == []

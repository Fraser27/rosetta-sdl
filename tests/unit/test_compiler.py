"""Tests for the deterministic metric compiler."""

import json
from unittest.mock import MagicMock

import pytest

from src.metrics.compiler import CompilationResult, FilterClause, compile_metric, compile_sql


@pytest.fixture
def mock_graph():
    graph = MagicMock()

    def query_side_effect(cypher, params=None):
        # Metric fetch query
        if "Metric" in cypher and "HAS_COLUMN" not in cypher:
            return [{
                "expression": "SUM(total_amount)",
                "metric_filters": ["status != 'cancelled'"],
                "name": "total_revenue",
                "source_table": "ecommerce.orders",
                "table_name": "ecommerce.orders",
                "parameters_json": None,
            }]
        # Column fetch query (for dimension validation)
        if "HAS_COLUMN" in cypher:
            return [
                {"name": "order_id"},
                {"name": "order_date"},
                {"name": "total_amount"},
                {"name": "status"},
                {"name": "year"},
            ]
        return []

    graph.query.side_effect = query_side_effect
    return graph


@pytest.fixture
def mock_graph_with_params():
    """Graph returning a metric with declared parameters."""
    graph = MagicMock()

    def query_side_effect(cypher, params=None):
        if "Metric" in cypher:
            return [{
                "expression": "SUM(amount)",
                "metric_filters": [],
                "name": "customer_revenue",
                "source_table": "apache_iceberg.payments_sample",
                "table_name": "apache_iceberg.payments_sample",
                "grain": ["user_id"],
                "parameters_json": json.dumps([
                    {"column": "user_id", "operator": "=", "required": False},
                ]),
            }]
        if "HAS_COLUMN" in cypher:
            return [
                {"name": "user_id"},
                {"name": "amount"},
                {"name": "status"},
            ]
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestCompileMetric:
    def test_simple_metric_no_dimensions(self, mock_graph):
        result = compile_metric("m_001", mock_graph)
        assert result.is_valid
        assert "SUM(total_amount) AS total_revenue" in result.sql
        assert "FROM ecommerce.orders" in result.sql
        assert "status != 'cancelled'" in result.sql
        assert "GROUP BY" not in result.sql

    def test_metric_with_dimensions(self, mock_graph):
        result = compile_metric("m_001", mock_graph, dimensions=["order_date"])
        assert result.is_valid
        assert "order_date" in result.sql
        assert "GROUP BY order_date" in result.sql

    def test_metric_with_filters(self, mock_graph):
        filters = [FilterClause(column="year", operator="=", value="2025")]
        result = compile_metric("m_001", mock_graph, filters=filters)
        assert result.is_valid
        assert "year = '2025'" in result.sql

    def test_metric_with_limit(self, mock_graph):
        result = compile_metric("m_001", mock_graph, limit=10)
        assert result.is_valid
        assert "LIMIT 10" in result.sql

    def test_metric_not_found(self):
        graph = MagicMock()
        graph.query.return_value = []
        result = compile_metric("nonexistent", graph)
        assert not result.is_valid
        assert "not found" in result.errors[0]

    def test_metric_with_order_by(self, mock_graph):
        result = compile_metric("m_001", mock_graph, dimensions=["order_date"], order_by=["order_date DESC"])
        assert "ORDER BY order_date DESC" in result.sql


class TestParameterValidation:
    def test_filter_on_declared_param(self, mock_graph_with_params):
        """Filter on a declared parameter should work."""
        filters = [FilterClause(column="user_id", operator="=", value="user_a")]
        result = compile_metric("m_009", mock_graph_with_params, filters=filters)
        assert result.is_valid
        assert "user_id = 'user_a'" in result.sql

    def test_undeclared_param_rejected(self, mock_graph_with_params):
        """Filter on an undeclared column should be rejected."""
        filters = [FilterClause(column="status", operator="=", value="completed")]
        result = compile_metric("m_009", mock_graph_with_params, filters=filters)
        assert not result.is_valid
        assert "not allowed" in result.errors[0]

    def test_required_param_missing(self):
        """Required parameter must be provided."""
        graph = MagicMock()

        def query_side_effect(cypher, params=None):
            if "Metric" in cypher:
                return [{
                    "expression": "SUM(amount)",
                    "metric_filters": [],
                    "name": "customer_revenue",
                    "source_table": "apache_iceberg.payments_sample",
                    "table_name": "apache_iceberg.payments_sample",
                    "grain": ["user_id"],
                    "parameters_json": json.dumps([
                        {"column": "user_id", "operator": "=", "required": True},
                    ]),
                }]
            return []

        graph.query.side_effect = query_side_effect
        result = compile_metric("m_009", graph)
        assert not result.is_valid
        assert "Required" in result.errors[0]

    def test_no_params_backward_compat(self, mock_graph):
        """Metrics without parameters accept any filter (backward compatible)."""
        filters = [FilterClause(column="anything", operator="=", value="val")]
        result = compile_metric("m_001", mock_graph, filters=filters)
        assert result.is_valid
        assert "anything = 'val'" in result.sql

    def test_no_filter_with_params_ok(self, mock_graph_with_params):
        """Metric with optional params but no filters should work."""
        result = compile_metric("m_009", mock_graph_with_params)
        assert result.is_valid
        assert "WHERE" not in result.sql

    def test_preview_shows_placeholders(self, mock_graph_with_params):
        """Preview mode injects '?' placeholders for declared parameters."""
        result = compile_metric("m_009", mock_graph_with_params, preview=True)
        assert result.is_valid
        assert "user_id = '?'" in result.sql

    def test_preview_skips_required_check(self):
        """Preview mode doesn't fail on missing required parameters."""
        graph = MagicMock()

        def query_side_effect(cypher, params=None):
            if "Metric" in cypher:
                return [{
                    "expression": "SUM(amount)",
                    "metric_filters": [],
                    "name": "customer_revenue",
                    "source_table": "apache_iceberg.payments_sample",
                    "table_name": "apache_iceberg.payments_sample",
                    "grain": ["user_id"],
                    "parameters_json": json.dumps([
                        {"column": "user_id", "operator": "=", "required": True},
                    ]),
                }]
            if "HAS_COLUMN" in cypher:
                return [{"name": "user_id"}, {"name": "amount"}]
            return []

        graph.query.side_effect = query_side_effect
        result = compile_metric("m_009", graph, preview=True)
        assert result.is_valid
        assert "user_id = '?'" in result.sql

    def test_preview_with_explicit_filters_validates(self, mock_graph_with_params):
        """Preview mode with explicit filters still validates them normally."""
        filters = [FilterClause(column="status", operator="=", value="completed")]
        result = compile_metric("m_009", mock_graph_with_params, filters=filters, preview=True)
        assert not result.is_valid
        assert "not allowed" in result.errors[0]


class TestCompileSQL:
    def test_simple_select(self):
        result = compile_sql("ecommerce.orders", ["order_id", "total_amount"])
        assert result.is_valid
        assert "SELECT order_id, total_amount" in result.sql
        assert "FROM ecommerce.orders" in result.sql

    def test_with_group_by(self):
        result = compile_sql(
            "ecommerce.orders",
            ["status", "COUNT(*)"],
            group_by=["status"],
        )
        assert "GROUP BY status" in result.sql

    def test_with_filters_and_limit(self):
        filters = [FilterClause(column="status", operator="=", value="completed")]
        result = compile_sql("ecommerce.orders", ["*"], filters=filters, limit=50)
        assert "status = 'completed'" in result.sql
        assert "LIMIT 50" in result.sql

    def test_in_filter(self):
        filters = [FilterClause(column="status", operator="IN", value=["completed", "shipped"])]
        result = compile_sql("ecommerce.orders", ["*"], filters=filters)
        assert "IN ('completed', 'shipped')" in result.sql

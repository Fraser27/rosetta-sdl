"""Tests for the deterministic metric compiler."""

from unittest.mock import MagicMock

import pytest

from src.metrics.compiler import CompilationResult, FilterClause, compile_metric, compile_sql


@pytest.fixture
def mock_graph():
    graph = MagicMock()
    graph.query.return_value = [{
        "expression": "SUM(total_amount)",
        "metric_filters": ["status != 'cancelled'"],
        "name": "total_revenue",
        "source_table": "ecommerce.orders",
        "table_name": "ecommerce.orders",
    }]
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

    def test_metric_not_found(self, mock_graph):
        mock_graph.query.return_value = []
        result = compile_metric("nonexistent", mock_graph)
        assert not result.is_valid
        assert "not found" in result.errors[0]

    def test_metric_with_order_by(self, mock_graph):
        result = compile_metric("m_001", mock_graph, dimensions=["order_date"], order_by=["order_date DESC"])
        assert "ORDER BY order_date DESC" in result.sql


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

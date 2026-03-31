"""Tests for the graph-based disambiguator."""

from unittest.mock import MagicMock

from src.query.disambiguator import disambiguate


def _make_graph(metric_results=None, table_results=None, column_results=None, path_results=None):
    graph = MagicMock()
    call_count = [0]

    def mock_query(cypher, params=None):
        # Route based on which index is being queried
        if "metric_search" in cypher:
            return metric_results or []
        elif "table_search" in cypher:
            return table_results or []
        elif "column_search" in cypher:
            return column_results or []
        elif "shortestPath" in cypher:
            return path_results or []
        return []

    graph.query.side_effect = mock_query
    return graph


class TestDisambiguator:
    def test_finds_metric(self):
        graph = _make_graph(metric_results=[{
            "metric_id": "m_001",
            "name": "total_revenue",
            "expression": "SUM(total_amount)",
            "source_table": "ecommerce.orders",
            "score": 2.0,
        }])
        result = disambiguate("total revenue", graph)
        assert len(result.metrics) == 1
        assert result.metrics[0]["name"] == "total_revenue"
        assert "ecommerce.orders" in result.tables

    def test_finds_tables(self):
        graph = _make_graph(table_results=[{
            "full_name": "ecommerce.customers",
            "name": "customers",
            "score": 1.5,
        }])
        result = disambiguate("customers", graph)
        assert "ecommerce.customers" in result.tables

    def test_finds_columns(self):
        graph = _make_graph(column_results=[{
            "name": "total_amount",
            "table": "ecommerce.orders",
            "score": 1.0,
        }])
        result = disambiguate("total amount", graph)
        assert "ecommerce.orders" in result.tables
        assert "total_amount" in result.columns.get("ecommerce.orders", [])

    def test_finds_join_paths(self):
        graph = _make_graph(
            table_results=[
                {"full_name": "ecommerce.orders", "name": "orders", "score": 1.5},
                {"full_name": "ecommerce.customers", "name": "customers", "score": 1.0},
            ],
            path_results=[{
                "tables": ["ecommerce.orders", "ecommerce.customers"],
                "join_columns": ["customer_id"],
            }],
        )
        result = disambiguate("orders and customers", graph)
        assert len(result.join_paths) >= 1

    def test_confidence_from_metric(self):
        graph = _make_graph(metric_results=[{
            "metric_id": "m_001", "name": "revenue",
            "expression": "SUM(x)", "source_table": "t", "score": 1.8,
        }])
        result = disambiguate("revenue", graph)
        assert result.confidence == 1.8

    def test_empty_results(self):
        graph = _make_graph()
        result = disambiguate("nonsense query", graph)
        assert result.tables == []
        assert result.metrics == []
        assert result.confidence == 0.0

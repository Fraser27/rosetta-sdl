"""Tests for the graph-based query router."""

from unittest.mock import MagicMock, patch

from src.query.router import route_query


def _make_graph(table_hits=None, metric_hits=None, doc_hits=None):
    """Create a mock graph that returns different results for different index queries."""
    graph = MagicMock()

    def mock_query(cypher, params=None):
        if "table_search" in cypher:
            return table_hits or []
        elif "metric_search" in cypher:
            return metric_hits or []
        elif "document_search" in cypher:
            return doc_hits or []
        return []

    graph.query.side_effect = mock_query
    return graph


class TestQueryRouter:
    def test_routes_to_structured_on_table_match(self):
        graph = _make_graph(
            table_hits=[{"type": "table", "id": "ecommerce.orders", "name": "orders", "description": "", "score": 1.5}],
        )
        result = route_query("show me orders", graph)
        assert result.route == "structured"
        assert "ecommerce.orders" in result.matched_tables

    def test_routes_to_structured_on_metric_match(self):
        graph = _make_graph(
            metric_hits=[{"type": "metric", "id": "m_001", "name": "total_revenue", "description": "", "score": 2.0}],
        )
        result = route_query("what is the total revenue", graph)
        assert result.route == "structured"
        assert "m_001" in result.matched_metrics

    def test_routes_to_unstructured_on_document_match(self):
        graph = _make_graph(
            doc_hits=[{"type": "document", "id": "s3://bucket/policy", "name": "return-policy", "description": "", "score": 1.0}],
        )
        result = route_query("return policy", graph)
        assert result.route == "unstructured"
        assert len(result.matched_documents) == 1

    def test_routes_to_both_when_mixed(self):
        graph = _make_graph(
            table_hits=[{"type": "table", "id": "ecommerce.returns", "name": "returns", "description": "", "score": 1.0}],
            doc_hits=[{"type": "document", "id": "s3://bucket/policy", "name": "return-policy", "description": "", "score": 1.0}],
        )
        result = route_query("returns and return policy", graph)
        assert result.route == "both"

    def test_defaults_to_structured_on_no_match(self):
        graph = _make_graph()
        result = route_query("something random", graph)
        assert result.route == "structured"

"""Tests for the graph-based disambiguator."""

from unittest.mock import MagicMock, patch

from src.config import EmbeddingConfig
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

    def test_metric_search_gated_to_approved(self):
        """NL routing must only surface approved metrics (governance gate)."""
        captured = []
        graph = MagicMock()

        def mock_query(cypher, params=None):
            if "metric_search" in cypher:
                captured.append(cypher)
            return []

        graph.query.side_effect = mock_query
        disambiguate("total revenue", graph)
        assert captured, "metric_search query was not issued"
        assert "COALESCE(node.status, 'approved') = 'approved'" in captured[0]


class TestWeakFulltextVeto:
    """A full-text hit below the confidence threshold is provisional: the vector
    search either confirms it, replaces it, or vetoes it into the ungoverned route."""

    @staticmethod
    def _weak_hit(score: float = 0.457) -> dict:
        return {
            "metric_id": "m_001",
            "name": "total_revenue",
            "expression": "SUM(x)",
            "source_table": "ecommerce.orders",
            "score": score,
        }

    def _graph(self, metric_hits, vector_hits):
        graph = MagicMock()

        def mock_query(cypher, params=None):
            if "vector.queryNodes" in cypher:
                return vector_hits
            if "metric_search" in cypher:
                return metric_hits
            return []

        graph.query.side_effect = mock_query
        return graph

    def test_weak_fulltext_with_no_vector_match_goes_ungoverned(self):
        # The reported bug: FT 0.457 < 1.1 and no vector hit above 0.77, yet the
        # weak hit was still served as a governed answer.
        graph = self._graph([self._weak_hit()], [])
        cfg = EmbeddingConfig(fulltext_confidence_threshold=1.1, vector_min_score=0.77)
        with patch("src.query.embeddings.get_embedding", return_value=[0.1] * 1024):
            result = disambiguate("whats the total customers", graph, embedding_config=cfg)
        assert result.metrics == []

    def test_vector_match_still_wins(self):
        strong_vec = self._weak_hit(0.81)
        graph = self._graph([self._weak_hit()], [strong_vec])
        cfg = EmbeddingConfig(fulltext_confidence_threshold=1.1, vector_min_score=0.77)
        with patch("src.query.embeddings.get_embedding", return_value=[0.1] * 1024):
            result = disambiguate("total revenue", graph, embedding_config=cfg)
        assert [h["score"] for h in result.metrics] == [0.81]

    def test_confident_fulltext_never_reaches_the_veto(self):
        confident = self._weak_hit(2.0)
        graph = self._graph([confident], [])
        cfg = EmbeddingConfig(fulltext_confidence_threshold=1.1, vector_min_score=0.77)
        result = disambiguate("total revenue", graph, embedding_config=cfg)
        assert [h["score"] for h in result.metrics] == [2.0]

    def test_embedding_failure_keeps_weak_hit(self):
        """An embedding outage must not silently flip governance to ungoverned."""
        graph = self._graph([self._weak_hit()], [])
        cfg = EmbeddingConfig(fulltext_confidence_threshold=1.1, vector_min_score=0.77)
        with patch("src.query.embeddings.get_embedding", return_value=[]):
            result = disambiguate("whats the total customers", graph, embedding_config=cfg)
        assert [h["score"] for h in result.metrics] == [0.457]

    def test_vector_search_disabled_keeps_weak_hit(self):
        graph = self._graph([self._weak_hit()], [])
        cfg = EmbeddingConfig(
            fulltext_confidence_threshold=1.1, vector_min_score=0.77, enabled=False
        )
        result = disambiguate("whats the total customers", graph, embedding_config=cfg)
        assert [h["score"] for h in result.metrics] == [0.457]


class TestMetricMatchThreshold:
    def _captured_params(self, embedding_config=None):
        captured = {}
        graph = MagicMock()

        def mock_query(cypher, params=None):
            if "metric_search" in cypher:
                captured.update(params or {})
            return []

        graph.query.side_effect = mock_query
        disambiguate("total revenue", graph, embedding_config=embedding_config)
        return captured

    def test_defaults_to_config_default(self):
        assert self._captured_params()["min"] == EmbeddingConfig.metric_match_min_score

    def test_uses_configured_threshold(self):
        cfg = EmbeddingConfig(metric_match_min_score=1.9, enabled=False)
        assert self._captured_params(cfg)["min"] == 1.9

    def test_threshold_is_parameterised_not_inlined(self):
        """A hardcoded floor would silently ignore the admin setting."""
        captured = []
        graph = MagicMock()

        def mock_query(cypher, params=None):
            if "metric_search" in cypher:
                captured.append(cypher)
            return []

        graph.query.side_effect = mock_query
        disambiguate("total revenue", graph, embedding_config=EmbeddingConfig(enabled=False))
        assert captured
        assert "score > $min" in captured[0]
        assert "score > 0.3" not in captured[0]

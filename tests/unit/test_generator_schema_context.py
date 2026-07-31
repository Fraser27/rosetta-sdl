"""Tests for deprecation precedence in the LLM schema context."""

from unittest.mock import MagicMock

from src.query.generator import _build_schema_context


def _graph_with_columns(rows):
    graph = MagicMock()
    graph.query.return_value = rows
    return graph


class TestSchemaContextDeprecation:
    def test_deprecated_column_shows_marker_not_description(self):
        graph = _graph_with_columns([
            {"name": "amount", "type": "double", "desc": "order total", "deprecated": False},
            {"name": "legacy_id", "type": "bigint", "desc": "old identifier", "deprecated": True},
        ])
        ctx = _build_schema_context(["db.orders"], graph)
        # Non-deprecated keeps its description.
        assert "amount (double) -- order total" in ctx
        # Deprecated column shows the avoid marker, and its own description is suppressed.
        assert "legacy_id (bigint) -- DEPRECATED: avoid using this column" in ctx
        assert "old identifier" not in ctx

    def test_plain_column_uses_description(self):
        graph = _graph_with_columns([
            {"name": "status", "type": "varchar", "desc": "order status", "deprecated": False},
        ])
        ctx = _build_schema_context(["db.orders"], graph)
        assert "status (varchar) -- order status" in ctx
        assert "DEPRECATED" not in ctx

"""Tests for the full-catalog schema context used to ground LLM SQL generation."""

import pytest
from unittest.mock import MagicMock

from src.query.generator import NoSchemaError, _build_schema_context


def _graph_with_columns(rows):
    graph = MagicMock()
    graph.query.return_value = rows
    return graph


class TestSchemaContextDeprecation:
    def test_deprecated_column_shows_marker_not_description(self):
        graph = _graph_with_columns([
            {"table": "db.orders", "name": "amount", "type": "double", "desc": "order total", "deprecated": False},
            {"table": "db.orders", "name": "legacy_id", "type": "bigint", "desc": "old identifier", "deprecated": True},
        ])
        ctx = _build_schema_context(graph)
        # Non-deprecated keeps its description.
        assert "amount (double) -- order total" in ctx
        # Deprecated column shows the avoid marker, and its own description is suppressed.
        assert "legacy_id (bigint) -- DEPRECATED: avoid using this column" in ctx
        assert "old identifier" not in ctx

    def test_plain_column_uses_description(self):
        graph = _graph_with_columns([
            {"table": "db.orders", "name": "status", "type": "varchar", "desc": "order status", "deprecated": False},
        ])
        ctx = _build_schema_context(graph)
        assert "status (varchar) -- order status" in ctx
        assert "DEPRECATED" not in ctx


class TestSchemaContextFullCatalog:
    def test_groups_columns_across_multiple_tables(self):
        graph = _graph_with_columns([
            {"table": "db.orders", "name": "id", "type": "bigint", "desc": "", "deprecated": False},
            {"table": "db.orders", "name": "amount", "type": "double", "desc": "", "deprecated": False},
            {"table": "db.customers", "name": "email", "type": "varchar", "desc": "", "deprecated": False},
        ])
        ctx = _build_schema_context(graph)
        assert "db.orders: id (bigint), amount (double)" in ctx
        assert "db.customers: email (varchar)" in ctx

    def test_empty_catalog_raises(self):
        graph = _graph_with_columns([])
        with pytest.raises(NoSchemaError):
            _build_schema_context(graph)

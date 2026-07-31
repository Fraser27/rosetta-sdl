"""Tests for B3: deterministic datasource binding of ungoverned SQL."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes_query import _resolve_datasource_for_sql, _tables_in_sql


def _graph_with_datasources(mapping):
    """mapping: table full_name -> datasource_id (or None for untagged)."""
    graph = MagicMock()

    def side_effect(cypher, params=None):
        fn = (params or {}).get("fn")
        if fn in mapping and mapping[fn]:
            return [{"ds": mapping[fn]}]
        return [{"ds": None}] if fn in mapping else []

    graph.query.side_effect = side_effect
    return graph


def test_tables_in_sql_extracts_qualified_names():
    sql = "SELECT * FROM ecommerce.orders o JOIN ecommerce.customers c ON o.cid = c.id"
    tables = _tables_in_sql(sql)
    assert "ecommerce.orders" in tables
    assert "ecommerce.customers" in tables


def test_tables_in_sql_excludes_cte_names():
    sql = "WITH rev AS (SELECT 1 FROM ecommerce.orders) SELECT * FROM rev"
    tables = _tables_in_sql(sql)
    assert "ecommerce.orders" in tables
    assert "rev" not in tables


def test_single_datasource_resolved():
    graph = _graph_with_datasources({
        "ecommerce.orders": "ds_redshift",
        "ecommerce.customers": "ds_redshift",
    })
    sql = "SELECT * FROM ecommerce.orders o JOIN ecommerce.customers c ON o.cid = c.id"
    assert _resolve_datasource_for_sql(sql, graph) == "ds_redshift"


def test_multiple_datasources_rejected():
    graph = _graph_with_datasources({
        "ecommerce.orders": "ds_redshift",
        "warehouse.sales": "ds_athena",
    })
    sql = "SELECT * FROM ecommerce.orders o JOIN warehouse.sales s ON o.id = s.id"
    with pytest.raises(HTTPException) as exc:
        _resolve_datasource_for_sql(sql, graph)
    assert exc.value.status_code == 400
    assert "multiple datasources" in exc.value.detail


def test_untagged_tables_return_none():
    # All referenced tables are untagged → None → caller uses Athena default.
    graph = _graph_with_datasources({"ecommerce.orders": None})
    sql = "SELECT * FROM ecommerce.orders"
    assert _resolve_datasource_for_sql(sql, graph) is None

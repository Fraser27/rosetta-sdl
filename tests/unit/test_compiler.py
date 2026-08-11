"""Tests for the deterministic metric compiler."""

import json
from unittest.mock import MagicMock

import pytest

from src.metrics.compiler import (
    CompilationResult,
    FilterClause,
    _IDENTIFIER_RE,
    _build_filter_clauses,
    _escape_sql_string,
    _validate_order_by,
    _validate_sql_expression,
    _validate_sql_predicate,
    compile_metric,
    compile_sql,
    compose_metrics,
)


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

    def test_no_params_filter_on_known_column_ok(self, mock_graph):
        """Metrics without declared parameters accept filters on real catalog columns."""
        filters = [FilterClause(column="status", operator="=", value="active")]
        result = compile_metric("m_001", mock_graph, filters=filters)
        assert result.is_valid
        assert "status = 'active'" in result.sql

    def test_no_params_filter_on_unknown_column_rejected(self, mock_graph):
        """Security: even without declared parameters, filters on unknown columns are rejected."""
        filters = [FilterClause(column="anything", operator="=", value="val")]
        result = compile_metric("m_001", mock_graph, filters=filters)
        assert not result.is_valid
        assert "not a known column" in result.errors[0]

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


# ---------------------------------------------------------------------------
# SQL-injection security fixes
# ---------------------------------------------------------------------------


class TestEscapeSqlString:
    def test_doubles_single_quotes(self):
        assert _escape_sql_string("x' OR '1'='1") == "x'' OR ''1''=''1"

    def test_no_quotes_unchanged(self):
        assert _escape_sql_string("completed") == "completed"

    def test_multiple_quotes(self):
        assert _escape_sql_string("''") == "''''"

    def test_scalar_hostile_value_is_inert_literal(self):
        """A hostile scalar value stays a single doubled-quote string literal."""
        filters = [FilterClause(column="status", operator="=", value="x' OR '1'='1")]
        clauses = _build_filter_clauses(filters)
        assert clauses == ["status = 'x'' OR ''1''=''1'"]

    def test_in_list_hostile_value_is_inert_literal(self):
        """Hostile values inside an IN list are each escaped into inert literals."""
        filters = [
            FilterClause(
                column="status",
                operator="IN",
                value=["x' OR '1'='1", "shipped"],
            )
        ]
        clauses = _build_filter_clauses(filters)
        assert clauses == ["status IN ('x'' OR ''1''=''1', 'shipped')"]


class TestValidateSqlExpression:
    @pytest.mark.parametrize(
        "expr",
        [
            "SUM(total_amount)",
            "SUM(total_amount) / COUNT(DISTINCT order_id)",
            "COUNT(DISTINCT customer_id)",
            "revenue - cost",
        ],
    )
    def test_safe_expressions(self, expr):
        assert _validate_sql_expression(expr) is True

    @pytest.mark.parametrize(
        "expr",
        [
            "SUM(x); DROP TABLE y",
            "(SELECT p FROM users)",
            "SUM(x) -- c",
            "1 UNION SELECT 1",
            "x /* */",
        ],
    )
    def test_unsafe_expressions(self, expr):
        assert _validate_sql_expression(expr) is False


class TestValidateSqlPredicate:
    @pytest.mark.parametrize(
        "pred",
        [
            "status != 'cancelled'",
            "amount > 100",
        ],
    )
    def test_safe_predicates(self, pred):
        assert _validate_sql_predicate(pred) is True

    @pytest.mark.parametrize(
        "pred",
        [
            "1=1; DROP",
            "x IN (SELECT id FROM users)",
            "status = 'x' -- comment",
        ],
    )
    def test_unsafe_predicates(self, pred):
        assert _validate_sql_predicate(pred) is False


class TestValidateOrderBy:
    def test_known_column(self):
        ok, bad = _validate_order_by(["order_date"], {"order_date"})
        assert ok is True
        assert bad == ""

    def test_known_column_with_direction(self):
        ok, bad = _validate_order_by(["order_date DESC"], {"order_date"})
        assert ok is True
        assert bad == ""

    def test_injection_entry_rejected(self):
        ok, bad = _validate_order_by(["1); DROP --"], {"order_date"})
        assert ok is False
        assert bad == "1); DROP --"

    def test_bad_direction_rejected(self):
        ok, bad = _validate_order_by(["col EVIL"], {"col"})
        assert ok is False
        assert bad == "col EVIL"

    def test_unknown_column_rejected(self):
        ok, bad = _validate_order_by(["mystery"], {"order_date"})
        assert ok is False
        assert bad == "mystery"


class TestIdentifierRe:
    @pytest.mark.parametrize("name", ["total_revenue", "_x", "a1", "Revenue"])
    def test_accepts_plain_identifiers(self, name):
        assert _IDENTIFIER_RE.match(name)

    @pytest.mark.parametrize("name", ["foo; bar", "foo bar", "1foo", "foo-bar", ""])
    def test_rejects_non_identifiers(self, name):
        assert not _IDENTIFIER_RE.match(name)


def _make_graph(metric, columns):
    """Build a MagicMock GraphClient in the same style as the module fixtures.

    `metric` is the dict returned for the metric-fetch query; `columns` is the
    iterable of column names returned for the HAS_COLUMN column-fetch query.
    """
    graph = MagicMock()

    def query_side_effect(cypher, params=None):
        if "Metric" in cypher and "HAS_COLUMN" not in cypher:
            return [metric]
        if "HAS_COLUMN" in cypher:
            return [{"name": c} for c in columns]
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestCompileMetricSecurity:
    def _valid_metric(self):
        return {
            "expression": "SUM(total_amount)",
            "metric_filters": ["status != 'cancelled'"],
            "name": "total_revenue",
            "source_table": "ecommerce.orders",
            "table_name": "ecommerce.orders",
            "parameters_json": None,
        }

    def test_happy_path_compiles(self):
        graph = _make_graph(self._valid_metric(), {"status", "order_date", "total_amount"})
        result = compile_metric("m_001", graph)
        assert result.is_valid
        assert "SUM(total_amount) AS total_revenue" in result.sql
        assert "FROM ecommerce.orders" in result.sql
        assert "status != 'cancelled'" in result.sql

    def test_filter_on_unknown_column_rejected(self):
        graph = _make_graph(self._valid_metric(), {"status", "order_date"})
        filters = [FilterClause(column="ssn", operator="=", value="123")]
        result = compile_metric("m_001", graph, filters=filters)
        assert not result.is_valid
        assert "not a known column" in result.errors[0]

    def test_known_column_filter_passes(self):
        graph = _make_graph(self._valid_metric(), {"status", "order_date"})
        filters = [FilterClause(column="status", operator="=", value="completed")]
        result = compile_metric("m_001", graph, filters=filters)
        assert result.is_valid
        assert "status = 'completed'" in result.sql

    def test_hostile_order_by_rejected(self):
        graph = _make_graph(self._valid_metric(), {"status", "order_date"})
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], order_by=["1); DROP TABLE orders --"]
        )
        assert not result.is_valid
        assert "Invalid order_by" in result.errors[0]

    def test_unsafe_stored_expression_rejected(self):
        metric = self._valid_metric()
        metric["expression"] = "(SELECT secret FROM admin.users)"
        graph = _make_graph(metric, {"status", "order_date"})
        result = compile_metric("m_001", graph)
        assert not result.is_valid
        assert "not a safe scalar SQL expression" in result.errors[0]

    def test_unsafe_stored_metric_filter_rejected(self):
        metric = self._valid_metric()
        metric["metric_filters"] = ["status = 'x'; DROP TABLE orders"]
        graph = _make_graph(metric, {"status", "order_date"})
        result = compile_metric("m_001", graph)
        assert not result.is_valid
        assert "not a safe predicate" in result.errors[0]

    def test_invalid_metric_name_rejected(self):
        metric = self._valid_metric()
        metric["name"] = "rev; DROP TABLE orders"
        graph = _make_graph(metric, {"status", "order_date"})
        result = compile_metric("m_001", graph)
        assert not result.is_valid
        assert "simple identifier" in result.errors[0]

    def test_hostile_filter_value_is_escaped_not_broken_out(self):
        """A hostile filter VALUE compiles but is neutralised via quote-doubling."""
        graph = _make_graph(self._valid_metric(), {"status", "order_date"})
        filters = [FilterClause(column="status", operator="=", value="x' OR '1'='1")]
        result = compile_metric("m_001", graph, filters=filters)
        assert result.is_valid
        # The value stays a single inert doubled-quote literal.
        assert "status = 'x'' OR ''1''=''1'" in result.sql
        # No broken-out injected clause slipped through.
        assert "OR '1'='1'" not in result.sql


# ---------------------------------------------------------------------------
# Cross-datasource validation (P1-3)
# ---------------------------------------------------------------------------


def _make_datasource_graph(metrics_by_id, columns, datasources):
    """Build a MagicMock GraphClient that answers the three query kinds used by
    the compiler:

      * metric fetch  — cypher contains "Metric" (but not "HAS_COLUMN")
                        → returns [metrics_by_id[params["id"]]] (or []).
      * column fetch  — cypher contains "HAS_COLUMN"
                        → returns [{"name": c}, ...] using `columns`, which may
                        be a flat iterable (same for every table) or a dict
                        keyed by table full_name (params["fn"]).
      * datasource    — cypher contains "datasource_id"
                        → returns [{"ds": datasources.get(params["fn"])}]; a
                        None value models an untagged table (permissive path).

    `datasources` maps table full_name -> datasource_id (or None). Tables absent
    from the map return no rows (also treated as untagged / permissive).
    """

    graph = MagicMock()

    def query_side_effect(cypher, params=None):
        params = params or {}
        if "datasource_id" in cypher:
            fn = params.get("fn")
            if fn in datasources:
                return [{"ds": datasources[fn]}]
            return []
        if "HAS_COLUMN" in cypher:
            if isinstance(columns, dict):
                cols = columns.get(params.get("fn"), [])
            else:
                cols = columns
            return [{"name": c} for c in cols]
        if "Metric" in cypher:
            m = metrics_by_id.get(params.get("id"))
            return [m] if m else []
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestCrossDatasourceJoin:
    def _metric_with_join(self):
        return {
            "metric_id": "m_join",
            "expression": "SUM(total_amount)",
            "metric_filters": [],
            "name": "total_revenue",
            "source_table": "ds_a.orders",
            "table_name": "ds_a.orders",
            "parameters_json": None,
            "joins_json": json.dumps([
                {
                    "table": "other.customers",
                    "source_column": "customer_id",
                    "target_column": "customer_id",
                    "join_type": "INNER",
                }
            ]),
        }

    def test_cross_datasource_join_rejected(self):
        graph = _make_datasource_graph(
            metrics_by_id={"m_join": self._metric_with_join()},
            columns=["total_amount", "customer_id"],
            datasources={"ds_a.orders": "ds_a", "other.customers": "ds_b"},
        )
        result = compile_metric("m_join", graph)
        assert not result.is_valid
        assert "Cross-datasource" in result.errors[0]

    def test_same_datasource_join_allowed(self):
        graph = _make_datasource_graph(
            metrics_by_id={"m_join": self._metric_with_join()},
            columns=["total_amount", "customer_id"],
            datasources={"ds_a.orders": "ds_a", "other.customers": "ds_a"},
        )
        result = compile_metric("m_join", graph)
        assert result.is_valid
        # No datasource error and the join is present in the SQL.
        assert not any("Cross-datasource" in e for e in result.errors)
        assert "JOIN other.customers" in result.sql

    def test_untagged_source_join_not_rejected(self):
        # Source table has no datasource_id (untagged) → permissive, skip the check.
        graph = _make_datasource_graph(
            metrics_by_id={"m_join": self._metric_with_join()},
            columns=["total_amount", "customer_id"],
            datasources={"ds_a.orders": None, "other.customers": "ds_b"},
        )
        result = compile_metric("m_join", graph)
        assert result.is_valid
        assert not any("Cross-datasource" in e for e in result.errors)


class TestCrossDatasourceComposition:
    def _base_metric(self, mid, name, table):
        return {
            "metric_id": mid,
            "expression": "SUM(total_amount)",
            "metric_filters": [],
            "name": name,
            "source_table": table,
            "table_name": table,
            "parameters_json": None,
            "joins_json": None,
        }

    def test_compose_cross_datasource_rejected(self):
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": self._base_metric("m_a", "revenue", "ds_a.orders"),
                "m_b": self._base_metric("m_b", "cost", "ds_b.expenses"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds_a.orders": "ds_a", "ds_b.expenses": "ds_b"},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert not result.is_valid
        assert "different datasources" in result.errors[0]

    def test_compose_same_datasource_allowed(self):
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": self._base_metric("m_a", "revenue", "ds_a.orders"),
                "m_b": self._base_metric("m_b", "cost", "ds_a.expenses"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds_a.orders": "ds_a", "ds_a.expenses": "ds_a"},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert result.is_valid
        assert not any("different datasources" in e for e in result.errors)
        assert "WITH revenue AS" in result.sql
        assert "cost AS" in result.sql

    def test_compose_untagged_base_not_rejected(self):
        # One base metric's table is untagged (None) → skipped, only one distinct ds.
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": self._base_metric("m_a", "revenue", "ds_a.orders"),
                "m_b": self._base_metric("m_b", "cost", "untagged.expenses"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds_a.orders": "ds_a", "untagged.expenses": None},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert result.is_valid
        assert not any("different datasources" in e for e in result.errors)


def _time_grain_graph(time_grains, col_types=None, time_grain_column="", grain=None):
    """Graph returning a revenue metric with an order_date DATE column."""
    col_types = col_types or {
        "order_id": "bigint",
        "order_date": "date",
        "total_amount": "double",
        "status": "varchar",
    }
    graph = MagicMock()

    def query_side_effect(cypher, params=None):
        if "Metric" in cypher and "HAS_COLUMN" not in cypher:
            return [{
                "expression": "SUM(total_amount)",
                "metric_filters": [],
                "name": "total_revenue",
                "source_table": "ecommerce.orders",
                "table_name": "ecommerce.orders",
                "grain": ["order_date"] if grain is None else grain,
                "time_grains": time_grains,
                "time_grain_column": time_grain_column,
                "parameters_json": None,
            }]
        if "data_type" in cypher:  # _fetch_column_types
            return [{"name": n, "data_type": t} for n, t in col_types.items()]
        if "HAS_COLUMN" in cypher:  # _fetch_table_columns / dimension validation
            return [{"name": n} for n in col_types]
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestTimeGrain:
    def test_month_bucketing_applied(self):
        graph = _time_grain_graph(["day", "month", "year"])
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="month"
        )
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('month', order_date) AS order_date" in result.sql
        # GROUP BY uses the bare expression, not the output alias.
        assert "GROUP BY DATE_TRUNC('month', order_date)" in result.sql

    def test_no_time_grain_defaults_to_coarsest_declared(self):
        # Declared time_grains are a restriction, so a caller that names no grain is
        # served at the coarsest declared one rather than the finer base grain.
        graph = _time_grain_graph(["day", "month"])
        result = compile_metric("m_001", graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('month', order_date) AS order_date" in result.sql
        assert "GROUP BY DATE_TRUNC('month', order_date)" in result.sql

    def test_no_declared_grains_leaves_dimension_raw(self):
        # With no declared time_grains there is nothing to enforce → base grain.
        graph = _time_grain_graph([])
        result = compile_metric("m_001", graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        assert "DATE_TRUNC" not in result.sql
        assert "GROUP BY order_date" in result.sql

    def test_undeclared_grain_rejected(self):
        graph = _time_grain_graph(["day", "month"])  # week not declared
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="week"
        )
        assert not result.is_valid
        assert "not allowed" in result.errors[0]

    def test_unsupported_grain_rejected(self):
        graph = _time_grain_graph(["day", "month", "decade"])
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="decade"
        )
        assert not result.is_valid
        assert "Unsupported time_grain" in result.errors[0]

    def test_no_temporal_dimension_rejected(self):
        # Only a non-temporal dimension present → no time axis to bucket.
        graph = _time_grain_graph(
            ["month"],
            col_types={"status": "varchar", "total_amount": "double"},
        )
        result = compile_metric(
            "m_001", graph, dimensions=["status"], time_grain="month"
        )
        assert not result.is_valid
        assert "No time axis available" in result.errors[0]

    def test_empty_declared_grains_allows_any_supported(self):
        # A metric with no declared time_grains permits any supported grain.
        graph = _time_grain_graph([])
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="year"
        )
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('year', order_date)" in result.sql

    def test_order_by_alias_after_bucketing(self):
        graph = _time_grain_graph(["month"])
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"],
            time_grain="month", order_by=["order_date"],
        )
        assert result.is_valid, result.errors
        assert "ORDER BY order_date" in result.sql


class TestTimeAxisGovernance:
    """A declared time axis is the ONLY column a caller may slice time by."""

    # Mirrors a Glue-partitioned lake table: string month/year partitions sitting
    # alongside the real date column, which is how the declared grain gets bypassed.
    _PARTITIONED = {
        "order_id": "bigint",
        "order_date": "date",
        "order_ts": "timestamp",
        "month": "string",
        "year": "string",
        "total_amount": "double",
        "status": "varchar",
    }

    def test_explicit_time_grain_column_is_the_axis(self):
        # order_ts comes first in dimensions, but the declared axis wins.
        graph = _time_grain_graph(
            ["year"], col_types=self._PARTITIONED, time_grain_column="order_date",
            grain=["order_date"],
        )
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="year"
        )
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('year', order_date)" in result.sql

    def test_time_grain_without_dimensions_adds_the_axis(self):
        """A bare time_grain must produce a time series, not a single total.

        Regression: the time-grain block was gated on `dimensions` being non-empty,
        so `time_grain` alone was silently dropped and the caller got the ungrouped
        aggregate — one row, no error, no indication the argument was ignored.
        """
        graph = _time_grain_graph(
            ["day", "month", "year"], time_grain_column="order_date", grain=[],
        )
        result = compile_metric("m_001", graph, time_grain="month")
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('month', order_date) AS order_date" in result.sql
        assert "GROUP BY DATE_TRUNC('month', order_date)" in result.sql

    def test_time_grain_appends_axis_alongside_other_dimensions(self):
        graph = _time_grain_graph(
            ["month"], time_grain_column="order_date", grain=[],
            col_types={"order_date": "date", "status": "varchar", "total_amount": "double"},
        )
        result = compile_metric(
            "m_001", graph, dimensions=["status"], time_grain="month"
        )
        assert result.is_valid, result.errors
        assert "status" in result.sql
        assert "DATE_TRUNC('month', order_date)" in result.sql

    def test_no_time_grain_and_no_dimensions_stays_ungrouped(self):
        """The axis is only auto-added for an *explicit* grain request."""
        graph = _time_grain_graph(
            ["day", "month"], time_grain_column="order_date", grain=[],
        )
        result = compile_metric("m_001", graph)
        assert result.is_valid, result.errors
        assert "GROUP BY" not in result.sql
        assert "DATE_TRUNC" not in result.sql

    def test_undeclared_time_grain_without_dimensions_refused(self):
        """Reaching the grain logic must not weaken its validation."""
        graph = _time_grain_graph(
            ["year"], time_grain_column="order_date", grain=[],
        )
        result = compile_metric("m_001", graph, time_grain="month")
        assert not result.is_valid
        assert any("not allowed" in e for e in result.errors)

    def test_calendar_partition_column_rejected(self):
        # GROUP BY month would report a year-only metric monthly.
        graph = _time_grain_graph(
            ["year"], col_types=self._PARTITIONED, time_grain_column="order_date",
        )
        result = compile_metric("m_001", graph, dimensions=["month"])
        assert not result.is_valid
        assert "not this metric's governed time axis" in result.errors[0]

    def test_other_timestamp_column_rejected(self):
        # A raw timestamp reintroduces per-second grain.
        graph = _time_grain_graph(
            ["year"], col_types=self._PARTITIONED, time_grain_column="order_date",
        )
        result = compile_metric("m_001", graph, dimensions=["order_ts"])
        assert not result.is_valid
        assert "not this metric's governed time axis" in result.errors[0]

    def test_non_temporal_dimension_still_allowed(self):
        # Governance targets time only — ordinary dimensions pass through.
        graph = _time_grain_graph(
            ["year"], col_types=self._PARTITIONED, time_grain_column="order_date",
        )
        result = compile_metric(
            "m_001", graph, dimensions=["order_date", "status"], time_grain="year"
        )
        assert result.is_valid, result.errors
        assert "DATE_TRUNC('year', order_date)" in result.sql
        assert "status" in result.sql

    def test_no_declared_grains_leaves_bypass_check_off(self):
        # Nothing declared → nothing to bypass; month grouping is legitimate.
        graph = _time_grain_graph([], col_types=self._PARTITIONED)
        result = compile_metric("m_001", graph, dimensions=["month"])
        assert result.is_valid, result.errors
        assert "GROUP BY month" in result.sql


def _aggregation_graph(aggregation, expression="SUM(total_amount)"):
    """Graph returning a metric with a given additivity class + date dimension."""
    graph = MagicMock()
    col_types = {"order_date": "date", "total_amount": "double", "status": "varchar"}

    def query_side_effect(cypher, params=None):
        if "Metric" in cypher and "HAS_COLUMN" not in cypher:
            return [{
                "expression": expression,
                "metric_filters": [],
                "name": "snapshot_metric",
                "source_table": "ecommerce.orders",
                "table_name": "ecommerce.orders",
                "grain": ["order_date"],
                "time_grains": ["day", "month"],
                "aggregation": aggregation,
                "parameters_json": None,
            }]
        if "data_type" in cypher:
            return [{"name": n, "data_type": t} for n, t in col_types.items()]
        if "HAS_COLUMN" in cypher:
            return [{"name": n} for n in col_types]
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestAggregationSemantics:
    def test_semi_additive_sum_over_time_rejected(self):
        graph = _aggregation_graph("semi_additive", "SUM(total_amount)")
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="month"
        )
        assert not result.is_valid
        assert "semi_additive" in result.errors[0]

    def test_semi_additive_without_time_grain_ok(self):
        # No time_grain requested → no cross-time rollup → allowed.
        graph = _aggregation_graph("semi_additive", "SUM(total_amount)")
        result = compile_metric("m_001", graph, dimensions=["order_date"])
        assert result.is_valid, result.errors

    def test_semi_additive_non_sum_over_time_ok(self):
        # A last-value / avg style expression is safe over time even if semi_additive.
        graph = _aggregation_graph("semi_additive", "AVG(total_amount)")
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="month"
        )
        assert result.is_valid, result.errors

    def test_additive_sum_over_time_ok(self):
        graph = _aggregation_graph("additive", "SUM(total_amount)")
        result = compile_metric(
            "m_001", graph, dimensions=["order_date"], time_grain="month"
        )
        assert result.is_valid, result.errors

    def test_compose_non_additive_warns(self):
        def base(mid, name, expr, agg):
            return {
                "metric_id": mid, "expression": expr, "metric_filters": [],
                "name": name, "source_table": "ds.orders", "table_name": "ds.orders",
                "parameters_json": None, "joins_json": None, "aggregation": agg,
            }
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": base("m_a", "revenue", "SUM(total_amount)", "additive"),
                "m_b": base("m_b", "aov", "AVG(total_amount)", "non_additive"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds.orders": "ds"},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        assert any("non_additive" in w for w in result.warnings)

    def test_compose_mixed_units_warns(self):
        def base(mid, name, unit):
            return {
                "metric_id": mid, "expression": "SUM(total_amount)", "metric_filters": [],
                "name": name, "source_table": "ds.orders", "table_name": "ds.orders",
                "parameters_json": None, "joins_json": None, "aggregation": "additive",
                "unit": unit,
            }
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": base("m_a", "revenue_usd", "USD"),
                "m_b": base("m_b", "revenue_eur", "EUR"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds.orders": "ds"},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        assert any("different units" in w for w in result.warnings)


def _fanout_graph(pk_by_table, expression="SUM(total_amount)"):
    """Graph for a metric that joins ecommerce.orders -> ecommerce.customers.

    pk_by_table maps table full_name -> set of PK column names (empty/absent = unknown).
    """
    graph = MagicMock()
    join = [{
        "table": "ecommerce.customers", "source_column": "customer_id",
        "target_column": "customer_id", "join_type": "LEFT",
    }]

    def query_side_effect(cypher, params=None):
        params = params or {}
        if "is_primary_key = true" in cypher:
            return [{"name": n} for n in pk_by_table.get(params.get("fn"), set())]
        if "Metric" in cypher and "HAS_COLUMN" not in cypher:
            return [{
                "expression": expression, "metric_filters": [], "name": "total_revenue",
                "source_table": "ecommerce.orders", "table_name": "ecommerce.orders",
                "joins_json": json.dumps(join), "parameters_json": None,
            }]
        if "HAS_COLUMN" in cypher:
            return [{"name": "customer_id"}, {"name": "total_amount"}, {"name": "region"}]
        return []

    graph.query.side_effect = query_side_effect
    return graph


class TestFanoutGuard:
    def test_fanout_warns_when_join_key_not_pk(self):
        # customers PK is customer_pk, but we join on customer_id → can fan out.
        graph = _fanout_graph({"ecommerce.customers": {"customer_pk"}})
        result = compile_metric("m_001", graph)
        assert result.is_valid, result.errors
        assert any("inflate" in w for w in result.warnings)

    def test_no_warn_when_join_key_is_pk(self):
        # Join key IS the PK → one-to-one, no fan-out.
        graph = _fanout_graph({"ecommerce.customers": {"customer_id"}})
        result = compile_metric("m_001", graph)
        assert result.is_valid
        assert not result.warnings

    def test_no_warn_when_pk_unknown(self):
        # No PK metadata on the target → stay silent (avoid false alarms).
        graph = _fanout_graph({"ecommerce.customers": set()})
        result = compile_metric("m_001", graph)
        assert result.is_valid
        assert not result.warnings

    def test_no_warn_for_count_distinct(self):
        # COUNT(DISTINCT) is immune to row duplication even with a fan-out join.
        graph = _fanout_graph(
            {"ecommerce.customers": {"customer_pk"}},
            expression="COUNT(DISTINCT order_id)",
        )
        result = compile_metric("m_001", graph)
        assert result.is_valid
        assert not result.warnings


class TestComposeJoinCorrectness:
    def _base(self, mid, name, table):
        return {
            "metric_id": mid, "expression": "SUM(total_amount)", "metric_filters": [],
            "name": name, "source_table": table, "table_name": table,
            "parameters_json": None, "joins_json": None,
        }

    def test_full_outer_join_and_coalesce(self):
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": self._base("m_a", "revenue", "ds.orders"),
                "m_b": self._base("m_b", "cost", "ds.orders"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds.orders": "ds"},
        )
        result = compose_metrics(["m_a", "m_b"], graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        # No CTE's rows should be dropped: FULL OUTER JOIN, dimension COALESCEd.
        assert "FULL OUTER JOIN" in result.sql
        assert "COALESCE(revenue.order_date, cost.order_date) AS order_date" in result.sql
        assert "LEFT JOIN" not in result.sql

    def test_three_way_compose_coalesces_all(self):
        graph = _make_datasource_graph(
            metrics_by_id={
                "m_a": self._base("m_a", "revenue", "ds.orders"),
                "m_b": self._base("m_b", "cost", "ds.orders"),
                "m_c": self._base("m_c", "tax", "ds.orders"),
            },
            columns=["total_amount", "order_date"],
            datasources={"ds.orders": "ds"},
        )
        result = compose_metrics(["m_a", "m_b", "m_c"], graph, dimensions=["order_date"])
        assert result.is_valid, result.errors
        assert result.sql.count("FULL OUTER JOIN") == 2
        # Third CTE joins against the COALESCE of the two preceding CTEs.
        assert "COALESCE(revenue.order_date, cost.order_date) = tax.order_date" in result.sql

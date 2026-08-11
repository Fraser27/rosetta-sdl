"""Deterministic metric compiler — generates SQL from metric definitions without LLM.

Pattern adapted from Fusion-main/agent/src/compiler.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_FUNC_RE = re.compile(r"^([A-Z_][A-Z_0-9]*)\s*\((.+)\)$", re.IGNORECASE)
_VALID_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "NOT IN", "BETWEEN"}
_VALID_JOIN_TYPES = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
# Time grains the compiler can bucket a temporal dimension into, mapped to the
# unit string Trino's DATE_TRUNC expects. Keys are what callers/metrics declare.
_TIME_GRAIN_UNITS = {
    "hour": "hour",
    "day": "day",
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
}
# Coarseness ranking, used to pick a default when a metric declares several grains.
_TIME_GRAIN_ORDER = {"hour": 0, "day": 1, "week": 2, "month": 3, "quarter": 4, "year": 5}
# Column data-type prefixes (lowercased) treated as temporal for bucketing.
_TEMPORAL_TYPE_PREFIXES = ("date", "timestamp", "time")
# Column names that denote a calendar part even when stored as a string/int
# (Glue partition columns like month='03'). These carry a time grain implicitly,
# so grouping by one sidesteps a metric's declared time_grains.
_CALENDAR_PART_NAMES = frozenset(_TIME_GRAIN_UNITS)
# Valid additivity classes for a metric.
_VALID_AGGREGATIONS = {"additive", "semi_additive", "non_additive"}


def _expr_is_sum(expression: str) -> bool:
    """True if the metric's top-level aggregate is a SUM (the additive form).

    Used to detect the semi-additive-over-time trap: SUM over a point-in-time
    snapshot bucketed by a coarser time grain double-counts.
    """
    return bool(re.match(r"^\s*SUM\s*\(", expression or "", re.IGNORECASE))


# Aggregates whose result inflates when source rows are duplicated by a fan-out join.
_FANOUT_SENSITIVE_RE = re.compile(r"\b(SUM|COUNT|AVG)\s*\(", re.IGNORECASE)


def _expr_is_fanout_sensitive(expression: str) -> bool:
    """True if the aggregate would be distorted by row duplication from a join.

    SUM/COUNT/AVG over a source measure all inflate when a one-to-many join
    multiplies source rows. COUNT(DISTINCT ...) is immune, so it's excluded.
    """
    expr = expression or ""
    if re.search(r"COUNT\s*\(\s*DISTINCT", expr, re.IGNORECASE):
        return False
    return bool(_FANOUT_SENSITIVE_RE.search(expr))


def _fetch_primary_keys(table: str, graph: GraphClient) -> set[str]:
    """Return the set of columns flagged as primary keys for a table.

    An empty set means either no PKs or (more commonly for Glue) no PK metadata
    at all — callers must treat "empty" as "unknown", not "no key".
    """
    rows = graph.query(
        "MATCH (t:Table {full_name: $fn})-[:HAS_COLUMN]->(c:Column) "
        "WHERE c.is_primary_key = true "
        "RETURN c.name AS name",
        {"fn": table},
    )
    return {r["name"] for r in rows}


def _detect_fanout_joins(
    expression: str,
    joins: list[MetricJoinDef],
    graph: GraphClient,
) -> list[str]:
    """Return join target tables that can fan out an additive measure.

    Only flags a join when we have POSITIVE evidence of risk: the target table
    has PK metadata defined AND the join's target_column is not among those PKs
    (so multiple target rows can match one source row, duplicating source rows).
    When a target has no PK metadata at all we stay silent to avoid false alarms.
    """
    if not _expr_is_fanout_sensitive(expression):
        return []
    risky: list[str] = []
    for j in joins:
        pks = _fetch_primary_keys(j.table, graph)
        if pks and j.target_column not in pks:
            risky.append(j.table)
    return risky
# Node types that make a fragment unsafe to inline: subqueries, set-ops, statement bodies.
_UNSAFE_EXPR_NODES = (exp.Select, exp.Subquery, exp.Union, exp.Except, exp.Intersect)


def _escape_sql_string(value: str) -> str:
    """Double single quotes so a value cannot break out of a quoted literal."""
    return value.replace("'", "''")


def _has_comment_or_separator(raw: str) -> bool:
    """Reject SQL fragments carrying comment markers or statement separators."""
    return ";" in raw or "--" in raw or "/*" in raw or "*/" in raw


def _validate_sql_expression(expr: str) -> bool:
    """True if `expr` is a single safe scalar SQL expression (e.g. SUM(total_amount)).

    Rejects statement separators, comments, multiple statements, and any
    embedded subquery/SELECT/set-operation.
    """
    if not expr or _has_comment_or_separator(expr):
        return False
    try:
        parsed = sqlglot.parse(expr, dialect="trino")
    except sqlglot.errors.ParseError:
        return False
    if len(parsed) != 1 or parsed[0] is None:
        return False
    node = parsed[0]
    # A bare scalar expression must not itself be a statement/SELECT.
    if isinstance(node, _UNSAFE_EXPR_NODES):
        return False
    return not any(node.find(t) for t in _UNSAFE_EXPR_NODES)


def _validate_sql_predicate(pred: str) -> bool:
    """True if `pred` is a single safe boolean predicate (e.g. status != 'cancelled')."""
    return _validate_sql_expression(pred)


def _validate_order_by(order_by: list[str], known: set[str]) -> tuple[bool, str]:
    """Each order_by entry must be a known name, optionally with trailing ASC/DESC."""
    for entry in order_by:
        parts = entry.split()
        col = parts[0]
        if len(parts) > 2 or (len(parts) == 2 and parts[1].upper() not in {"ASC", "DESC"}):
            return False, entry
        if col not in known:
            return False, entry
    return True, ""


@dataclass
class FilterClause:
    column: str
    operator: str
    value: str | int | float | list


@dataclass
class MetricJoinDef:
    table: str
    source_column: str
    target_column: str
    join_type: str = "INNER"


@dataclass
class CompilationResult:
    sql: str
    source_table: str
    metric_name: str | None = None
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _make_alias(table: str, used: set[str]) -> str:
    """Generate a short alias from the table name, avoiding collisions."""
    # Take the first char of the final dotted segment; fall back to "t" for
    # empty strings, trailing dots, or names without a usable segment.
    last_segment = table.split(".")[-1] if table else ""
    short = last_segment[0] if last_segment else "t"
    alias = short
    i = 2
    while alias in used:
        alias = f"{short}{i}"
        i += 1
    used.add(alias)
    return alias


def _parse_joins_json(raw: str | list | None) -> list[MetricJoinDef]:
    """Parse joins from JSON string or list."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [
        MetricJoinDef(
            table=j["table"],
            source_column=j["source_column"],
            target_column=j["target_column"],
            join_type=j.get("join_type", "INNER").upper(),
        )
        for j in raw
        if j.get("table") and j.get("source_column") and j.get("target_column")
    ]


def _fetch_table_columns(table: str, graph: GraphClient) -> set[str]:
    """Fetch the set of column names for a table from the graph."""
    results = graph.query(
        "MATCH (t:Table {full_name: $fn})-[:HAS_COLUMN]->(c:Column) "
        "RETURN c.name AS name",
        {"fn": table},
    )
    return {r["name"] for r in results}


def _fetch_column_types(table: str, graph: GraphClient) -> dict[str, str]:
    """Map column name -> data_type (lowercased) for a table from the graph."""
    results = graph.query(
        "MATCH (t:Table {full_name: $fn})-[:HAS_COLUMN]->(c:Column) "
        "RETURN c.name AS name, c.data_type AS data_type",
        {"fn": table},
    )
    return {r["name"]: (r.get("data_type") or "").lower() for r in results}


def _is_temporal_type(data_type: str) -> bool:
    """True if a column data type looks like a date/time type."""
    return data_type.startswith(_TEMPORAL_TYPE_PREFIXES)


def _resolve_time_axis(
    metric: dict, col_types: dict[str, str]
) -> str | None:
    """Resolve the metric's governed time axis — the one column time_grains applies to.

    Prefers the explicit time_grain_column; falls back to the first temporal column
    in the metric's grain so metrics authored before that field keep working.
    """
    declared = (metric.get("time_grain_column") or "").strip()
    if declared:
        return declared
    for d in metric.get("grain") or []:
        if _is_temporal_type(col_types.get(d, "")):
            return d
    return None


def _default_time_grain(declared_grains: list[str]) -> str | None:
    """Pick the grain to bucket to when a metric restricts grains but none was requested.

    Coarsest declared grain wins: it is the safest default, since a metric limited to
    ['year'] must never emit finer-grained rows just because the caller stayed silent.
    """
    valid = [g.lower() for g in declared_grains if g.lower() in _TIME_GRAIN_UNITS]
    if not valid:
        return None
    return max(valid, key=lambda g: _TIME_GRAIN_ORDER[g])


def _is_time_like(column: str, col_types: dict[str, str]) -> bool:
    """True if grouping by this column slices data along time.

    Covers both real date/timestamp columns and calendar-part partition columns
    (month, year, ...) that are typed as string/int but still carry a time grain.
    """
    return _is_temporal_type(col_types.get(column, "")) or column.lower() in _CALENDAR_PART_NAMES


def _check_time_axis_bypass(
    dimensions: list[str],
    time_axis: str | None,
    declared_grains: list[str],
    col_types: dict[str, str],
) -> str | None:
    """Reject time-based dimensions other than the metric's governed time axis.

    A metric that declares time_grains only controls DATE_TRUNC on its time axis.
    Grouping by any *other* time-like column (a raw timestamp, or a month/year
    partition string) reintroduces a finer grain and bypasses that restriction, so
    it is refused outright rather than silently answered at the wrong grain.
    """
    if not declared_grains:
        return None
    for d in dimensions:
        if d == time_axis or not _is_time_like(d, col_types):
            continue
        axis_hint = (
            f"group by '{time_axis}' with a time_grain from {sorted(declared_grains)} instead"
            if time_axis
            else f"this metric declares time_grains {sorted(declared_grains)} but no time axis column"
        )
        return (
            f"Dimension '{d}' groups by time but is not this metric's governed time axis "
            f"— that would bypass the declared time_grains {sorted(declared_grains)}; {axis_hint}."
        )
    return None


def _apply_time_grain(
    dimensions: list[str],
    time_grain: str | None,
    declared_grains: list[str],
    col_types: dict[str, str],
    time_axis: str | None,
) -> tuple[list[str], list[str], str | None]:
    """Rewrite the time axis to DATE_TRUNC(<unit>, col) when a grain applies.

    Returns (select_dims, group_dims, error).
    - select_dims: SELECT-list form; the bucketed dimension carries an `AS col` alias.
    - group_dims: GROUP BY form; the bucketed dimension is the bare DATE_TRUNC
      expression (Trino rejects GROUP BY on an output alias).
    - error is set (and dims returned unchanged) if the requested grain is invalid,
      not declared on the metric, or the time axis isn't among the dimensions.
    """
    if not time_grain:
        return dimensions, dimensions, None
    unit = _TIME_GRAIN_UNITS.get(time_grain.lower())
    if unit is None:
        return dimensions, dimensions, f"Unsupported time_grain '{time_grain}'"
    # A metric that declares time_grains restricts callers to that set.
    if declared_grains and time_grain.lower() not in {g.lower() for g in declared_grains}:
        return (
            dimensions,
            dimensions,
            f"time_grain '{time_grain}' not allowed; declared: {sorted(declared_grains)}",
        )
    if not time_axis:
        return dimensions, dimensions, (
            "No time axis available to apply time_grain — set the metric's "
            "time_grain_column, or include a date/timestamp column in its grain"
        )
    if time_axis not in dimensions:
        return dimensions, dimensions, (
            f"time_grain '{time_grain}' applies to time axis '{time_axis}', "
            f"which is not among the queried dimensions {dimensions}"
        )
    trunc = f"DATE_TRUNC('{unit}', {time_axis})"
    select_dims = [f"{trunc} AS {time_axis}" if d == time_axis else d for d in dimensions]
    group_dims = [trunc if d == time_axis else d for d in dimensions]
    return select_dims, group_dims, None


def _fetch_table_datasource(table: str, graph: GraphClient) -> str | None:
    """Fetch the owning datasource_id for a table from the graph, or None if untagged."""
    rows = graph.query(
        "MATCH (t:Table {full_name: $fn}) RETURN t.datasource_id AS ds",
        {"fn": table},
    )
    return rows[0]["ds"] if rows and rows[0].get("ds") else None


def _validate_dimensions(
    dimensions: list[str],
    table: str,
    joins: list[MetricJoinDef],
    graph: GraphClient,
) -> tuple[list[str], list[str]]:
    """Validate dimensions against actual table columns (source + joined tables).

    Returns (valid_dimensions, invalid_dimensions).
    """
    all_columns = _fetch_table_columns(table, graph)
    for j in joins:
        all_columns |= _fetch_table_columns(j.table, graph)
    if not all_columns:
        return dimensions, []
    valid = [d for d in dimensions if d in all_columns]
    invalid = [d for d in dimensions if d not in all_columns]
    return valid, invalid


@dataclass
class MetricParameterDef:
    column: str
    operator: str = "="
    required: bool = False
    description: str = ""


def _parse_parameters_json(raw: str | list | None) -> list[MetricParameterDef]:
    """Parse parameters from JSON string or list."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [
        MetricParameterDef(
            column=p["column"],
            operator=p.get("operator", "="),
            required=p.get("required", False),
            description=p.get("description", ""),
        )
        for p in raw
        if p.get("column")
    ]


def compile_metric(
    metric_id: str,
    graph: GraphClient,
    dimensions: list[str] | None = None,
    filters: list[FilterClause] | None = None,
    order_by: list[str] | None = None,
    limit: int | None = None,
    preview: bool = False,
    time_grain: str | None = None,
) -> CompilationResult:
    """Compile a governed metric into SQL by reading its definition from the graph.

    This is fully deterministic — no LLM involved.
    When preview=True, skips required-param validation and injects '?' placeholders.
    When time_grain is set, the temporal dimension is bucketed via DATE_TRUNC and
    validated against the metric's declared time_grains.
    """
    filters = filters or []

    # Fetch metric from graph
    metric = _fetch_metric_def(metric_id, graph)

    if not metric:
        return CompilationResult(
            sql="", source_table="", metric_name=None,
            is_valid=False, errors=[f"Metric '{metric_id}' not found"],
        )

    # Handle derived metrics — compose base metrics via CTEs
    if metric.get("type") == "derived":
        base_ids = metric.get("base_metrics") or []
        if not base_ids:
            return CompilationResult(
                sql="", source_table="", metric_name=metric.get("name"),
                is_valid=False, errors=[f"Derived metric '{metric_id}' has no base metrics"],
            )
        # Use the derived metric's grain as default dimensions
        derived_dims = dimensions or (metric.get("grain") or [])
        result = compose_metrics(
            metric_ids=base_ids, graph=graph, dimensions=derived_dims,
            filters=filters, order_by=order_by, limit=limit,
        )
        # Override the metric name with the derived metric's name
        if result.is_valid:
            result.metric_name = metric.get("name", metric_id)
            # If the derived metric has its own expression (e.g. "revenue - cost"),
            # wrap the composed query to apply it
            derived_expr = metric.get("expression", "")
            if derived_expr and len(base_ids) > 1:
                result = _wrap_derived_expression(result, metric, derived_dims, order_by, limit)
        return result

    table = metric.get("source_table") or metric.get("table_name", "")
    expression = metric.get("expression", "")
    name = metric.get("name", metric_id)
    metric_filters = metric.get("metric_filters") or []
    joins = _parse_joins_json(metric.get("joins_json"))
    parameters = _parse_parameters_json(metric.get("parameters_json"))

    # Validate the alias name is a plain identifier and the aggregate expression is safe.
    if not _IDENTIFIER_RE.match(str(name)):
        return CompilationResult(
            sql="", source_table=table, metric_name=str(name),
            is_valid=False, errors=[f"Invalid metric name (must be a simple identifier): '{name}'"],
        )
    if not _validate_sql_expression(expression):
        return CompilationResult(
            sql="", source_table=table, metric_name=name,
            is_valid=False, errors=[f"Metric expression is not a safe scalar SQL expression: '{expression}'"],
        )

    # Reject stored filters that aren't safe boolean predicates.
    for mf in metric_filters:
        if not _validate_sql_predicate(str(mf)):
            return CompilationResult(
                sql="", source_table=table, metric_name=name,
                is_valid=False, errors=[f"Stored metric filter is not a safe predicate: '{mf}'"],
            )

    # Reject cross-datasource joins: all joined tables must live on the source's datasource.
    # Permissive when unknown — skip the check for untagged tables (null datasource_id).
    if joins and table:
        source_ds = _fetch_table_datasource(table, graph)
        if source_ds:
            for j in joins:
                join_ds = _fetch_table_datasource(j.table, graph)
                if join_ds and join_ds != source_ds:
                    return CompilationResult(
                        sql="", source_table=table, metric_name=name,
                        is_valid=False,
                        errors=[
                            f"Cross-datasource join not allowed: table '{j.table}' "
                            f"(datasource '{join_ds}') cannot be joined to source '{table}' "
                            f"(datasource '{source_ds}'). Joins must stay within one datasource."
                        ],
                    )

    # Validate EVERY user filter column against the real catalog columns (source + joins).
    if filters and table:
        known_columns = _fetch_table_columns(table, graph)
        for j in joins:
            known_columns |= _fetch_table_columns(j.table, graph)
        if known_columns:
            for f in filters:
                if f.column not in known_columns:
                    return CompilationResult(
                        sql="", source_table=table, metric_name=name,
                        is_valid=False, errors=[f"Filter column '{f.column}' is not a known column"],
                    )

    # Validate filters against declared parameters
    if parameters:
        param_map = {p.column: p for p in parameters}
        if preview and not filters:
            # Preview mode: inject placeholder filters for all declared parameters
            filters = [FilterClause(column=p.column, operator=p.operator, value="?") for p in parameters]
        else:
            if filters:
                for f in filters:
                    if f.column not in param_map:
                        return CompilationResult(
                            sql="", source_table=table, metric_name=name,
                            is_valid=False,
                            errors=[f"Filter on '{f.column}' not allowed — declared parameters: {list(param_map.keys())}"],
                        )
            # Check required parameters are provided
            provided = {f.column for f in filters} if filters else set()
            missing = [p.column for p in parameters if p.required and p.column not in provided]
            if missing:
                return CompilationResult(
                    sql="", source_table=table, metric_name=name,
                    is_valid=False,
                    errors=[f"Required parameter(s) missing: {missing}"],
                )

    # Fall back to metric grain if no dimensions provided
    if not dimensions:
        dimensions = list(metric.get("grain") or [])
    else:
        dimensions = list(dimensions)

    # Validate dimensions against actual table columns
    if dimensions and table:
        valid_dims, invalid_dims = _validate_dimensions(dimensions, table, joins, graph)
        if invalid_dims:
            logger.warning(
                "Metric '%s': invalid dimensions %s not in table columns — dropping them",
                metric_id, invalid_dims,
            )
            dimensions = valid_dims

    # Output (alias) names stay the plain dimension columns even after bucketing,
    # so ORDER BY can reference them regardless of DATE_TRUNC rewriting.
    dimension_names = list(dimensions)
    group_dimensions = list(dimensions)
    # Apply time-grain bucketing (DATE_TRUNC) to the metric's governed time axis.
    declared = list(metric.get("time_grains") or [])
    # An explicit time_grain must be honoured even with no dimensions: asking for a
    # monthly series is asking to group by month. Previously this branch was skipped
    # whenever dimensions was empty, so the grain was dropped and the caller silently
    # received the single ungrouped aggregate instead of a time series.
    if table and (dimensions or time_grain):
        col_types = _fetch_column_types(table, graph)
        for j in joins:
            col_types |= _fetch_column_types(j.table, graph)
        time_axis = _resolve_time_axis(metric, col_types)

        # A requested grain implies grouping by the axis it applies to, so add it
        # rather than refusing over an argument the caller shouldn't need to repeat.
        # Only for an explicit request: the coarsest-grain default below must not
        # invent a time dimension the caller never asked to slice by.
        if time_grain and time_axis and time_axis not in dimensions:
            dimensions = [*dimensions, time_axis]
            dimension_names = list(dimensions)
            group_dimensions = list(dimensions)

        # Refuse dimensions that slice by time outside the governed axis.
        bypass_err = _check_time_axis_bypass(dimensions, time_axis, declared, col_types)
        if bypass_err:
            return CompilationResult(
                sql="", source_table=table, metric_name=name,
                is_valid=False, errors=[bypass_err],
            )

        # A metric that restricts grains is always served at one of them: when the
        # caller names no grain, fall back to the coarsest declared rather than
        # leaking the finer base grain. Skipped for semi-additive sums, where any
        # cross-time rollup is invalid — those stay at base grain instead of
        # auto-applying a grain the check below would then reject.
        aggregation = (metric.get("aggregation") or "additive").lower()
        semi_additive_sum = aggregation == "semi_additive" and _expr_is_sum(expression)
        effective_grain = time_grain
        if not effective_grain and declared and time_axis in dimensions and not semi_additive_sum:
            effective_grain = _default_time_grain(declared)

        select_dims, group_dims, tg_err = _apply_time_grain(
            dimensions, effective_grain, declared, col_types, time_axis
        )
        if tg_err:
            return CompilationResult(
                sql="", source_table=table, metric_name=name,
                is_valid=False, errors=[tg_err],
            )
        # Semi-additive measures (point-in-time snapshots) cannot be SUMmed across
        # time — bucketing a daily balance up to month double-counts. Reject it.
        if effective_grain and semi_additive_sum:
            return CompilationResult(
                sql="", source_table=table, metric_name=name,
                is_valid=False,
                errors=[
                    f"Metric '{name}' is semi_additive (a point-in-time snapshot) and "
                    f"cannot be summed across a time grain. Use a last-value / average "
                    f"aggregation over the period, or query at the base grain without time_grain."
                ],
            )
        dimensions = select_dims
        group_dimensions = group_dims

    # Build aliases: source table + joined tables
    used_aliases: set[str] = set()
    source_alias = _make_alias(table, used_aliases)

    # Table alias map: full_name -> alias
    alias_map: dict[str, str] = {table: source_alias}
    for j in joins:
        if j.table not in alias_map:
            alias_map[j.table] = _make_alias(j.table, used_aliases)

    # Build SELECT
    select_cols = list(dimensions)
    select_cols.append(f"{expression} AS {name}")

    # Build FROM + JOINs
    from_clause = f"{table} {source_alias}"
    join_clauses: list[str] = []
    for j in joins:
        jt = j.join_type if j.join_type in _VALID_JOIN_TYPES else "INNER"
        j_alias = alias_map[j.table]
        join_clauses.append(
            f"{jt} JOIN {j.table} {j_alias} "
            f"ON {source_alias}.{j.source_column} = {j_alias}.{j.target_column}"
        )

    # Validate order_by against known names (dimensions + metric output name).
    if order_by:
        known = set(dimension_names) | {name}
        ok, bad = _validate_order_by(order_by, known)
        if not ok:
            return CompilationResult(
                sql="", source_table=table, metric_name=name,
                is_valid=False, errors=[f"Invalid order_by entry: '{bad}'"],
            )

    sql = f"SELECT {', '.join(select_cols)}\nFROM {from_clause}"
    for jc in join_clauses:
        sql += f"\n{jc}"

    # Build WHERE
    where_parts = list(metric_filters)
    where_parts.extend(_build_filter_clauses(filters))
    if where_parts:
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    # GROUP BY (bare expressions — bucketed dimension is the DATE_TRUNC, not its alias)
    if group_dimensions:
        sql += f"\nGROUP BY {', '.join(group_dimensions)}"

    # ORDER BY
    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    elif dimension_names:
        sql += f"\nORDER BY {name} DESC"

    # LIMIT
    if limit:
        sql += f"\nLIMIT {limit}"

    # Warn (don't block) when a join can fan out an additive measure and inflate it.
    warnings: list[str] = []
    if joins:
        risky = _detect_fanout_joins(expression, joins, graph)
        if risky:
            warnings.append(
                f"Metric '{name}' aggregates over join(s) to {risky} whose join key is "
                f"not that table's primary key: a one-to-many match will duplicate source "
                f"rows and inflate the result. Verify the join is one-to-one, or pre-aggregate."
            )

    return CompilationResult(
        sql=sql, source_table=table, metric_name=name, warnings=warnings,
    )


def _fetch_metric_def(metric_id: str, graph: GraphClient) -> dict | None:
    """Fetch a single metric definition from the graph."""
    results = graph.query(
        "MATCH (m:Metric {metric_id: $id}) "
        "OPTIONAL MATCH (m)-[:MEASURES]->(t:Table) "
        "RETURN m.metric_id AS metric_id, m.expression AS expression, "
        "m.filters AS metric_filters, m.name AS name, "
        "m.source_table AS source_table, m.joins_json AS joins_json, "
        "m.parameters_json AS parameters_json, "
        "m.type AS type, m.base_metrics AS base_metrics, "
        "m.grain AS grain, m.time_grains AS time_grains, "
        "COALESCE(m.time_grain_column, '') AS time_grain_column, "
        "COALESCE(m.aggregation, 'additive') AS aggregation, "
        "COALESCE(m.value_type, 'number') AS value_type, COALESCE(m.unit, '') AS unit, "
        "t.full_name AS table_name",
        {"id": metric_id},
    )
    return results[0] if results else None


class _UnsafeMetricError(ValueError):
    """Raised when a stored metric definition contains unsafe SQL fragments."""


def _compile_metric_cte(
    metric: dict,
    dimensions: list[str],
    cte_alias: str,
    graph: GraphClient | None = None,
    time_grain: str | None = None,
) -> str:
    """Compile a single metric into a CTE body (the SELECT inside WITH ... AS (...))."""
    table = metric.get("source_table") or metric.get("table_name", "")
    expression = metric.get("expression", "")
    name = metric.get("name", "value")
    metric_filters = metric.get("metric_filters") or []
    joins = _parse_joins_json(metric.get("joins_json"))
    group_dimensions = list(dimensions)

    # Bucket this metric's governed time axis so each CTE aggregates at the same grain.
    if graph is not None and dimensions and table:
        col_types = _fetch_column_types(table, graph)
        for j in joins:
            col_types |= _fetch_column_types(j.table, graph)
        declared = list(metric.get("time_grains") or [])
        time_axis = _resolve_time_axis(metric, col_types)
        bypass_err = _check_time_axis_bypass(dimensions, time_axis, declared, col_types)
        if bypass_err:
            raise _UnsafeMetricError(bypass_err)
        effective_grain = time_grain
        if not effective_grain and declared and time_axis in dimensions:
            effective_grain = _default_time_grain(declared)
        select_dims, group_dims, tg_err = _apply_time_grain(
            dimensions, effective_grain, declared, col_types, time_axis
        )
        if tg_err:
            raise _UnsafeMetricError(f"Metric '{name}': {tg_err}")
        dimensions = select_dims
        group_dimensions = group_dims

    # Reject unsafe alias / expression / stored predicates before inlining them.
    if not _IDENTIFIER_RE.match(str(name)):
        raise _UnsafeMetricError(f"Invalid metric name (must be a simple identifier): '{name}'")
    if not _validate_sql_expression(expression):
        raise _UnsafeMetricError(f"Metric expression is not a safe scalar SQL expression: '{expression}'")
    for mf in metric_filters:
        if not _validate_sql_predicate(str(mf)):
            raise _UnsafeMetricError(f"Stored metric filter is not a safe predicate: '{mf}'")

    used_aliases: set[str] = set()
    source_alias = _make_alias(table, used_aliases)
    alias_map: dict[str, str] = {table: source_alias}
    for j in joins:
        if j.table not in alias_map:
            alias_map[j.table] = _make_alias(j.table, used_aliases)

    select_cols = list(dimensions)
    select_cols.append(f"{expression} AS {name}")

    from_clause = f"{table} {source_alias}"
    join_clauses: list[str] = []
    for j in joins:
        jt = j.join_type if j.join_type in _VALID_JOIN_TYPES else "INNER"
        j_alias = alias_map[j.table]
        join_clauses.append(
            f"{jt} JOIN {j.table} {j_alias} "
            f"ON {source_alias}.{j.source_column} = {j_alias}.{j.target_column}"
        )

    sql = f"SELECT {', '.join(select_cols)}\n  FROM {from_clause}"
    for jc in join_clauses:
        sql += f"\n  {jc}"

    if metric_filters:
        sql += f"\n  WHERE {' AND '.join(metric_filters)}"

    if group_dimensions:
        sql += f"\n  GROUP BY {', '.join(group_dimensions)}"

    return sql


def _wrap_derived_expression(
    base_result: CompilationResult,
    metric: dict,
    dimensions: list[str],
    order_by: list[str] | None,
    limit: int | None,
) -> CompilationResult:
    """Wrap a composed CTE query with an outer SELECT that applies the derived expression.

    For example, if base CTEs produce total_revenue and total_cost,
    and the derived expression is "total_revenue - total_cost", the result is:
      WITH ... (base CTEs + outer join)
      SELECT dims, (total_revenue - total_cost) AS profit FROM (composed query) sub
    """
    name = metric.get("name", "derived")
    expression = metric.get("expression", "")

    # Reject unsafe alias / expression before inlining into the outer SELECT.
    if not _IDENTIFIER_RE.match(str(name)):
        return CompilationResult(
            sql="", source_table=base_result.source_table, metric_name=str(name),
            is_valid=False, errors=[f"Invalid derived metric name (must be a simple identifier): '{name}'"],
        )
    if not _validate_sql_expression(expression):
        return CompilationResult(
            sql="", source_table=base_result.source_table, metric_name=name,
            is_valid=False, errors=[f"Derived metric expression is not a safe scalar SQL expression: '{expression}'"],
        )

    # Validate order_by against known output names (dimensions + derived name).
    if order_by:
        ok, bad = _validate_order_by(order_by, set(dimensions) | {name})
        if not ok:
            return CompilationResult(
                sql="", source_table=base_result.source_table, metric_name=name,
                is_valid=False, errors=[f"Invalid order_by entry: '{bad}'"],
            )

    # Wrap the entire composed query as a subquery
    dim_cols = ", ".join(f"sub.{d}" for d in dimensions) if dimensions else ""
    select_parts = []
    if dim_cols:
        select_parts.append(dim_cols)
    select_parts.append(f"({expression}) AS {name}")

    sql = f"SELECT {', '.join(select_parts)}\nFROM (\n{base_result.sql}\n) sub"

    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    elif dimensions:
        sql += f"\nORDER BY {name} DESC"

    if limit:
        sql += f"\nLIMIT {limit}"

    return CompilationResult(
        sql=sql,
        source_table=base_result.source_table,
        metric_name=name,
    )


def compose_metrics(
    metric_ids: list[str],
    graph: GraphClient,
    dimensions: list[str] | None = None,
    filters: list[FilterClause] | None = None,
    order_by: list[str] | None = None,
    limit: int | None = None,
    time_grain: str | None = None,
) -> CompilationResult:
    """Compose multiple metrics into a single CTE-based SQL query.

    Each metric becomes a WITH clause, and the outer SELECT joins them
    on shared dimensions. Fully deterministic — no LLM involved.
    """
    dimensions = dimensions or []
    filters = filters or []

    if not metric_ids:
        return CompilationResult(
            sql="", source_table="", is_valid=False,
            errors=["No metric IDs provided"],
        )

    # Single metric — delegate to compile_metric
    if len(metric_ids) == 1:
        return compile_metric(
            metric_ids[0], graph, dimensions=dimensions,
            filters=filters, order_by=order_by, limit=limit,
            time_grain=time_grain,
        )

    # Fetch all metric definitions
    metric_defs: list[dict] = []
    errors: list[str] = []
    for mid in metric_ids:
        mdef = _fetch_metric_def(mid, graph)
        if not mdef:
            errors.append(f"Metric '{mid}' not found")
        else:
            metric_defs.append(mdef)

    if errors:
        return CompilationResult(
            sql="", source_table="", is_valid=False, errors=errors,
        )

    # Reject cross-datasource composition: all base metrics must share one datasource.
    # Permissive when unknown — skip metrics whose source table is untagged (null datasource_id).
    ds_by_metric: dict[str, str] = {}
    for mdef in metric_defs:
        mtable = mdef.get("source_table") or mdef.get("table_name", "")
        if not mtable:
            continue
        mds = _fetch_table_datasource(mtable, graph)
        if mds:
            ds_by_metric[mdef.get("name", mdef["metric_id"])] = mds
    distinct_ds = set(ds_by_metric.values())
    if len(distinct_ds) > 1:
        detail = ", ".join(f"{k} -> {v}" for k, v in ds_by_metric.items())
        return CompilationResult(
            sql="", source_table=(metric_defs[0].get("source_table", "") if metric_defs else ""),
            metric_name=" + ".join(m.get("name", m["metric_id"]) for m in metric_defs),
            is_valid=False,
            errors=[
                f"Derived metric composes base metrics from different datasources "
                f"({detail}); composition must stay within one datasource."
            ],
        )

    # Build CTEs — one per metric
    cte_parts: list[str] = []
    cte_names: list[str] = []  # CTE alias for each metric
    metric_names: list[str] = []  # output column name from each metric

    used_cte_names: set[str] = set()
    for mdef in metric_defs:
        name = mdef.get("name", mdef["metric_id"])
        # Ensure unique CTE name
        cte_name = name
        i = 2
        while cte_name in used_cte_names:
            cte_name = f"{name}_{i}"
            i += 1
        used_cte_names.add(cte_name)

        try:
            cte_body = _compile_metric_cte(
                mdef, dimensions, cte_name, graph=graph, time_grain=time_grain
            )
        except _UnsafeMetricError as e:
            return CompilationResult(
                sql="", source_table=mdef.get("source_table", ""),
                metric_name=name, is_valid=False, errors=[str(e)],
            )
        cte_parts.append(f"{cte_name} AS (\n  {cte_body}\n)")
        cte_names.append(cte_name)
        metric_names.append(name)

    # Build outer SELECT — join CTEs on shared dimensions.
    first = cte_names[0]

    # Dimensions are COALESCEd across every CTE so a NULL/missing dimension value in
    # any single CTE doesn't blank the output row. Each metric contributes its value.
    outer_select: list[str] = []
    for d in dimensions:
        coalesced = ", ".join(f"{cte}.{d}" for cte in cte_names)
        outer_select.append(f"COALESCE({coalesced}) AS {d}")
    for cte_name, metric_name in zip(cte_names, metric_names):
        outer_select.append(f"{cte_name}.{metric_name}")

    # FROM first CTE, FULL OUTER JOIN the rest on dimensions so no CTE's rows are
    # dropped when its dimension set differs from the first CTE's. For chains beyond
    # two CTEs, join each new CTE against the COALESCE of all preceding CTEs' dims.
    outer_from = first
    outer_joins: list[str] = []
    for idx, cte_name in enumerate(cte_names[1:], start=1):
        if dimensions:
            prior = cte_names[:idx]
            on_parts = []
            for d in dimensions:
                left = (
                    f"{prior[0]}.{d}"
                    if len(prior) == 1
                    else f"COALESCE({', '.join(f'{c}.{d}' for c in prior)})"
                )
                on_parts.append(f"{left} = {cte_name}.{d}")
            outer_joins.append(f"FULL OUTER JOIN {cte_name} ON {' AND '.join(on_parts)}")
        else:
            # No shared dimensions — CROSS JOIN (each CTE returns one row)
            outer_joins.append(f"CROSS JOIN {cte_name}")

    # Assemble the full query
    sql = f"WITH {',\n'.join(cte_parts)}\nSELECT {', '.join(outer_select)}\nFROM {outer_from}"
    for oj in outer_joins:
        sql += f"\n{oj}"

    # Validate user filter columns against the real columns of all composed tables.
    if filters:
        known_columns: set[str] = set()
        for mdef in metric_defs:
            mtable = mdef.get("source_table") or mdef.get("table_name", "")
            if mtable:
                known_columns |= _fetch_table_columns(mtable, graph)
            for j in _parse_joins_json(mdef.get("joins_json")):
                known_columns |= _fetch_table_columns(j.table, graph)
        if known_columns:
            for f in filters:
                if f.column not in known_columns:
                    return CompilationResult(
                        sql="", source_table=(metric_defs[0].get("source_table", "") if metric_defs else ""),
                        metric_name=" + ".join(metric_names), is_valid=False,
                        errors=[f"Filter column '{f.column}' is not a known column"],
                    )

    # Validate order_by against known output names (dimensions + metric names).
    if order_by:
        ok, bad = _validate_order_by(order_by, set(dimensions) | set(metric_names))
        if not ok:
            return CompilationResult(
                sql="", source_table=(metric_defs[0].get("source_table", "") if metric_defs else ""),
                metric_name=" + ".join(metric_names), is_valid=False,
                errors=[f"Invalid order_by entry: '{bad}'"],
            )

    # Outer WHERE (user-provided filters on dimension columns)
    where_parts = _build_filter_clauses(filters)
    if where_parts:
        # Qualify filter columns with first CTE alias
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    # ORDER BY (reference the coalesced dimension alias, not a single CTE's column)
    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    elif dimensions:
        sql += f"\nORDER BY {dimensions[0]}"

    # LIMIT
    if limit:
        sql += f"\nLIMIT {limit}"

    source_tables = [m.get("source_table", "") for m in metric_defs]
    combined_name = " + ".join(metric_names)

    # Annotate (don't block) compositions whose components can't be freely rolled up.
    # The CTEs recompute each metric from base rows at the shared dimensions, so the
    # values themselves are correct — but a consumer aggregating the RESULT further
    # (e.g. summing a non_additive ratio across the returned rows) would be wrong.
    warnings: list[str] = []
    non_additive = [
        m.get("name", m["metric_id"])
        for m in metric_defs
        if (m.get("aggregation") or "additive").lower() == "non_additive"
    ]
    if non_additive:
        warnings.append(
            f"Composition includes non_additive metric(s) {non_additive}: these values "
            f"are correct at the queried grain but must NOT be summed/re-aggregated across rows."
        )

    # Warn on mixing incompatible units (e.g. USD + EUR, or currency + count). Distinct
    # non-empty units among the composed metrics are almost always a modeling mistake.
    units = {
        (m.get("unit") or "").strip()
        for m in metric_defs
        if (m.get("unit") or "").strip()
    }
    if len(units) > 1:
        warnings.append(
            f"Composition mixes metrics with different units {sorted(units)}; "
            f"combining them may not be meaningful."
        )

    return CompilationResult(
        sql=sql,
        source_table=source_tables[0] if source_tables else "",
        metric_name=combined_name,
        warnings=warnings,
    )


def compile_sql(
    table: str,
    select_columns: list[str],
    filters: list[FilterClause] | None = None,
    group_by: list[str] | None = None,
    order_by: list[str] | None = None,
    limit: int | None = None,
) -> CompilationResult:
    """Compile a raw analytical query (no metric, just table + columns)."""
    last_segment = table.split(".")[-1] if table else ""
    alias = last_segment[0] if last_segment else "t"
    sql = f"SELECT {', '.join(select_columns)}\nFROM {table} {alias}"

    where_parts = _build_filter_clauses(filters or [])
    if where_parts:
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    if group_by:
        sql += f"\nGROUP BY {', '.join(group_by)}"

    # Validate order_by against the columns/expressions actually selected or grouped.
    if order_by:
        known = set(select_columns) | set(group_by or [])
        ok, bad = _validate_order_by(order_by, known)
        if not ok:
            return CompilationResult(
                sql="", source_table=table, is_valid=False,
                errors=[f"Invalid order_by entry: '{bad}'"],
            )
        sql += f"\nORDER BY {', '.join(order_by)}"

    if limit:
        sql += f"\nLIMIT {limit}"

    return CompilationResult(sql=sql, source_table=table)


def _build_filter_clauses(filters: list[FilterClause]) -> list[str]:
    """Build SQL WHERE clause fragments from filter objects."""
    clauses = []
    for f in filters:
        op = f.operator.upper()
        if op not in _VALID_OPERATORS:
            continue
        # Escape single quotes in string literals so a value cannot break out.
        if op == "IN" and isinstance(f.value, list):
            formatted = ", ".join(
                f"'{_escape_sql_string(v)}'" if isinstance(v, str) else str(v) for v in f.value
            )
            clauses.append(f"{f.column} IN ({formatted})")
        elif isinstance(f.value, str):
            clauses.append(f"{f.column} {op} '{_escape_sql_string(f.value)}'")
        else:
            clauses.append(f"{f.column} {op} {f.value}")
    return clauses

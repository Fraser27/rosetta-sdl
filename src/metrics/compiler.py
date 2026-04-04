"""Deterministic metric compiler — generates SQL from metric definitions without LLM.

Pattern adapted from Fusion-main/agent/src/compiler.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_FUNC_RE = re.compile(r"^([A-Z_][A-Z_0-9]*)\s*\((.+)\)$", re.IGNORECASE)
_VALID_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "NOT IN", "BETWEEN"}
_VALID_JOIN_TYPES = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}


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


def _make_alias(table: str, used: set[str]) -> str:
    """Generate a short alias from the table name, avoiding collisions."""
    short = table.split(".")[-1][0] if "." in table else "t"
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
) -> CompilationResult:
    """Compile a governed metric into SQL by reading its definition from the graph.

    This is fully deterministic — no LLM involved.
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

    # Validate filters against declared parameters
    if parameters:
        param_map = {p.column: p for p in parameters}
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

    sql = f"SELECT {', '.join(select_cols)}\nFROM {from_clause}"
    for jc in join_clauses:
        sql += f"\n{jc}"

    # Build WHERE
    where_parts = list(metric_filters)
    where_parts.extend(_build_filter_clauses(filters))
    if where_parts:
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    # GROUP BY
    if dimensions:
        sql += f"\nGROUP BY {', '.join(dimensions)}"

    # ORDER BY
    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    elif dimensions:
        sql += f"\nORDER BY {name} DESC"

    # LIMIT
    if limit:
        sql += f"\nLIMIT {limit}"

    return CompilationResult(sql=sql, source_table=table, metric_name=name)


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
        "m.grain AS grain, t.full_name AS table_name",
        {"id": metric_id},
    )
    return results[0] if results else None


def _compile_metric_cte(
    metric: dict,
    dimensions: list[str],
    cte_alias: str,
) -> str:
    """Compile a single metric into a CTE body (the SELECT inside WITH ... AS (...))."""
    table = metric.get("source_table") or metric.get("table_name", "")
    expression = metric.get("expression", "")
    name = metric.get("name", "value")
    metric_filters = metric.get("metric_filters") or []
    joins = _parse_joins_json(metric.get("joins_json"))

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

    if dimensions:
        sql += f"\n  GROUP BY {', '.join(dimensions)}"

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

        cte_body = _compile_metric_cte(mdef, dimensions, cte_name)
        cte_parts.append(f"{cte_name} AS (\n  {cte_body}\n)")
        cte_names.append(cte_name)
        metric_names.append(name)

    # Build outer SELECT — join CTEs on shared dimensions
    first = cte_names[0]

    # Select dimensions from the first CTE + each metric's value column
    outer_select: list[str] = [f"{first}.{d}" for d in dimensions]
    for cte_name, metric_name in zip(cte_names, metric_names):
        outer_select.append(f"{cte_name}.{metric_name}")

    # FROM first CTE, LEFT JOIN the rest on dimensions
    outer_from = first
    outer_joins: list[str] = []
    for cte_name in cte_names[1:]:
        if dimensions:
            on_clause = " AND ".join(
                f"{first}.{d} = {cte_name}.{d}" for d in dimensions
            )
            outer_joins.append(f"LEFT JOIN {cte_name} ON {on_clause}")
        else:
            # No shared dimensions — CROSS JOIN (each CTE returns one row)
            outer_joins.append(f"CROSS JOIN {cte_name}")

    # Assemble the full query
    sql = f"WITH {',\n'.join(cte_parts)}\nSELECT {', '.join(outer_select)}\nFROM {outer_from}"
    for oj in outer_joins:
        sql += f"\n{oj}"

    # Outer WHERE (user-provided filters on dimension columns)
    where_parts = _build_filter_clauses(filters)
    if where_parts:
        # Qualify filter columns with first CTE alias
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    # ORDER BY
    if order_by:
        sql += f"\nORDER BY {', '.join(order_by)}"
    elif dimensions:
        sql += f"\nORDER BY {first}.{dimensions[0]}"

    # LIMIT
    if limit:
        sql += f"\nLIMIT {limit}"

    source_tables = [m.get("source_table", "") for m in metric_defs]
    combined_name = " + ".join(metric_names)

    return CompilationResult(
        sql=sql,
        source_table=source_tables[0] if source_tables else "",
        metric_name=combined_name,
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
    alias = table.split(".")[-1][0] if "." in table else "t"
    sql = f"SELECT {', '.join(select_columns)}\nFROM {table} {alias}"

    where_parts = _build_filter_clauses(filters or [])
    if where_parts:
        sql += f"\nWHERE {' AND '.join(where_parts)}"

    if group_by:
        sql += f"\nGROUP BY {', '.join(group_by)}"

    if order_by:
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
        if op == "IN" and isinstance(f.value, list):
            formatted = ", ".join(
                f"'{v}'" if isinstance(v, str) else str(v) for v in f.value
            )
            clauses.append(f"{f.column} IN ({formatted})")
        elif isinstance(f.value, str):
            clauses.append(f"{f.column} {op} '{f.value}'")
        else:
            clauses.append(f"{f.column} {op} {f.value}")
    return clauses

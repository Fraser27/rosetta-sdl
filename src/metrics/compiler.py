"""Deterministic metric compiler — generates SQL from metric definitions without LLM.

Pattern adapted from Fusion-main/agent/src/compiler.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_FUNC_RE = re.compile(r"^([A-Z_][A-Z_0-9]*)\s*\((.+)\)$", re.IGNORECASE)
_VALID_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "NOT IN", "BETWEEN"}


@dataclass
class FilterClause:
    column: str
    operator: str
    value: str | int | float | list


@dataclass
class CompilationResult:
    sql: str
    source_table: str
    metric_name: str | None = None
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)


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
    dimensions = dimensions or []
    filters = filters or []

    # Fetch metric from graph
    results = graph.query(
        "MATCH (m:Metric {metric_id: $id}) "
        "OPTIONAL MATCH (m)-[:MEASURES]->(t:Table) "
        "RETURN m.expression AS expression, m.filters AS metric_filters, "
        "m.name AS name, m.source_table AS source_table, t.full_name AS table_name",
        {"id": metric_id},
    )

    if not results:
        return CompilationResult(
            sql="", source_table="", metric_name=None,
            is_valid=False, errors=[f"Metric '{metric_id}' not found"],
        )

    metric = results[0]
    table = metric.get("source_table") or metric.get("table_name", "")
    expression = metric.get("expression", "")
    name = metric.get("name", metric_id)
    metric_filters = metric.get("metric_filters") or []

    # Build SELECT
    select_cols = list(dimensions)
    select_cols.append(f"{expression} AS {name}")

    alias = table.split(".")[-1][0] if "." in table else "t"
    sql = f"SELECT {', '.join(select_cols)}\nFROM {table} {alias}"

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

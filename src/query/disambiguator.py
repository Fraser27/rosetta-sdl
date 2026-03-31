"""Graph-based disambiguator — resolves business terms to schema elements."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


@dataclass
class DisambiguationResult:
    tables: list[str] = field(default_factory=list)
    columns: dict[str, list[str]] = field(default_factory=dict)  # table -> [columns]
    metrics: list[dict] = field(default_factory=list)
    join_paths: list[dict] = field(default_factory=list)
    confidence: float = 0.0


def disambiguate(question: str, graph: GraphClient) -> DisambiguationResult:
    """Resolve a natural language question to specific schema elements using the graph.

    1. Search metrics by name/synonym
    2. Search tables/columns by full-text
    3. Find join paths between matched tables
    """
    result = DisambiguationResult()

    # 1. Find matching metrics
    metric_hits = graph.query(
        "CALL db.index.fulltext.queryNodes('metric_search', $q) YIELD node, score "
        "WHERE score > 0.3 "
        "WITH node AS m, score "
        "MATCH (m)-[:MEASURES]->(t:Table) "
        "RETURN m.metric_id AS metric_id, m.name AS name, m.expression AS expression, "
        "t.full_name AS source_table, score "
        "ORDER BY score DESC LIMIT 5",
        {"q": question},
    )
    result.metrics = metric_hits

    # Collect tables from metric matches
    tables_from_metrics = {h["source_table"] for h in metric_hits if h.get("source_table")}

    # 2. Find matching tables directly
    table_hits = graph.query(
        "CALL db.index.fulltext.queryNodes('table_search', $q) YIELD node, score "
        "WHERE score > 0.3 "
        "RETURN node.full_name AS full_name, node.name AS name, score "
        "ORDER BY score DESC LIMIT 5",
        {"q": question},
    )
    tables_from_search = {h["full_name"] for h in table_hits if h.get("full_name")}

    # 3. Find matching columns
    column_hits = graph.query(
        "CALL db.index.fulltext.queryNodes('column_search', $q) YIELD node, score "
        "WHERE score > 0.3 "
        "RETURN node.name AS name, node.table AS table, score "
        "ORDER BY score DESC LIMIT 10",
        {"q": question},
    )
    for ch in column_hits:
        table = ch.get("table", "")
        col = ch.get("name", "")
        if table and col:
            result.columns.setdefault(table, []).append(col)
            tables_from_search.add(table)

    # Combine all discovered tables
    all_tables = list(tables_from_metrics | tables_from_search)
    result.tables = all_tables

    # 4. Find join paths between pairs of matched tables
    if len(all_tables) >= 2:
        for i, t1 in enumerate(all_tables):
            for t2 in all_tables[i + 1:]:
                paths = graph.query(
                    "MATCH path = shortestPath("
                    "(t1:Table {full_name: $t1})-[:JOINS_TO*..4]-(t2:Table {full_name: $t2})) "
                    "RETURN [n IN nodes(path) | n.full_name] AS tables, "
                    "[r IN relationships(path) | r.on_column] AS join_columns",
                    {"t1": t1, "t2": t2},
                )
                result.join_paths.extend(paths)

    # Compute confidence based on match quality
    if metric_hits:
        result.confidence = max(h.get("score", 0) for h in metric_hits)
    elif table_hits:
        result.confidence = max(h.get("score", 0) for h in table_hits) * 0.8
    elif column_hits:
        result.confidence = max(h.get("score", 0) for h in column_hits) * 0.6

    logger.info(
        "Disambiguated: tables=%s, metrics=%d, joins=%d, confidence=%.2f",
        result.tables, len(result.metrics), len(result.join_paths), result.confidence,
    )
    return result

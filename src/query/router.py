"""Graph-based query router — decides whether to hit Athena, S3 Vectors, or both."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.config import EmbeddingConfig
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    route: str  # structured | unstructured | both
    matched_tables: list[str] = field(default_factory=list)
    matched_metrics: list[str] = field(default_factory=list)
    matched_documents: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def route_query(
    question: str,
    graph: GraphClient,
    min_score: float = 0.3,
    embedding_config: EmbeddingConfig | None = None,
) -> RouteResult:
    """Route a question by searching the graph for matching nodes.

    Uses Neo4j full-text indexes to find matching tables, metrics, and documents,
    then decides the route based on what matched.
    """
    # Search across all full-text indexes, with vector fallback
    hits = _search_all_indexes(question, graph, min_score, embedding_config)

    tables = [h["id"] for h in hits if h["type"] == "table"]
    metrics = [h["id"] for h in hits if h["type"] == "metric"]
    documents = [h["id"] for h in hits if h["type"] == "document"]
    scores = {h["id"]: h["score"] for h in hits}

    has_structured = bool(tables or metrics)
    has_unstructured = bool(documents)

    if has_structured and has_unstructured:
        route = "both"
    elif has_unstructured:
        route = "unstructured"
    else:
        route = "structured"  # default fallback

    result = RouteResult(
        route=route,
        matched_tables=tables,
        matched_metrics=metrics,
        matched_documents=documents,
        scores=scores,
    )
    logger.info(
        "Routed query to '%s' (tables=%d, metrics=%d, docs=%d)",
        route, len(tables), len(metrics), len(documents),
    )
    return result


def _search_all_indexes(
    question: str,
    graph: GraphClient,
    min_score: float,
    embedding_config: EmbeddingConfig | None = None,
) -> list[dict]:
    """Search all full-text indexes and return combined results, with vector fallback."""
    all_hits: list[dict] = []

    # Search tables
    try:
        table_hits = graph.query(
            "CALL db.index.fulltext.queryNodes('table_search', $q) YIELD node, score "
            "WHERE score > $min "
            "RETURN 'table' AS type, node.full_name AS id, node.name AS name, "
            "node.description AS description, score "
            "ORDER BY score DESC LIMIT 10",
            {"q": question, "min": min_score},
        )
        all_hits.extend(table_hits)
    except Exception as e:
        logger.debug("Table search failed: %s", e)

    # Search metrics
    try:
        metric_hits = graph.query(
            "CALL db.index.fulltext.queryNodes('metric_search', $q) YIELD node, score "
            "WHERE score > $min "
            "RETURN 'metric' AS type, node.metric_id AS id, node.name AS name, "
            "node.definition AS description, score "
            "ORDER BY score DESC LIMIT 10",
            {"q": question, "min": min_score},
        )
        all_hits.extend(metric_hits)
    except Exception as e:
        logger.debug("Metric search failed: %s", e)

    # Vector fallback for metrics: if full-text confidence is low, try semantic similarity
    if embedding_config and embedding_config.enabled:
        best_metric_score = max(
            (h["score"] for h in all_hits if h.get("type") == "metric"), default=0
        )
        if best_metric_score < embedding_config.fulltext_confidence_threshold:
            try:
                from src.graph import queries as q
                from src.query.embeddings import get_embedding

                question_vec = get_embedding(
                    question, embedding_config.model_id, embedding_config.dimensions
                )
                if question_vec:
                    vector_hits = graph.query(
                        q.VECTOR_SEARCH_METRICS_SIMPLE,
                        {
                            "top_k": 5,
                            "vec": question_vec,
                            "min_score": embedding_config.vector_min_score,
                            "limit": 5,
                        },
                    )
                    if vector_hits:
                        logger.info(
                            "Router vector fallback found %d metric(s)", len(vector_hits)
                        )
                        all_hits.extend(vector_hits)
            except Exception as e:
                logger.debug("Router vector fallback failed: %s", e)

    # Search documents
    try:
        doc_hits = graph.query(
            "CALL db.index.fulltext.queryNodes('document_search', $q) YIELD node, score "
            "WHERE score > $min "
            "RETURN 'document' AS type, node.s3_key AS id, node.name AS name, "
            "node.description AS description, score "
            "ORDER BY score DESC LIMIT 10",
            {"q": question, "min": min_score},
        )
        all_hits.extend(doc_hits)
    except Exception as e:
        logger.debug("Document search failed: %s", e)

    return sorted(all_hits, key=lambda h: h.get("score", 0), reverse=True)

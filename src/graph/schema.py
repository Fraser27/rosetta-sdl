"""Graph schema — constraints, indexes, and initialization."""

from __future__ import annotations

import logging

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

# Constraints ensure uniqueness for key node types
CONSTRAINTS = [
    "CREATE CONSTRAINT table_unique IF NOT EXISTS FOR (t:Table) REQUIRE t.full_name IS UNIQUE",
    "CREATE CONSTRAINT metric_unique IF NOT EXISTS FOR (m:Metric) REQUIRE m.metric_id IS UNIQUE",
    "CREATE CONSTRAINT document_unique IF NOT EXISTS FOR (d:Document) REQUIRE d.s3_key IS UNIQUE",
    "CREATE CONSTRAINT business_term_unique IF NOT EXISTS FOR (bt:BusinessTerm) REQUIRE bt.name IS UNIQUE",
    "CREATE CONSTRAINT datasource_unique IF NOT EXISTS FOR (ds:DataSource) REQUIRE ds.name IS UNIQUE",
    "CREATE CONSTRAINT datasource_id_unique IF NOT EXISTS FOR (ds:DataSource) REQUIRE ds.datasource_id IS UNIQUE",
    "CREATE CONSTRAINT concept_unique IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
    # Index metric-version snapshots for fast per-metric history lookup.
    "CREATE INDEX metric_version_lookup IF NOT EXISTS FOR (mv:MetricVersion) ON (mv.metric_id)",
]

# Full-text indexes for search across node properties.
#
# All use the `english` analyzer rather than Neo4j's default
# `standard-no-stop-words`, which indexes stopwords as ordinary terms. On a small
# corpus that lets a stopword shared with one node's description dominate the
# Lucene score and win routing outright. `english` also stems, so "returns"
# matches "return". Query-side sanitisation in strip_fulltext_stopwords is the
# belt to this braces — it protects deployments whose indexes predate this change.
_FULLTEXT_ANALYZER = "OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}"

FULLTEXT_INDEXES = [
    (
        "table_search",
        "CREATE FULLTEXT INDEX table_search IF NOT EXISTS "
        "FOR (t:Table) ON EACH [t.name, t.full_name, t.description] " + _FULLTEXT_ANALYZER,
    ),
    (
        "column_search",
        "CREATE FULLTEXT INDEX column_search IF NOT EXISTS "
        "FOR (c:Column) ON EACH [c.name, c.description] " + _FULLTEXT_ANALYZER,
    ),
    (
        "metric_search",
        "CREATE FULLTEXT INDEX metric_search IF NOT EXISTS "
        "FOR (m:Metric) ON EACH [m.name, m.definition, m.synonyms_text] " + _FULLTEXT_ANALYZER,
    ),
    (
        "document_search",
        "CREATE FULLTEXT INDEX document_search IF NOT EXISTS "
        "FOR (d:Document) ON EACH [d.name, d.description] " + _FULLTEXT_ANALYZER,
    ),
    (
        "business_term_search",
        "CREATE FULLTEXT INDEX business_term_search IF NOT EXISTS "
        "FOR (bt:BusinessTerm) ON EACH [bt.name, bt.definition] " + _FULLTEXT_ANALYZER,
    ),
]

# Vector indexes for semantic similarity search (requires Neo4j 5.11+)
VECTOR_INDEXES = [
    (
        "metric_embedding",
        "CREATE VECTOR INDEX metric_embedding IF NOT EXISTS "
        "FOR (m:Metric) ON (m.embedding) "
        "OPTIONS {indexConfig: {"
        "`vector.dimensions`: 1024, "
        "`vector.similarity_function`: 'cosine'"
        "}}",
    ),
    (
        # Embeddings of uploaded document metadata (txt/md). Fixed at 1024 dims
        # (Titan v2). Switching to a different-dimension embedding model requires
        # dropping + recreating this index and re-uploading metadata.
        "document_embedding",
        "CREATE VECTOR INDEX document_embedding IF NOT EXISTS "
        "FOR (d:Document) ON (d.metadata_embedding) "
        "OPTIONS {indexConfig: {"
        "`vector.dimensions`: 1024, "
        "`vector.similarity_function`: 'cosine'"
        "}}",
    ),
]


def _migrate_fulltext_analyzers(graph: GraphClient) -> None:
    """Drop full-text indexes still using a non-`english` analyzer so they get rebuilt.

    `CREATE ... IF NOT EXISTS` is a no-op against an existing index, including when
    the analyzer differs, so an index created before the `english` switch would keep
    its old stopword-preserving behaviour forever. Dropping is safe: the caller
    recreates immediately, and full-text indexes are derived data rebuilt from nodes.
    """
    managed = {name for name, _ in FULLTEXT_INDEXES}
    try:
        existing = graph.query(
            "SHOW FULLTEXT INDEXES YIELD name, options "
            "RETURN name, options['indexConfig']['fulltext.analyzer'] AS analyzer"
        )
    except Exception as e:
        logger.warning("Could not inspect full-text analyzers, skipping migration: %s", e)
        return

    for row in existing:
        name = row.get("name")
        if name in managed and row.get("analyzer") != "english":
            try:
                graph.write(f"DROP INDEX {name}")
                logger.info(
                    "Dropped index %s (analyzer=%s) for rebuild with 'english'",
                    name,
                    row.get("analyzer"),
                )
            except Exception as e:
                logger.warning("Could not drop index %s for analyzer migration: %s", name, e)


def init_schema(graph: GraphClient) -> None:
    """Create constraints and indexes if they don't exist."""
    for cypher in CONSTRAINTS:
        try:
            graph.write(cypher)
        except Exception as e:
            logger.warning("Constraint already exists or error: %s", e)

    _migrate_fulltext_analyzers(graph)

    for name, cypher in FULLTEXT_INDEXES:
        try:
            graph.write(cypher)
            logger.info("Created/verified index: %s", name)
        except Exception as e:
            logger.warning("Index %s already exists or error: %s", name, e)

    for name, cypher in VECTOR_INDEXES:
        try:
            graph.write(cypher)
            logger.info("Created/verified vector index: %s", name)
        except Exception as e:
            logger.warning("Vector index %s creation failed (Neo4j 5.11+ required): %s", name, e)

    logger.info("Graph schema initialized")

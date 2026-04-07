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
    "CREATE CONSTRAINT concept_unique IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
]

# Full-text indexes for search across node properties
FULLTEXT_INDEXES = [
    (
        "table_search",
        "CREATE FULLTEXT INDEX table_search IF NOT EXISTS "
        "FOR (t:Table) ON EACH [t.name, t.full_name, t.description]",
    ),
    (
        "column_search",
        "CREATE FULLTEXT INDEX column_search IF NOT EXISTS "
        "FOR (c:Column) ON EACH [c.name, c.description]",
    ),
    (
        "metric_search",
        "CREATE FULLTEXT INDEX metric_search IF NOT EXISTS "
        "FOR (m:Metric) ON EACH [m.name, m.definition, m.synonyms_text]",
    ),
    (
        "document_search",
        "CREATE FULLTEXT INDEX document_search IF NOT EXISTS "
        "FOR (d:Document) ON EACH [d.name, d.description]",
    ),
    (
        "business_term_search",
        "CREATE FULLTEXT INDEX business_term_search IF NOT EXISTS "
        "FOR (bt:BusinessTerm) ON EACH [bt.name, bt.definition]",
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
]


def init_schema(graph: GraphClient) -> None:
    """Create constraints and indexes if they don't exist."""
    for cypher in CONSTRAINTS:
        try:
            graph.write(cypher)
        except Exception as e:
            logger.warning("Constraint already exists or error: %s", e)

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

"""Graph loader — bulk loads metadata into Neo4j."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.graph import queries

if TYPE_CHECKING:
    from src.catalog.models import DocumentMeta, JoinPath, MetricDefinition, TableMeta
    from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


def load_structured(graph: GraphClient, tables: list[TableMeta], joins: list[JoinPath]) -> int:
    """Load tables, columns, and join paths into the graph. Returns node count."""
    count = 0

    for table in tables:
        # Merge datasource
        graph.write(queries.MERGE_DATASOURCE, {
            "name": table.database,
            "glue_database": table.database,
            "catalog_type": table.catalog_type,
        })

        # Merge table
        graph.write(queries.MERGE_TABLE, {
            "full_name": table.full_name,
            "name": table.name,
            "database": table.database,
            "description": table.description,
            "catalog_type": table.catalog_type,
            "row_count_approx": table.row_count_approx,
        })
        count += 1

        # Merge columns
        for col in table.columns:
            graph.write(queries.MERGE_COLUMN, {
                "table_full_name": table.full_name,
                "name": col.name,
                "data_type": col.data_type,
                "description": col.description,
                "is_partition": col.is_partition,
                "is_primary_key": col.is_primary_key,
            })
            count += 1

    # Merge join paths
    for jp in joins:
        graph.write(queries.MERGE_JOIN_PATH, {
            "source_table": jp.source_table,
            "target_table": jp.target_table,
            "on_column": jp.on_column,
            "join_type": jp.join_type,
        })

    logger.info("Loaded %d structured nodes into graph", count)
    return count


def load_metrics(graph: GraphClient, metrics: list[MetricDefinition]) -> int:
    """Load metric definitions into the graph. Returns count."""
    for m in metrics:
        graph.write(queries.MERGE_METRIC, {
            "metric_id": m.metric_id,
            "name": m.name,
            "definition": m.definition,
            "expression": m.expression,
            "type": m.type,
            "filters": m.filters,
            "grain": m.grain,
            "synonyms": m.synonyms,
            "synonyms_text": " ".join(m.synonyms),
            "time_grains": m.time_grains,
            "source_table": m.source_table,
            "joins_json": "[]",
            "base_metrics": [],
            "source": "yaml",
        })

        # Link business terms from synonyms
        for synonym in [m.name] + m.synonyms:
            graph.write(queries.MERGE_BUSINESS_TERM, {
                "name": synonym.lower(),
                "definition": m.definition,
                "synonyms": m.synonyms,
            })
            graph.write(queries.LINK_TERM_TO_METRIC, {
                "term_name": synonym.lower(),
                "metric_id": m.metric_id,
            })

    logger.info("Loaded %d metrics into graph", len(metrics))
    return len(metrics)


def load_documents(graph: GraphClient, documents: list[DocumentMeta]) -> int:
    """Load document metadata and their metadata keys into the graph. Returns count."""
    metadata_key_count = 0
    for doc in documents:
        graph.write(queries.MERGE_DOCUMENT, {
            "s3_key": doc.s3_key,
            "name": doc.name,
            "vector_bucket": doc.vector_bucket,
            "vector_index": doc.vector_index,
            "description": doc.description,
            "type": doc.type,
        })

        for mk in doc.metadata_keys:
            graph.write(queries.MERGE_DOCUMENT_METADATA_KEY, {
                "s3_key": doc.s3_key,
                "name": mk.name,
                "data_type": mk.data_type,
                "filterable": mk.description != "non-filterable",
            })
            metadata_key_count += 1

    logger.info("Loaded %d documents with %d metadata keys into graph", len(documents), metadata_key_count)
    return len(documents)

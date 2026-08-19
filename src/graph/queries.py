"""Parameterized Cypher query templates."""

# -- Data loading --

MERGE_DATASOURCE = """
MERGE (ds:DataSource {name: $name})
SET ds.glue_database = $glue_database, ds.catalog_type = $catalog_type
"""

# -- Datasource management (executor-backed) --

UPSERT_DATASOURCE_FULL = """
MERGE (ds:DataSource {datasource_id: $datasource_id})
SET ds.name = $name, ds.type = $type, ds.endpoint = $endpoint,
    ds.database = $database, ds.region = $region,
    ds.secret_arn = $secret_arn, ds.status = $status,
    ds.enabled = $enabled, ds.created_at = coalesce(ds.created_at, $created_at),
    ds.last_health_check = $last_health_check
"""

LIST_DATASOURCES_FULL = """
MATCH (ds:DataSource)
WHERE ds.datasource_id IS NOT NULL
OPTIONAL MATCH (m:Metric)-[:EXECUTES_ON]->(ds)
RETURN ds.datasource_id AS datasource_id, ds.name AS name, ds.type AS type,
       ds.endpoint AS endpoint, ds.database AS database, ds.region AS region,
       ds.secret_arn AS secret_arn, ds.status AS status, ds.enabled AS enabled,
       ds.last_health_check AS last_health_check, ds.created_at AS created_at,
       count(m) AS metric_count
ORDER BY ds.name
"""

GET_DATASOURCE = """
MATCH (ds:DataSource {datasource_id: $datasource_id})
OPTIONAL MATCH (m:Metric)-[:EXECUTES_ON]->(ds)
RETURN ds.datasource_id AS datasource_id, ds.name AS name, ds.type AS type,
       ds.endpoint AS endpoint, ds.database AS database, ds.region AS region,
       ds.secret_arn AS secret_arn, ds.status AS status, ds.enabled AS enabled,
       ds.last_health_check AS last_health_check, ds.created_at AS created_at,
       count(m) AS metric_count
"""

DELETE_DATASOURCE = """
MATCH (ds:DataSource {datasource_id: $datasource_id})
DETACH DELETE ds
"""

GET_METRICS_FOR_DATASOURCE = """
MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $datasource_id})
RETURN m.metric_id AS metric_id, m.name AS name, m.enabled AS enabled,
       m.disabled_reason AS disabled_reason
"""

SET_DATASOURCE_ENABLED = """
MATCH (ds:DataSource {datasource_id: $datasource_id})
SET ds.enabled = $enabled
"""

# Cascade-disable: mark every enabled metric on this datasource as disabled,
# tagging the reason so re-enable only restores metrics WE turned off.
CASCADE_DISABLE_METRICS = """
MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $datasource_id})
WHERE coalesce(m.enabled, true) = true
SET m.enabled = false, m.disabled_reason = $reason
RETURN count(m) AS affected
"""

# Cascade-enable: restore only metrics that WE disabled via the cascade
# (matched by the sentinel reason), leaving individually-disabled metrics off.
CASCADE_ENABLE_METRICS = """
MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $datasource_id})
WHERE m.enabled = false AND m.disabled_reason = $reason
SET m.enabled = true, m.disabled_reason = null
RETURN count(m) AS affected
"""

LINK_METRIC_TO_DATASOURCE = """
MATCH (m:Metric {metric_id: $metric_id}), (ds:DataSource {datasource_id: $datasource_id})
MERGE (m)-[:EXECUTES_ON]->(ds)
"""

UNLINK_METRIC_FROM_DATASOURCE = """
MATCH (m:Metric {metric_id: $metric_id})-[r:EXECUTES_ON]->()
DELETE r
"""

UPSERT_SYSTEM_CONFIG = """
MERGE (c:SystemConfig {key: $key})
SET c.query_model = $query_model
"""

# Set only the s3vectors embedding model, preserving query_model.
UPSERT_S3VECTORS_EMBEDDING_MODEL = """
MERGE (c:SystemConfig {key: $key})
SET c.s3vectors_embedding_model = $s3vectors_embedding_model
"""

# Set only the enrichment model, preserving the others.
UPSERT_ENRICHMENT_MODEL = """
MERGE (c:SystemConfig {key: $key})
SET c.enrichment_model = $enrichment_model
"""

# Set only the ungoverned-query kill switch, preserving the others.
UPSERT_BLOCK_UNGOVERNED = """
MERGE (c:SystemConfig {key: $key})
SET c.block_ungoverned_queries = $block_ungoverned_queries
"""

# Set only the metric-matching thresholds, preserving the others.
UPSERT_MATCH_THRESHOLDS = """
MERGE (c:SystemConfig {key: $key})
SET c.metric_match_min_score = $metric_match_min_score,
    c.fulltext_confidence_threshold = $fulltext_confidence_threshold,
    c.vector_min_score = $vector_min_score
"""

GET_SYSTEM_CONFIG = """
MATCH (c:SystemConfig {key: $key})
RETURN c.query_model AS query_model,
       c.s3vectors_embedding_model AS s3vectors_embedding_model,
       c.enrichment_model AS enrichment_model,
       c.block_ungoverned_queries AS block_ungoverned_queries,
       c.metric_match_min_score AS metric_match_min_score,
       c.fulltext_confidence_threshold AS fulltext_confidence_threshold,
       c.vector_min_score AS vector_min_score
"""

MERGE_TABLE = """
MERGE (t:Table {full_name: $full_name})
SET t.name = $name, t.database = $database, t.description = $description,
    t.catalog_type = $catalog_type, t.row_count_approx = $row_count_approx,
    t.datasource_id = $datasource_id
WITH t
MATCH (ds:DataSource {name: $database})
MERGE (ds)-[:CONTAINS]->(t)
WITH t
OPTIONAL MATCH (eds:DataSource {datasource_id: $datasource_id})
FOREACH (_ IN CASE WHEN eds IS NOT NULL THEN [1] ELSE [] END |
    MERGE (eds)-[:PROVIDES]->(t)
)
"""

MERGE_COLUMN = """
MATCH (t:Table {full_name: $table_full_name})
MERGE (c:Column {name: $name, table: $table_full_name})
SET c.data_type = $data_type, c.description = $description,
    c.is_partition = $is_partition, c.is_primary_key = $is_primary_key,
    c.is_deprecated = coalesce(c.is_deprecated, $is_deprecated)
MERGE (t)-[:HAS_COLUMN]->(c)
"""

MERGE_JOIN_PATH = """
MATCH (t1:Table {full_name: $source_table}), (t2:Table {full_name: $target_table})
MERGE (t1)-[:JOINS_TO {on_column: $on_column, join_type: $join_type}]->(t2)
"""

# Atomic reservation of a metric_id. Relies on the metric_unique constraint to
# fail (ConstraintError) if the id already exists — this closes the check-then-write
# race that a MERGE-based create would leave open under concurrency.
CREATE_METRIC_NODE = """
CREATE (m:Metric {metric_id: $metric_id})
"""

# Snapshot the CURRENT metric state into a :MetricVersion node before it is
# overwritten by an update. Copies the versioned definition fields, links it via
# HAS_VERSION, then prunes to the 10 most recent snapshots for this metric.
# No-op if the metric doesn't exist yet (first create has nothing to snapshot).
SNAPSHOT_METRIC_VERSION = """
MATCH (m:Metric {metric_id: $metric_id})
WHERE m.name IS NOT NULL
CREATE (mv:MetricVersion {
    metric_id: m.metric_id,
    version: COALESCE(m.version, 1),
    name: m.name, definition: m.definition, expression: m.expression,
    type: m.type, source_table: m.source_table,
    synonyms: m.synonyms, grain: m.grain, filters: m.filters,
    time_grains: m.time_grains, time_grain_column: m.time_grain_column,
    aggregation: m.aggregation,
    value_type: m.value_type, unit: m.unit, format: m.format,
    status: m.status, updated_by: m.updated_by, updated_at: m.updated_at,
    joins_json: m.joins_json, parameters_json: m.parameters_json,
    base_metrics: m.base_metrics, source: m.source,
    snapshot_at: $snapshot_at
})
CREATE (m)-[:HAS_VERSION]->(mv)
WITH m
MATCH (m)-[:HAS_VERSION]->(old:MetricVersion)
WITH old ORDER BY old.version DESC
SKIP 10
DETACH DELETE old
"""

# List version snapshots for a metric (newest first), summary fields only.
LIST_METRIC_VERSIONS = """
MATCH (:Metric {metric_id: $metric_id})-[:HAS_VERSION]->(mv:MetricVersion)
RETURN mv.version AS version, mv.name AS name, mv.status AS status,
       mv.updated_by AS updated_by, mv.updated_at AS updated_at,
       mv.snapshot_at AS snapshot_at
ORDER BY mv.version DESC
"""

# Full definition of one historical version (for view / restore).
GET_METRIC_VERSION = """
MATCH (:Metric {metric_id: $metric_id})-[:HAS_VERSION]->(mv:MetricVersion {version: $version})
RETURN mv.metric_id AS metric_id, mv.name AS name, mv.definition AS definition,
       mv.expression AS expression, mv.type AS type, mv.source_table AS source_table,
       mv.synonyms AS synonyms, mv.grain AS grain, mv.filters AS filters,
       mv.time_grains AS time_grains,
       COALESCE(mv.time_grain_column, '') AS time_grain_column,
       mv.aggregation AS aggregation,
       mv.value_type AS value_type, mv.unit AS unit, mv.format AS format,
       mv.status AS status, mv.version AS version,
       mv.updated_by AS updated_by, mv.updated_at AS updated_at,
       mv.joins_json AS joins_json, mv.parameters_json AS parameters_json,
       mv.base_metrics AS base_metrics, mv.source AS source
"""

# Delete one specific historical version.
DELETE_METRIC_VERSION = """
MATCH (:Metric {metric_id: $metric_id})-[:HAS_VERSION]->(mv:MetricVersion {version: $version})
DETACH DELETE mv
"""

MERGE_METRIC = """
MERGE (m:Metric {metric_id: $metric_id})
SET m.name = $name, m.definition = $definition, m.expression = $expression,
    m.type = $type, m.filters = $filters, m.grain = $grain,
    m.synonyms = $synonyms, m.synonyms_text = $synonyms_text,
    m.time_grains = $time_grains, m.time_grain_column = $time_grain_column,
    m.source_table = $source_table,
    m.aggregation = $aggregation, m.value_type = $value_type,
    m.unit = $unit, m.format = $format,
    m.status = $status, m.version = $version,
    m.updated_by = $updated_by, m.updated_at = $updated_at,
    m.joins_json = $joins_json, m.parameters_json = $parameters_json,
    m.base_metrics = $base_metrics, m.source = $source
WITH m
OPTIONAL MATCH (t:Table {full_name: $source_table})
FOREACH (_ IN CASE WHEN t IS NOT NULL THEN [1] ELSE [] END |
    MERGE (m)-[:MEASURES]->(t)
)
"""

# Read just the governance fields of a metric (for version bump / status transitions).
GET_METRIC_GOVERNANCE = """
MATCH (m:Metric {metric_id: $metric_id})
RETURN COALESCE(m.status, 'approved') AS status, COALESCE(m.version, 1) AS version
"""

# Transition a metric's lifecycle status (approve/deprecate) without touching its definition.
SET_METRIC_STATUS = """
MATCH (m:Metric {metric_id: $metric_id})
SET m.status = $status, m.updated_by = $updated_by, m.updated_at = $updated_at
RETURN m.metric_id AS metric_id, m.status AS status, m.version AS version
"""

LINK_DERIVED_METRIC = """
MATCH (derived:Metric {metric_id: $derived_id}), (base:Metric {metric_id: $base_id})
MERGE (derived)-[:DERIVES_FROM]->(base)
"""

CLEAR_DERIVED_LINKS = """
MATCH (m:Metric {metric_id: $metric_id})-[r:DERIVES_FROM]->()
DELETE r
"""

MERGE_METRIC_COLUMN = """
MATCH (m:Metric {metric_id: $metric_id}), (c:Column {name: $column_name, table: $table_full_name})
MERGE (m)-[:USES_COLUMN]->(c)
"""

MERGE_DOCUMENT = """
MERGE (d:Document {s3_key: $s3_key})
SET d.name = $name, d.vector_bucket = $vector_bucket,
    d.vector_index = $vector_index, d.description = $description,
    d.type = $type
"""

MERGE_DOCUMENT_METADATA_KEY = """
MATCH (d:Document {s3_key: $s3_key})
MERGE (mk:MetadataKey {name: $name, document: $s3_key})
SET mk.data_type = $data_type, mk.filterable = $filterable
MERGE (d)-[:HAS_METADATA_KEY]->(mk)
"""

MERGE_BUSINESS_TERM = """
MERGE (bt:BusinessTerm {name: $name})
SET bt.definition = $definition, bt.synonyms = $synonyms
"""

MERGE_CONCEPT = """
MERGE (c:Concept {name: $name})
SET c.definition = $definition
"""

LINK_DOCUMENT_TO_TABLE = """
MATCH (d:Document {s3_key: $s3_key}), (t:Table {full_name: $table_full_name})
MERGE (d)-[:RELATES_TO]->(t)
"""

LINK_DOCUMENT_TO_CONCEPT = """
MATCH (d:Document {s3_key: $s3_key}), (c:Concept {name: $concept_name})
MERGE (d)-[:COVERS_CONCEPT]->(c)
"""

LINK_TERM_TO_METRIC = """
MATCH (bt:BusinessTerm {name: $term_name}), (m:Metric {metric_id: $metric_id})
MERGE (bt)-[:MAPS_TO]->(m)
"""

LINK_TERM_TO_COLUMN = """
MATCH (bt:BusinessTerm {name: $term_name}), (c:Column {name: $column_name, table: $table_full_name})
MERGE (bt)-[:MAPS_TO]->(c)
"""

SET_METRIC_EMBEDDING = """
MATCH (m:Metric {metric_id: $metric_id})
SET m.embedding = $embedding
"""

VECTOR_SEARCH_METRICS = """
CALL db.index.vector.queryNodes('metric_embedding', $top_k, $vec)
YIELD node, score
WHERE score > $min_score AND COALESCE(node.status, 'approved') = 'approved'
WITH node AS m, score
MATCH (m)-[:MEASURES]->(t:Table)
RETURN m.metric_id AS metric_id, m.name AS name, m.expression AS expression,
       t.full_name AS source_table, score
ORDER BY score DESC LIMIT $limit
"""

VECTOR_SEARCH_METRICS_SIMPLE = """
CALL db.index.vector.queryNodes('metric_embedding', $top_k, $vec)
YIELD node, score
WHERE score > $min_score AND COALESCE(node.status, 'approved') = 'approved'
RETURN 'metric' AS type, node.metric_id AS id, node.name AS name,
       node.definition AS description, score
ORDER BY score DESC LIMIT $limit
"""

# Store uploaded-metadata embedding (+ the capped source text) on a Document.
SET_DOCUMENT_METADATA_EMBEDDING = """
MATCH (d:Document {s3_key: $s3_key})
SET d.metadata_embedding = $embedding, d.metadata_text = $metadata_text
"""

# Semantic fallback for the router: kNN over document metadata embeddings.
VECTOR_SEARCH_DOCUMENTS_SIMPLE = """
CALL db.index.vector.queryNodes('document_embedding', $top_k, $vec)
YIELD node, score
WHERE score > $min_score
RETURN 'document' AS type, node.s3_key AS id, node.name AS name,
       node.description AS description, score
ORDER BY score DESC LIMIT $limit
"""

# -- Read queries --

LIST_TABLES = """
MATCH (t:Table)
OPTIONAL MATCH (t)<-[:CONTAINS]-(ds:DataSource)
RETURN t.full_name AS full_name, t.name AS name, t.database AS database,
       t.description AS description, t.catalog_type AS catalog_type,
       COALESCE(ds.name, '') AS datasource,
       COALESCE(t.datasource_id, '') AS datasource_id
ORDER BY t.full_name
"""

GET_TABLE_DETAILS = """
MATCH (t:Table {full_name: $full_name})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
RETURN t.full_name AS full_name, t.name AS name, t.database AS database,
       t.description AS description, t.catalog_type AS catalog_type,
       collect({
           name: c.name, data_type: c.data_type, description: c.description,
           is_partition: c.is_partition, is_primary_key: c.is_primary_key,
           is_deprecated: coalesce(c.is_deprecated, false)
       }) AS columns
"""

GET_TABLE_JOINS = """
MATCH (t:Table {full_name: $full_name})-[j:JOINS_TO]-(other:Table)
RETURN other.full_name AS related_table, j.on_column AS on_column, j.join_type AS join_type
"""

FIND_JOIN_PATH = """
MATCH path = shortestPath(
    (t1:Table {full_name: $source})-[:JOINS_TO*..4]-(t2:Table {full_name: $target})
)
RETURN [n IN nodes(path) | n.full_name] AS tables,
       [r IN relationships(path) | r.on_column] AS join_columns
"""

LIST_METRICS = """
MATCH (m:Metric)
OPTIONAL MATCH (m)-[:MEASURES]->(t:Table)
OPTIONAL MATCH (m)-[:EXECUTES_ON]->(ds:DataSource)
RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition,
       m.expression AS expression, m.type AS type, m.synonyms AS synonyms,
       m.grain AS grain, m.time_grains AS time_grains, m.filters AS filters,
       COALESCE(m.time_grain_column, '') AS time_grain_column,
       COALESCE(m.aggregation, 'additive') AS aggregation,
       COALESCE(m.value_type, 'number') AS value_type,
       COALESCE(m.unit, '') AS unit, COALESCE(m.format, '') AS format,
       COALESCE(m.status, 'approved') AS status, COALESCE(m.version, 1) AS version,
       COALESCE(m.updated_by, '') AS updated_by, COALESCE(m.updated_at, '') AS updated_at,
       COALESCE(t.full_name, m.source_table, '') AS source_table,
       m.joins_json AS joins_json, m.parameters_json AS parameters_json,
       m.base_metrics AS base_metrics, COALESCE(m.source, 'user') AS source,
       COALESCE(ds.datasource_id, '') AS datasource_id
ORDER BY m.name
"""

GET_METRIC = """
MATCH (m:Metric {metric_id: $metric_id})
OPTIONAL MATCH (m)-[:MEASURES]->(t:Table)
OPTIONAL MATCH (m)-[:EXECUTES_ON]->(ds:DataSource)
OPTIONAL MATCH (m)-[:USES_COLUMN]->(c:Column)
RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition,
       m.expression AS expression, m.type AS type, m.synonyms AS synonyms,
       m.grain AS grain, m.time_grains AS time_grains, m.filters AS filters,
       COALESCE(m.time_grain_column, '') AS time_grain_column,
       COALESCE(m.aggregation, 'additive') AS aggregation,
       COALESCE(m.value_type, 'number') AS value_type,
       COALESCE(m.unit, '') AS unit, COALESCE(m.format, '') AS format,
       COALESCE(m.status, 'approved') AS status, COALESCE(m.version, 1) AS version,
       COALESCE(m.updated_by, '') AS updated_by, COALESCE(m.updated_at, '') AS updated_at,
       COALESCE(m.source_table, '') AS source_table, m.joins_json AS joins_json,
       m.parameters_json AS parameters_json, m.base_metrics AS base_metrics,
       COALESCE(m.source, 'user') AS source,
       COALESCE(ds.datasource_id, '') AS datasource_id,
       t.full_name AS table_name,
       collect(c.name) AS used_columns
"""

SEARCH_ALL = """
CALL db.index.fulltext.queryNodes('table_search', $query) YIELD node, score
WHERE score > $min_score
RETURN 'table' AS type, node.full_name AS id, node.name AS name,
       node.description AS description, score
ORDER BY score DESC LIMIT $limit

UNION

CALL db.index.fulltext.queryNodes('metric_search', $query) YIELD node, score
WHERE score > $min_score
RETURN 'metric' AS type, node.metric_id AS id, node.name AS name,
       node.definition AS description, score
ORDER BY score DESC LIMIT $limit

UNION

CALL db.index.fulltext.queryNodes('document_search', $query) YIELD node, score
WHERE score > $min_score
RETURN 'document' AS type, node.s3_key AS id, node.name AS name,
       node.description AS description, score
ORDER BY score DESC LIMIT $limit
"""

LIST_DOCUMENTS = """
MATCH (d:Document)
OPTIONAL MATCH (d)-[:RELATES_TO]->(t:Table)
OPTIONAL MATCH (d)-[:COVERS_CONCEPT]->(c:Concept)
RETURN d.s3_key AS s3_key, d.name AS name, d.description AS description,
       d.type AS type, d.vector_bucket AS vector_bucket, d.vector_index AS vector_index,
       collect(DISTINCT t.full_name) AS related_tables,
       collect(DISTINCT c.name) AS concepts
ORDER BY d.name
"""

GET_DOCUMENT = """
MATCH (d:Document {s3_key: $s3_key})
OPTIONAL MATCH (d)-[:HAS_METADATA_KEY]->(mk:MetadataKey)
OPTIONAL MATCH (d)-[:RELATES_TO]->(t:Table)
OPTIONAL MATCH (d)-[:COVERS_CONCEPT]->(c:Concept)
RETURN d.s3_key AS s3_key, d.name AS name, d.description AS description,
       d.type AS type, d.vector_bucket AS vector_bucket, d.vector_index AS vector_index,
       collect(DISTINCT {name: mk.name, data_type: mk.data_type, filterable: mk.filterable}) AS metadata_keys,
       collect(DISTINCT t.full_name) AS related_tables,
       collect(DISTINCT c.name) AS concepts
"""

GRAPH_SUMMARY = """
MATCH (n)
WITH labels(n)[0] AS label, count(*) AS cnt
RETURN label, cnt ORDER BY cnt DESC
"""

GET_ALL_TABLE_NAMES = """
MATCH (t:Table) RETURN collect(t.full_name) AS table_names
"""

DELETE_METRIC = """
MATCH (m:Metric {metric_id: $metric_id})
OPTIONAL MATCH (m)-[:HAS_VERSION]->(mv:MetricVersion)
DETACH DELETE m, mv
"""

EMBEDDING_STATS = """
MATCH (m:Metric)
WITH count(m) AS total,
     count(CASE WHEN m.embedding IS NOT NULL THEN 1 END) AS embedded
RETURN total, embedded
"""

GRAPH_DATA = """
MATCH (n)
WHERE NOT n:AuditEvent AND NOT n:SystemConfig AND NOT n:BlockedQuery
WITH n, labels(n)[0] AS lbl, id(n) AS nid
RETURN collect({
    id: toString(nid),
    label: CASE lbl
        WHEN 'Table' THEN n.name
        WHEN 'Column' THEN n.name
        WHEN 'Metric' THEN n.name
        WHEN 'DataSource' THEN n.name
        WHEN 'BusinessTerm' THEN n.name
        WHEN 'Document' THEN n.name
        WHEN 'Concept' THEN n.name
        WHEN 'MetadataKey' THEN n.name
        ELSE toString(nid)
    END,
    type: lbl,
    datasource: CASE lbl
        WHEN 'DataSource' THEN n.name
        WHEN 'Table' THEN n.database
        WHEN 'Column' THEN split(n.table, '.')[0]
        WHEN 'Metric' THEN CASE WHEN n.source_table CONTAINS '.' THEN split(n.source_table, '.')[0] ELSE null END
        ELSE null
    END,
    properties: CASE lbl
        WHEN 'Metric' THEN {hasEmbedding: n.embedding IS NOT NULL}
        ELSE {}
    END
}) AS nodes
"""

GRAPH_EDGES = """
MATCH (a)-[r]->(b)
WHERE NOT a:AuditEvent AND NOT a:SystemConfig AND NOT a:BlockedQuery
  AND NOT b:AuditEvent AND NOT b:SystemConfig AND NOT b:BlockedQuery
WITH a, r, b, labels(a)[0] AS albl, labels(b)[0] AS blbl
RETURN collect({
    source: toString(id(a)),
    target: toString(id(b)),
    type: type(r)
}) AS edges
"""

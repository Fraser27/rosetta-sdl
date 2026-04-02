"""Parameterized Cypher query templates."""

# -- Data loading --

MERGE_DATASOURCE = """
MERGE (ds:DataSource {name: $name})
SET ds.glue_database = $glue_database, ds.catalog_type = $catalog_type
"""

MERGE_TABLE = """
MERGE (t:Table {full_name: $full_name})
SET t.name = $name, t.database = $database, t.description = $description,
    t.catalog_type = $catalog_type, t.row_count_approx = $row_count_approx
WITH t
MATCH (ds:DataSource {name: $database})
MERGE (ds)-[:CONTAINS]->(t)
"""

MERGE_COLUMN = """
MATCH (t:Table {full_name: $table_full_name})
MERGE (c:Column {name: $name, table: $table_full_name})
SET c.data_type = $data_type, c.description = $description,
    c.is_partition = $is_partition, c.is_primary_key = $is_primary_key
MERGE (t)-[:HAS_COLUMN]->(c)
"""

MERGE_JOIN_PATH = """
MATCH (t1:Table {full_name: $source_table}), (t2:Table {full_name: $target_table})
MERGE (t1)-[:JOINS_TO {on_column: $on_column, join_type: $join_type}]->(t2)
"""

MERGE_METRIC = """
MERGE (m:Metric {metric_id: $metric_id})
SET m.name = $name, m.definition = $definition, m.expression = $expression,
    m.type = $type, m.filters = $filters, m.grain = $grain,
    m.synonyms = $synonyms, m.synonyms_text = $synonyms_text,
    m.time_grains = $time_grains, m.source_table = $source_table,
    m.joins_json = $joins_json, m.base_metrics = $base_metrics
WITH m
OPTIONAL MATCH (t:Table {full_name: $source_table})
FOREACH (_ IN CASE WHEN t IS NOT NULL THEN [1] ELSE [] END |
    MERGE (m)-[:MEASURES]->(t)
)
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

# -- Read queries --

LIST_TABLES = """
MATCH (t:Table)
OPTIONAL MATCH (t)<-[:CONTAINS]-(ds:DataSource)
RETURN t.full_name AS full_name, t.name AS name, t.database AS database,
       t.description AS description, t.catalog_type AS catalog_type,
       ds.name AS datasource
ORDER BY t.full_name
"""

GET_TABLE_DETAILS = """
MATCH (t:Table {full_name: $full_name})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
RETURN t.full_name AS full_name, t.name AS name, t.database AS database,
       t.description AS description, t.catalog_type AS catalog_type,
       collect({
           name: c.name, data_type: c.data_type, description: c.description,
           is_partition: c.is_partition, is_primary_key: c.is_primary_key
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
RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition,
       m.expression AS expression, m.type AS type, m.synonyms AS synonyms,
       m.grain AS grain, m.time_grains AS time_grains, m.filters AS filters,
       COALESCE(t.full_name, m.source_table, '') AS source_table,
       m.joins_json AS joins_json, m.base_metrics AS base_metrics
ORDER BY m.name
"""

GET_METRIC = """
MATCH (m:Metric {metric_id: $metric_id})
OPTIONAL MATCH (m)-[:MEASURES]->(t:Table)
OPTIONAL MATCH (m)-[:USES_COLUMN]->(c:Column)
RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition,
       m.expression AS expression, m.type AS type, m.synonyms AS synonyms,
       m.grain AS grain, m.time_grains AS time_grains, m.filters AS filters,
       COALESCE(m.source_table, '') AS source_table, m.joins_json AS joins_json,
       m.base_metrics AS base_metrics,
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
DETACH DELETE m
"""

GRAPH_DATA = """
MATCH (n)
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
    properties: {}
}) AS nodes
"""

GRAPH_EDGES = """
MATCH (a)-[r]->(b)
WITH a, r, b, labels(a)[0] AS albl, labels(b)[0] AS blbl
RETURN collect({
    source: toString(id(a)),
    target: toString(id(b)),
    type: type(r)
}) AS edges
"""

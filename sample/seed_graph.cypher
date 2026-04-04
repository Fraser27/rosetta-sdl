// Seed graph for demo — run this after `docker-compose up` to populate
// Neo4j with sample ecommerce schema without needing AWS Glue.
//
// Usage: cat sample/seed_graph.cypher | docker exec -i $(docker ps -q -f name=neo4j) cypher-shell -u neo4j -p semantic-layer

// DataSource
MERGE (ds:DataSource {name: "ecommerce"})
SET ds.glue_database = "ecommerce_demo", ds.catalog_type = "glue";

// Tables
MERGE (t1:Table {full_name: "ecommerce.customers"})
SET t1.name = "customers", t1.database = "ecommerce", t1.description = "Customer master data with demographics and segmentation", t1.catalog_type = "glue";
MERGE (t2:Table {full_name: "ecommerce.orders"})
SET t2.name = "orders", t2.database = "ecommerce", t2.description = "Order transactions with status and amounts", t2.catalog_type = "glue";
MERGE (t3:Table {full_name: "ecommerce.products"})
SET t3.name = "products", t3.database = "ecommerce", t3.description = "Product catalog with pricing and categories", t3.catalog_type = "glue";
MERGE (t4:Table {full_name: "ecommerce.order_items"})
SET t4.name = "order_items", t4.database = "ecommerce", t4.description = "Line items for each order with quantity and pricing", t4.catalog_type = "glue";

// DataSource -> Tables
MATCH (ds:DataSource {name: "ecommerce"})
MATCH (t1:Table {full_name: "ecommerce.customers"})
MATCH (t2:Table {full_name: "ecommerce.orders"})
MATCH (t3:Table {full_name: "ecommerce.products"})
MATCH (t4:Table {full_name: "ecommerce.order_items"})
MERGE (ds)-[:CONTAINS]->(t1)
MERGE (ds)-[:CONTAINS]->(t2)
MERGE (ds)-[:CONTAINS]->(t3)
MERGE (ds)-[:CONTAINS]->(t4);

// Columns — customers
MATCH (t:Table {full_name: "ecommerce.customers"})
MERGE (c1:Column {name: "customer_id", table: "ecommerce.customers"}) SET c1.data_type = "int", c1.is_primary_key = true MERGE (t)-[:HAS_COLUMN]->(c1)
MERGE (c2:Column {name: "name", table: "ecommerce.customers"}) SET c2.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c2)
MERGE (c3:Column {name: "email", table: "ecommerce.customers"}) SET c3.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c3)
MERGE (c4:Column {name: "city", table: "ecommerce.customers"}) SET c4.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c4)
MERGE (c5:Column {name: "state", table: "ecommerce.customers"}) SET c5.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c5)
MERGE (c6:Column {name: "signup_date", table: "ecommerce.customers"}) SET c6.data_type = "date" MERGE (t)-[:HAS_COLUMN]->(c6)
MERGE (c7:Column {name: "segment", table: "ecommerce.customers"}) SET c7.data_type = "string", c7.description = "Customer segment: Enterprise, SMB, or Consumer" MERGE (t)-[:HAS_COLUMN]->(c7);

// Columns — orders
MATCH (t:Table {full_name: "ecommerce.orders"})
MERGE (c1:Column {name: "order_id", table: "ecommerce.orders"}) SET c1.data_type = "int", c1.is_primary_key = true MERGE (t)-[:HAS_COLUMN]->(c1)
MERGE (c2:Column {name: "customer_id", table: "ecommerce.orders"}) SET c2.data_type = "int" MERGE (t)-[:HAS_COLUMN]->(c2)
MERGE (c3:Column {name: "order_date", table: "ecommerce.orders"}) SET c3.data_type = "date" MERGE (t)-[:HAS_COLUMN]->(c3)
MERGE (c4:Column {name: "status", table: "ecommerce.orders"}) SET c4.data_type = "string", c4.description = "Order status: pending, completed, cancelled, shipped" MERGE (t)-[:HAS_COLUMN]->(c4)
MERGE (c5:Column {name: "total_amount", table: "ecommerce.orders"}) SET c5.data_type = "double" MERGE (t)-[:HAS_COLUMN]->(c5)
MERGE (c6:Column {name: "item_count", table: "ecommerce.orders"}) SET c6.data_type = "int" MERGE (t)-[:HAS_COLUMN]->(c6);

// Columns — products
MATCH (t:Table {full_name: "ecommerce.products"})
MERGE (c1:Column {name: "product_id", table: "ecommerce.products"}) SET c1.data_type = "int", c1.is_primary_key = true MERGE (t)-[:HAS_COLUMN]->(c1)
MERGE (c2:Column {name: "name", table: "ecommerce.products"}) SET c2.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c2)
MERGE (c3:Column {name: "category", table: "ecommerce.products"}) SET c3.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c3)
MERGE (c4:Column {name: "subcategory", table: "ecommerce.products"}) SET c4.data_type = "string" MERGE (t)-[:HAS_COLUMN]->(c4)
MERGE (c5:Column {name: "price", table: "ecommerce.products"}) SET c5.data_type = "double" MERGE (t)-[:HAS_COLUMN]->(c5)
MERGE (c6:Column {name: "cost", table: "ecommerce.products"}) SET c6.data_type = "double" MERGE (t)-[:HAS_COLUMN]->(c6);

// Columns — order_items
MATCH (t:Table {full_name: "ecommerce.order_items"})
MERGE (c1:Column {name: "order_item_id", table: "ecommerce.order_items"}) SET c1.data_type = "int", c1.is_primary_key = true MERGE (t)-[:HAS_COLUMN]->(c1)
MERGE (c2:Column {name: "order_id", table: "ecommerce.order_items"}) SET c2.data_type = "int" MERGE (t)-[:HAS_COLUMN]->(c2)
MERGE (c3:Column {name: "product_id", table: "ecommerce.order_items"}) SET c3.data_type = "int" MERGE (t)-[:HAS_COLUMN]->(c3)
MERGE (c4:Column {name: "quantity", table: "ecommerce.order_items"}) SET c4.data_type = "int" MERGE (t)-[:HAS_COLUMN]->(c4)
MERGE (c5:Column {name: "unit_price", table: "ecommerce.order_items"}) SET c5.data_type = "double" MERGE (t)-[:HAS_COLUMN]->(c5)
MERGE (c6:Column {name: "line_total", table: "ecommerce.order_items"}) SET c6.data_type = "double" MERGE (t)-[:HAS_COLUMN]->(c6);

// Join paths
MATCH (t1:Table {full_name: "ecommerce.orders"}), (t2:Table {full_name: "ecommerce.customers"})
MERGE (t1)-[:JOINS_TO {on_column: "customer_id", join_type: "INNER"}]->(t2);
MATCH (t1:Table {full_name: "ecommerce.orders"}), (t2:Table {full_name: "ecommerce.order_items"})
MERGE (t1)-[:JOINS_TO {on_column: "order_id", join_type: "INNER"}]->(t2);
MATCH (t1:Table {full_name: "ecommerce.order_items"}), (t2:Table {full_name: "ecommerce.products"})
MERGE (t1)-[:JOINS_TO {on_column: "product_id", join_type: "INNER"}]->(t2);

// Metrics
MERGE (m1:Metric {metric_id: "m_001"})
SET m1.name = "total_revenue", m1.definition = "Total dollar value of all completed orders",
    m1.expression = "SUM(total_amount)", m1.type = "simple",
    m1.filters = ["status != 'cancelled'"], m1.grain = ["order_date"],
    m1.synonyms = ["total sales", "revenue", "gross revenue"],
    m1.synonyms_text = "total sales revenue gross revenue",
    m1.source_table = "ecommerce.orders", m1.source = "sample";
MATCH (m1:Metric {metric_id: "m_001"}), (t:Table {full_name: "ecommerce.orders"})
MERGE (m1)-[:MEASURES]->(t);

MERGE (m2:Metric {metric_id: "m_002"})
SET m2.name = "average_order_value", m2.definition = "Average dollar value per completed order",
    m2.expression = "SUM(total_amount) / COUNT(DISTINCT order_id)", m2.type = "simple",
    m2.filters = ["status != 'cancelled'"], m2.grain = ["order_date"],
    m2.synonyms = ["AOV", "avg order", "average order"],
    m2.synonyms_text = "AOV avg order average order",
    m2.source_table = "ecommerce.orders", m2.source = "sample";
MATCH (m2:Metric {metric_id: "m_002"}), (t:Table {full_name: "ecommerce.orders"})
MERGE (m2)-[:MEASURES]->(t);

MERGE (m3:Metric {metric_id: "m_003"})
SET m3.name = "customer_count", m3.definition = "Count of distinct customers",
    m3.expression = "COUNT(DISTINCT customer_id)", m3.type = "simple",
    m3.synonyms = ["number of customers", "active customers"],
    m3.synonyms_text = "number of customers active customers",
    m3.source_table = "ecommerce.customers", m3.source = "sample";
MATCH (m3:Metric {metric_id: "m_003"}), (t:Table {full_name: "ecommerce.customers"})
MERGE (m3)-[:MEASURES]->(t);

MERGE (m4:Metric {metric_id: "m_004"})
SET m4.name = "order_count", m4.definition = "Total number of orders",
    m4.expression = "COUNT(DISTINCT order_id)", m4.type = "simple",
    m4.synonyms = ["number of orders", "total orders"],
    m4.synonyms_text = "number of orders total orders",
    m4.source_table = "ecommerce.orders", m4.source = "sample";
MATCH (m4:Metric {metric_id: "m_004"}), (t:Table {full_name: "ecommerce.orders"})
MERGE (m4)-[:MEASURES]->(t);

// Business terms
MERGE (bt1:BusinessTerm {name: "revenue"}) SET bt1.definition = "Total dollar value of completed orders";
MERGE (bt2:BusinessTerm {name: "aov"}) SET bt2.definition = "Average order value";
MERGE (bt3:BusinessTerm {name: "customer"}) SET bt3.definition = "A person who has placed at least one order";
MATCH (bt1:BusinessTerm {name: "revenue"}), (m:Metric {metric_id: "m_001"}) MERGE (bt1)-[:MAPS_TO]->(m);
MATCH (bt2:BusinessTerm {name: "aov"}), (m:Metric {metric_id: "m_002"}) MERGE (bt2)-[:MAPS_TO]->(m);

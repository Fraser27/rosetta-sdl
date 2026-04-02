# Rosetta SDL

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Neo4j 5](https://img.shields.io/badge/Neo4j-5_Community-008CC1.svg)](https://neo4j.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io/)

> **Translate business language into data insights.** An open-source semantic data layer that uses Neo4j as a unified ontology graph to bridge business terminology with technical schema — like the Rosetta Stone bridged ancient languages. One definition of "revenue". One source of truth. Every agent, dashboard, and notebook speaks the same language.

---

## The Problem

Your data lake has hundreds of tables across Glue, Iceberg, and S3. Multiple teams define "revenue" differently. AI agents hallucinate SQL against tables they shouldn't touch. Unstructured documents (PDFs, policies) live in a separate world from your structured data.

## The Solution

A **Neo4j knowledge graph** that unifies your entire data estate — tables, columns, metrics, join paths, business terms, and documents — into a single queryable ontology. AI agents discover data through the graph, execute governed metrics deterministically, and get their SQL validated by an AST-based firewall before it touches Athena.

```
"What was total revenue last quarter?"
  → Graph finds metric: total_revenue (SUM(total_amount) WHERE status != 'cancelled')
  → Deterministic SQL compilation (no LLM, no hallucination)
  → SQL Firewall validates table access
  → Athena executes → results returned

"What's the return policy for electronics?"
  → Graph routes to unstructured path
  → S3 Vectors semantic search
  → Relevant document chunks returned
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Clients                               │
│         Claude Code  /  QuickSuite  /  Strands SDK               │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP Protocol (stdio)
┌──────────────────────────▼──────────────────────────────────────┐
│              MCP Server (9 tools)                                 │
│         discover / query / metrics / search                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST API
┌──────────────────────────▼──────────────────────────────────────┐
│                     FastAPI Service                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  Catalog  │  │ Metrics  │  │  Query   │  │    Admin       │  │
│  │  Routes   │  │  CRUD    │  │  Router  │  │  Scan/Enrich   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘  │
│       │              │             │                 │            │
│  ┌────▼──────────────▼─────────────▼─────────────────▼────────┐  │
│  │                    Neo4j Knowledge Graph                    │  │
│  │                                                            │  │
│  │  (:DataSource)──[:CONTAINS]──▶(:Table)──[:HAS_COLUMN]──▶(:Column)
│  │       │                         │  ▲                         │
│  │       │              [:JOINS_TO]│  │[:MEASURES]              │
│  │       │                         ▼  │                         │
│  │  (:Document)               (:Table) (:Metric)               │
│  │       │                              │                       │
│  │  [:COVERS_CONCEPT]          [:MAPS_TO]                      │
│  │       ▼                        ▼                             │
│  │  (:Concept)◀──────────(:BusinessTerm)                       │
│  └────────────────────────────────────────────────────────────┘  │
│       │              │                    │                       │
│  ┌────▼─────┐  ┌────▼──────────┐  ┌─────▼────────────────┐     │
│  │ SQL      │  │ Metric        │  │ Query Router          │     │
│  │ Firewall │  │ Compiler      │  │ Structured/Unstructured│    │
│  │ (sqlglot)│  │ (deterministic)│  │ /Both                 │    │
│  └────┬─────┘  └────┬──────────┘  └──┬──────────┬────────┘     │
│       │              │                │          │               │
└───────┼──────────────┼────────────────┼──────────┼───────────────┘
        │              │                │          │
   ┌────▼──────────────▼────┐    ┌─────▼───┐  ┌──▼──────────┐
   │      Amazon Athena      │    │ Bedrock │  │ S3 Vectors  │
   │    (query execution)    │    │  (NL→SQL)│  │  (semantic  │
   └─────────────────────────┘    └─────────┘  │   search)   │
                                               └─────────────┘
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Graph-based Ontology** | Neo4j stores tables, columns, metrics, joins, business terms, and documents as a connected knowledge graph |
| **Governed Metrics** | Define business metrics once in YAML. Compiled to SQL deterministically — no LLM involved, no hallucination |
| **SQL Firewall** | sqlglot AST parsing validates every query. Extracts table refs from CTEs, subqueries, UNIONs. Fail-closed on parse errors |
| **Auto-Discovery** | Scans AWS Glue catalog and S3 Vector buckets. Populates the graph automatically |
| **LLM Enrichment** | Bedrock generates descriptions, extracts business terms and concepts, links documents to tables |
| **Dual Query Routing** | Graph traversal decides: structured question → Athena, unstructured → S3 Vectors, cross-system → both |
| **Ad-hoc SQL Generation** | For questions without a matching metric, LLM generates SQL grounded in the real schema from the graph |
| **MCP Integration** | 9 MCP tools for Claude Code, QuickSuite, Strands SDK, and Bedrock AgentCore |
| **Plan Mode** | `plan_query` returns SQL/search params without executing — agents can delegate execution to external Athena/S3Vectors MCP servers |
| **React Admin UI** | Visual dashboard, table browser, metric CRUD, interactive force-directed graph explorer, light/dark mode |
| **Cognito Auth** | JWT-based authentication. Middleware validates tokens on every API call. Disabled in local dev |
| **Domain-Agnostic** | Point it at any data lake. Provide `config.yaml` + `metrics.yaml`. The framework handles the rest |

---

## Quick Start (Local — 5 minutes)

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local dev)
- Node.js 18+ (for the React UI)

### 1. Start the services

```bash
git clone https://github.com/Fraser27/rosetta-sdl.git
cd rosetta-sdl

# Start Neo4j + FastAPI
docker-compose up -d
```

Wait ~30 seconds for Neo4j to become healthy, then verify:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# {"status": "healthy", "neo4j": "connected", ...}
```

### 2. Seed the demo graph

Load a sample ecommerce ontology (4 tables, 37 nodes, 38 edges):

```bash
cat sample/seed_graph.cypher | docker exec -i \
  $(docker ps -q -f name=neo4j) cypher-shell -u neo4j -p semantic-layer
```

### 3. Verify it works

```bash
# 4 tables
curl -s http://localhost:8000/catalog/tables | python3 -m json.tool

# 4 governed metrics
curl -s http://localhost:8000/metrics | python3 -m json.tool

# Full-text search
curl -s "http://localhost:8000/catalog/search?q=revenue" | python3 -m json.tool

# Graph summary
curl -s http://localhost:8000/catalog/graph | python3 -m json.tool
```

### 4. Launch the Admin UI

```bash
cd ui
npm install
npm run dev
# Open http://localhost:3000
```

### 5. Connect your AI agent via MCP

Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "rosetta-sdl": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "env": { "API_URL": "http://localhost:8000" }
    }
  }
}
```

Then ask:

```
> discover data assets about revenue
> what is total revenue by customer segment?
> show me the schema for the orders table
```

---

## Connect to Your Data Lake

### Scan AWS Glue + S3 Vectors

```bash
# Set your data sources
export GLUE_DATABASES=my_database,another_db
export VECTOR_BUCKETS=my-vector-bucket
export ATHENA_WORKGROUP=my-workgroup
export ATHENA_OUTPUT_BUCKET=s3://my-bucket/athena-results/

# Restart to pick up config
docker-compose up -d

# Auto-discover tables, columns, and documents
curl -s -X POST http://localhost:8000/admin/scan | python3 -m json.tool

# LLM-enrich metadata (descriptions, business terms, concepts)
curl -s -X POST http://localhost:8000/admin/enrich | python3 -m json.tool
```

### Define Your Metrics

Create `metrics.yaml`:

```yaml
version: "1.0"
metrics:
  - metric_id: m_001
    name: total_revenue
    synonyms: ["total sales", "revenue", "gross revenue"]
    definition: "Total dollar value of all completed orders"
    type: simple
    expression: "SUM(total_amount)"
    source_table: "ecommerce.orders"
    filters: ["status != 'cancelled'"]
    grain: ["order_date"]
    time_grains: ["day", "week", "month", "quarter", "year"]

  - metric_id: m_002
    name: average_order_value
    synonyms: ["AOV", "avg order"]
    definition: "Average dollar value per completed order"
    type: derived
    expression: "SUM(total_amount) / COUNT(DISTINCT order_id)"
    source_table: "ecommerce.orders"
    grain: ["order_date"]

join_paths:
  - source: "ecommerce.orders"
    target: "ecommerce.customers"
    on: "customer_id"
  - source: "ecommerce.orders"
    target: "ecommerce.order_items"
    on: "order_id"
```

Or create metrics visually in the Admin UI at `/metrics`.

---

## Deploy to AWS

### Architecture

```
                    Internet
                       |
              +--------v--------+
              |   CloudFront    |
              |  UI (S3) + API  |
              +---+----------+--+
                  |          |
         S3 (UI) |          | /api/*
                  |    +-----v--------+
                  |    |     ALB      |  <-- Public subnet
                  |    | (HTTP:80)    |
                  |    +-----+-------+
                  |          |
            ------+----------+-------- Private subnet ---
                  |    +-----v--------------+
                  |    |       EC2           |
                  |    |  FastAPI + Neo4j    |
                  |    |  (Docker Compose)   |
                  |    +--------------------+
```

| Component | Service | Spec |
|-----------|---------|------|
| **React UI** | S3 + CloudFront | CDN, HTTPS, SPA routing |
| **API Gateway** | ALB | Public subnet, forwards to EC2:8000 |
| **FastAPI + Neo4j** | EC2 (private subnet) | t4g.medium (ARM64), 30GB gp3 EBS, Docker Compose |
| **Auth** | Cognito | Hosted UI, email sign-in, JWT tokens |
| **Access** | SSM | No SSH keys, no public IP - `aws ssm start-session` |

### Deploy with CDK

```bash
cd cdk
npm install

# Bootstrap CDK (first time only)
npx cdk bootstrap

# Deploy everything
npx cdk deploy
```

CDK outputs:

```
RosettaSdlStack.CloudFrontUrl = https://xxxxx.cloudfront.net
RosettaSdlStack.AlbDnsName = Rosett-Alb-xxxxx.us-east-1.elb.amazonaws.com
RosettaSdlStack.CognitoUserPoolId = us-east-1_XXXXXXX
RosettaSdlStack.CognitoClientId = xxxxxxxxxxxxxxxxx
RosettaSdlStack.CognitoDomain = https://semantic-layer-xxxx.auth.us-east-1.amazoncognito.com
RosettaSdlStack.SsmCommand = aws ssm start-session --target i-xxxxxxxxx
```

### Post-deploy: Fix Cognito settings

CDK may not correctly apply self-signup and email verification settings. Run these commands after deployment using the `CognitoUserPoolId` from the CDK outputs:

```bash
# Enable self-signup (allow users to register)
aws cognito-idp update-user-pool \
  --user-pool-id <CognitoUserPoolId> \
  --admin-create-user-config AllowAdminCreateUserOnly=false

# Enable email auto-verification (required for signup flow)
aws cognito-idp update-user-pool \
  --user-pool-id <CognitoUserPoolId> \
  --auto-verified-attributes email
```

Verify the settings:

```bash
aws cognito-idp describe-user-pool \
  --user-pool-id <CognitoUserPoolId> \
  --query 'UserPool.{AllowAdminCreateUserOnly:AdminCreateUserConfig.AllowAdminCreateUserOnly,AutoVerifiedAttributes:AutoVerifiedAttributes}'
# Expected: {"AllowAdminCreateUserOnly": false, "AutoVerifiedAttributes": ["email"]}
```

### Post-deploy: Seed demo data

```bash
# Connect to EC2 via SSM
aws ssm start-session --target <instance-id>

# Seed the demo graph
cd /opt/semantic-layer
cat sample/seed_graph.cypher | docker exec -i \
  $(docker ps -q -f name=neo4j) cypher-shell -u neo4j -p semantic-layer
```

---

## Deploy to AgentCore

Expose Rosetta SDL as an MCP server on **Amazon Bedrock AgentCore**, enabling any AI agent to use it via the MCP protocol with full Cognito authentication.

### Architecture

```
+---------------------+
|   Any AI Agent      |
| (Claude, Strands,   |
|  QuickSuite)        |
+----------+----------+
           | JWT Auth (Cognito)
           v
+---------------------+
| AgentCore Gateway   |
| (MCP Protocol)      |
+----------+----------+
           | OAuth2 (Cognito M2M)
           v
+---------------------+
| AgentCore Runtime   |
| Rosetta SDL MCP     |
| (9 tools)           |
+----------+----------+
           | HTTP
           v
+---------------------+
| EC2 (FastAPI+Neo4j) |
+---------------------+
```

### Option A: Deploy Script

```bash
cd agentcore
pip install bedrock-agentcore-starter-toolkit

# Interactive (step-by-step with pauses)
python deploy_agent.py

# Non-interactive (automated)
python deploy_agent.py --non-interactive
```

The script auto-discovers the ALB DNS from CloudFormation stack outputs.

### Option B: Jupyter Notebook

```bash
cd agentcore
jupyter notebook deploy_to_agentcore.ipynb
```

Walk through each step with explanations — ideal for workshops and learning.

### What Gets Created

| Resource | Purpose |
|----------|---------|
| Gateway IAM Role | Allows Gateway to invoke Runtime |
| Runtime IAM Role | Allows MCP container to run |
| Gateway Cognito Pool | Inbound JWT auth for clients |
| Runtime Cognito Pool | Outbound OAuth2 auth (Gateway → Runtime) |
| AgentCore Gateway | MCP protocol entry point |
| AgentCore Runtime | Rosetta SDL MCP server (Docker container) |
| OAuth2 Credential Provider | Links Gateway auth to Runtime auth |
| Gateway Target | Connects Gateway to Runtime endpoint |

---

## Neo4j Graph Schema

### Nodes

| Label | Key Properties | Description |
|-------|---------------|-------------|
| `DataSource` | `name`, `glue_database`, `catalog_type` | Glue database |
| `Table` | `full_name`, `name`, `database`, `description` | Data lake table |
| `Column` | `name`, `data_type`, `table` | Table column |
| `Metric` | `metric_id`, `name`, `expression`, `synonyms` | Governed business metric |
| `BusinessTerm` | `name`, `definition`, `synonyms` | Business vocabulary |
| `Document` | `name`, `s3_key`, `vector_bucket` | Unstructured document |
| `MetadataKey` | `name`, `data_type`, `document` | Queryable metadata attribute on a document (excl. embeddings) |
| `Concept` | `name`, `definition` | Business concept from documents |

### Edges

| Edge | From | To | Properties |
|------|------|----|-----------|
| `CONTAINS` | DataSource | Table | |
| `HAS_COLUMN` | Table | Column | |
| `JOINS_TO` | Table | Table | `on_column`, `join_type` |
| `MEASURES` | Metric | Table | |
| `USES_COLUMN` | Metric | Column | |
| `MAPS_TO` | BusinessTerm | Metric/Column | |
| `RELATES_TO` | Document | Table | |
| `HAS_METADATA_KEY` | Document | MetadataKey | |
| `COVERS_CONCEPT` | Document | Concept | |

### Explore in Neo4j Browser

Open `http://localhost:7474` (login: `neo4j` / `semantic-layer`):

```cypher
-- Full ontology visualization
MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 200

-- Tables and their columns
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column) RETURN t.name, collect(c.name)

-- Metrics and what they measure
MATCH (m:Metric)-[:MEASURES]->(t:Table) RETURN m.name, m.expression, t.full_name

-- Join paths between tables
MATCH (t1:Table)-[j:JOINS_TO]->(t2:Table) RETURN t1.name, j.on_column, t2.name

-- Full-text search for revenue
CALL db.index.fulltext.queryNodes('metric_search', 'revenue') YIELD node, score
RETURN node.name, node.definition, score
```

---

## API Reference

### Catalog

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health + Neo4j connectivity |
| `GET` | `/catalog/tables` | List all tables |
| `GET` | `/catalog/tables/{name}` | Table schema with columns and joins |
| `GET` | `/catalog/tables/{name}/related` | Tables joinable to this table |
| `GET` | `/catalog/search?q=` | Full-text search across all node types |
| `GET` | `/catalog/graph` | Node/edge counts by type |
| `GET` | `/catalog/graph/data` | All nodes and edges for visualization |

### Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metrics` | List all governed metrics |
| `GET` | `/metrics/{id}` | Full metric definition |
| `POST` | `/metrics` | Create a new metric |
| `PUT` | `/metrics/{id}` | Update a metric |
| `DELETE` | `/metrics/{id}` | Delete a metric |
| `POST` | `/metrics/{id}/query` | Execute metric with dimensions/filters |
| `POST` | `/metrics/{id}/compile` | Compile metric to SQL without executing |

### Query

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query/natural-language` | Full NL query pipeline (route → compile → firewall → execute) |
| `POST` | `/query/plan` | Plan-only: returns SQL + vector search params without executing |
| `POST` | `/query/compose` | Compose multiple metrics into a CTE query, optionally execute |
| `POST` | `/query/sql` | Direct SQL execution with firewall validation |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/scan` | Scan Glue databases + S3 Vector buckets → populate graph |
| `POST` | `/admin/enrich` | LLM-enrich metadata (descriptions, terms, concepts) |
| `POST` | `/admin/clear` | Clear all nodes and edges from the graph |

---

## MCP Tools

9 tools exposed via the [Model Context Protocol](https://modelcontextprotocol.io/) for AI agent integration:

| Tool | Description |
|------|-------------|
| `discover_data_assets` | Full-text search across tables, metrics, documents. **Start here.** |
| `get_table_details` | Full table schema — columns, types, descriptions, join paths |
| `find_join_path` | Shortest join path between two tables |
| `list_metrics` | All governed metrics with expressions and synonyms |
| `get_metric_definition` | Full metric details including filters, grain, and source table |
| `query_metric` | Execute a governed metric with dimensions and filters |
| `execute_query` | Natural language query — auto-routes to Athena or S3 Vectors |
| `search_documents` | Semantic search over unstructured documents |
| `plan_query` | **Plan-only mode** — returns SQL/search params without executing (for delegation to external MCP servers) |

### Execute vs. Plan

Rosetta SDL supports two execution modes:

| Mode | Tool | Who executes? | Use case |
|------|------|---------------|----------|
| **Execute** | `execute_query` | Rosetta (internally via Athena/S3 Vectors) | Standalone deployment |
| **Plan** | `plan_query` | Agent delegates to external Athena/S3Vectors MCP servers | Multi-gateway architecture |

**Plan mode** is useful when you already have Athena and S3Vectors MCP servers deployed on a separate AgentCore Gateway. The agent asks Rosetta for the governed SQL, then passes it to the execution MCPs:

```
Agent: "What was total revenue?"
  |
  |--1--> Rosetta SDL: plan_query("What was total revenue?")
  |       <-- SQL: SELECT SUM(total_amount) FROM ecommerce.orders WHERE ...
  |           Intent: metric (governed, deterministic)
  |           Tables: [ecommerce.orders]
  |
  |--2--> Athena MCP: execute_query(sql)
  |       <-- Results: [{total_revenue: 1234567.89}]
  |
  |--3--> Agent synthesizes answer
```

---

## How It Works

### Query Flow

```
User: "What was total revenue last quarter?"
                    │
           ┌────────▼─────────┐
           │   Query Router    │  Full-text search across graph
           │   (graph-based)   │  indexes → finds metric m_001
           └────────┬─────────┘
                    │ metric match
           ┌────────▼─────────┐
           │  Metric Compiler  │  Deterministic SQL from graph
           │  (no LLM needed)  │  properties — fast, cheap,
           └────────┬─────────┘  reproducible
                    │
           ┌────────▼─────────┐
           │   SQL Firewall    │  sqlglot AST validates all
           │   (fail-closed)   │  table references
           └────────┬─────────┘
                    │
           ┌────────▼─────────┐
           │  Athena Executor  │  Execute validated SQL
           └────────┬─────────┘
                    │
                 Results
```

### Governed vs. Ad-hoc Queries

| | Governed Metric | Ad-hoc Query |
|---|---|---|
| **Trigger** | Question matches a metric name/synonym | No metric match |
| **SQL Source** | Deterministic compilation from graph | LLM generates SQL grounded in schema from graph |
| **LLM Used?** | No | Yes (Bedrock) |
| **Firewall** | Yes | Yes |
| **Speed** | Fast (no LLM latency) | Slower (LLM call) |
| **Reproducible** | Always same SQL | May vary |

---

## Testing

### Unit tests (no AWS or Neo4j needed)

```bash
pip install ".[dev]"
python -m pytest tests/unit -v
```

**30 tests** covering:

| Module | Tests | What's Tested |
|--------|-------|--------------|
| **Compiler** | 10 | Simple/derived metrics, multi-table, filters, dimensions, limits, order_by, not-found |
| **Firewall** | 9 | Allowed/denied tables, subqueries, CTEs, UNIONs, fail-closed on bad SQL |
| **Router** | 5 | Structured/unstructured/both routing, default fallback |
| **Disambiguator** | 6 | Metric matching, table matching, column matching, join paths, confidence scores |

### Smoke test (requires running services)

```bash
docker-compose up -d
cat sample/seed_graph.cypher | docker exec -i $(docker ps -q -f name=neo4j) cypher-shell -u neo4j -p semantic-layer

# Run smoke tests
make smoke-test
```

### Manual verification checklist

| Test | Command | Expected |
|------|---------|----------|
| Health | `curl localhost:8000/health` | `"neo4j": "connected"` |
| Tables | `curl localhost:8000/catalog/tables` | 4 tables |
| Metrics | `curl localhost:8000/metrics` | 4 metrics |
| Search | `curl "localhost:8000/catalog/search?q=revenue"` | Results with scores |
| Graph | `curl localhost:8000/catalog/graph` | Node counts > 0 |
| Table detail | `curl localhost:8000/catalog/tables/ecommerce.orders` | Columns + joins |
| Metric detail | `curl localhost:8000/metrics/m_001` | Expression + source |
| Create metric | `curl -X POST localhost:8000/metrics -d '...'` | `{"ok": true}` |
| Firewall | `curl -X POST localhost:8000/query/sql -d '{"sql":"SELECT * FROM admin.secrets"}'` | 403 |

---

## Project Structure

```
rosetta-sdl/
├── src/
│   ├── main.py                  # FastAPI app + Mangum handler
│   ├── config.py                # YAML + env config loader
│   ├── auth.py                  # Cognito JWT middleware
│   ├── graph/
│   │   ├── client.py            # Neo4j driver wrapper
│   │   ├── schema.py            # Constraints + full-text indexes
│   │   ├── queries.py           # Parameterized Cypher templates
│   │   └── loader.py            # Bulk graph population
│   ├── catalog/
│   │   └── models.py            # Pydantic models
│   ├── discovery/
│   │   ├── glue_scanner.py      # AWS Glue → TableMeta
│   │   ├── s3vectors_scanner.py # S3 Vectors → DocumentMeta
│   │   └── enrichment.py        # Bedrock LLM enrichment
│   ├── metrics/
│   │   ├── loader.py            # YAML → Metric nodes
│   │   └── compiler.py          # Deterministic SQL compilation
│   ├── query/
│   │   ├── router.py            # Graph-based query routing
│   │   ├── disambiguator.py     # Business term → schema resolution
│   │   ├── generator.py         # LLM SQL generation
│   │   ├── firewall.py          # sqlglot AST validation
│   │   ├── athena_executor.py   # Athena query execution
│   │   └── vectors_executor.py  # S3 Vectors semantic search
│   ├── api/
│   │   ├── routes_catalog.py    # Catalog endpoints
│   │   ├── routes_metrics.py    # Metrics CRUD + query
│   │   ├── routes_query.py      # NL query + SQL execution
│   │   └── routes_admin.py      # Scan, enrich, clear
│   └── mcp/
│       └── server.py            # MCP adapter (9 tools, stdio transport)
├── agentcore/                   # AgentCore deployment
│   ├── rosetta_mcp.py           # MCP server for AgentCore Runtime (streamable-http)
│   ├── deploy_agent.py          # Standalone deploy script (interactive/non-interactive)
│   ├── deploy_to_agentcore.ipynb# Workshop notebook (step-by-step)
│   ├── ac_utils.py              # IAM roles, Cognito setup helpers
│   └── requirements.txt         # MCP + httpx dependencies
├── ui/                          # React admin UI
│   ├── src/
│   │   ├── App.tsx              # Layout + auth + routing
│   │   ├── api.ts               # API client with JWT
│   │   ├── auth.ts              # Cognito auth helpers
│   │   └── pages/
│   │       ├── Dashboard.tsx    # Overview stats
│   │       ├── Tables.tsx       # Table browser + search
│   │       ├── TableDetail.tsx  # Column/join details
│   │       ├── Metrics.tsx      # CRUD for governed metrics
│   │       ├── QueryBuilder.tsx # No-code multi-metric composer
│   │       ├── GraphExplorer.tsx# Force-directed graph viz
│   │       ├── Admin.tsx        # Scan/enrich/clear
│   │       └── Login.tsx        # Cognito login
│   └── vite.config.ts           # Dev proxy to FastAPI
├── cdk/                         # AWS CDK infrastructure
│   └── lib/
│       └── rosetta-sdl-stack.ts     # VPC + EC2 + ALB + CloudFront + S3 + Cognito
├── sample/
│   ├── config.yaml              # Sample configuration
│   ├── metrics.yaml             # 6 sample metrics + join paths
│   └── seed_graph.cypher        # Demo ecommerce ontology
├── tests/unit/                  # 30 unit tests
├── docker-compose.yml           # Neo4j + FastAPI
├── Dockerfile                   # FastAPI service
├── Dockerfile.ui                # React UI (nginx)
└── pyproject.toml
```

---

## Configuration

### `config.yaml`

```yaml
neo4j:
  uri: "bolt://localhost:7687"
  user: "neo4j"
  password: "semantic-layer"

databases:
  - name: "ecommerce"
    glue_database: "ecommerce_demo"
    catalog_type: "glue"              # glue | iceberg | federated

vector_buckets:
  - name: "company-docs"
    bucket: "my-vector-bucket"

athena:
  workgroup: "semantic-layer-wg"
  output_bucket: "s3://my-bucket/athena-results/"

metrics_file: "metrics.yaml"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEO4J_URI` | Neo4j bolt connection URI | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | `semantic-layer` |
| `GLUE_DATABASES` | Comma-separated Glue databases to scan | |
| `VECTOR_BUCKETS` | Comma-separated S3 Vector bucket names | |
| `ATHENA_WORKGROUP` | Athena workgroup name | `primary` |
| `ATHENA_OUTPUT_BUCKET` | S3 path for Athena query results | |
| `METRICS_FILE` | Path to metrics YAML file | `metrics.yaml` |
| `BEDROCK_QUERY_MODEL` | Bedrock model for NL→SQL | `anthropic.claude-sonnet-4-20250514` |
| `BEDROCK_ENRICHMENT_MODEL` | Bedrock model for metadata enrichment | `anthropic.claude-haiku-4-5-20251001` |
| `COGNITO_USER_POOL_ID` | Cognito pool ID (empty = auth disabled) | |
| `COGNITO_REGION` | Cognito region | `us-east-1` |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Neo4j as the ontology** | The graph IS the semantic layer. No separate metadata store. Relationships are first-class citizens, not foreign keys in a RDBMS. |
| **Neo4j Community Edition** | Open source, single instance. A semantic layer stores metadata (~10K-100K nodes), not OLTP data. Single instance handles this easily. |
| **Deterministic metric compilation** | Governed metrics produce identical SQL every time. No LLM variance, no hallucination, no cost per query. LLM is only used for ad-hoc analytical queries. |
| **SQL firewall via AST parsing** | sqlglot parses SQL into an AST and extracts every table reference — including CTEs, subqueries, and UNIONs. Fail-closed on parse errors means malformed SQL never reaches Athena. |
| **Graph-based routing** | Full-text indexes across all node types decide whether a question is structured, unstructured, or both. No hardcoded rules. |
| **FastAPI on EC2** | Portable. `docker-compose up` works on any machine. SSM access, no SSH keys. Move to ECS/Lambda later if needed. |
| **CloudFront + S3 for UI** | CDN-hosted React SPA with HTTPS. `/api/*` routes to ALB, everything else to S3. Zero server management. |
| **Cognito auth** | JWT tokens validated in FastAPI middleware. Disabled when `COGNITO_USER_POOL_ID` is empty, so local dev requires zero auth setup. |
| **MCP over REST** | MCP server is a thin HTTP client that translates tool calls to REST API calls. Deployed locally (Claude Code) or in AgentCore. No separate deployment needed. |

---

## Security

- **SQL Firewall**: Every query is AST-parsed. Only whitelisted tables are accessible. DDL/DML statements are blocked.
- **Cognito JWT**: Access tokens validated on every API call (when enabled). Token expiry enforced.
- **IAM Roles**: EC2 instance role follows least-privilege — Glue read, Athena execute, S3 read/write, Bedrock invoke.
- **No SSH Keys**: EC2 access via SSM Session Manager only.
- **Encrypted EBS**: 30GB gp3 volume with encryption at rest.
- **Network**: Security group restricts inbound to ports 8000, 7474, 22.

---

## Operations & Troubleshooting

### EC2 Access via SSM

```bash
# Connect to the EC2 instance (no SSH keys needed)
aws ssm start-session --target <instance-id>

# Navigate to the project
cd /opt/semantic-layer
```

### Docker Logs

```bash
# View recent logs (last 100 lines)
docker logs semantic-layer-rosetta-1 --tail 100

# Follow logs in real-time
docker logs semantic-layer-rosetta-1 -f

# View Neo4j logs
docker logs semantic-layer-neo4j-1 --tail 100

# List running containers
docker ps

# Restart services
docker-compose restart
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| 500 on `/api/metrics` | Pydantic validation error (null fields from Neo4j) | Check `source_table` COALESCE in `queries.py` |
| 503 on any API | Graph client not initialized | Check Neo4j container is running |
| 401 Unauthorized | Expired or missing JWT token | Re-login via Cognito hosted UI |
| Metrics return empty | Graph not seeded | Run `POST /admin/scan` or seed demo data |

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`python -m pytest tests/unit -v`)
4. Commit your changes
5. Push to the branch and open a Pull Request

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Acknowledgements

Built with [Neo4j](https://neo4j.com/), [FastAPI](https://fastapi.tiangolo.com/), [sqlglot](https://github.com/tobymao/sqlglot), [React](https://react.dev/), and [AWS CDK](https://aws.amazon.com/cdk/).

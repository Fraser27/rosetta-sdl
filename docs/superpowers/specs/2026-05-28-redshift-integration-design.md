# Redshift Serverless Integration Design

**Date:** 2026-05-28
**Status:** Approved

## Overview

Integrate Amazon Redshift Serverless as a datasource into Rosetta SDL, enabling metrics to target either Athena or Redshift for query execution. Introduces an executor plugin architecture, dedicated datasource management UI, health polling with auto-disable/re-enable of metrics, and async test-connection jobs.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Datasource binding | Per-metric | Each metric declares its target datasource via `EXECUTES_ON` relationship |
| UI approach | Dedicated Datasources page | Clear separation from Admin; first-class concern |
| Connection loss handling | Auto-disable metrics | Metrics flagged `disabled` when datasource unhealthy; re-enabled on recovery |
| Test-connection pattern | Async job with polling | Matches existing enrichment job pattern; handles slow connections |
| Credential storage | Secrets Manager + graph metadata | Secrets never in graph/config; graph holds routing metadata |
| Health monitoring | Background polling task (30s) | Keeps status fresh; configurable interval |
| Executor architecture | Plugin pattern | Clean extensibility for future datasources |

---

## 1. Executor Plugin Architecture

### Interface

```python
class BaseExecutor(ABC):
    datasource_type: str  # "athena", "redshift_serverless"

    @abstractmethod
    async def execute(self, sql: str, max_rows: int = 500) -> ExecutionResult: ...

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult: ...

    @abstractmethod
    async def health_check(self) -> HealthStatus: ...
```

### Executor Registry

```python
class ExecutorRegistry:
    _executors: dict[str, BaseExecutor]  # keyed by datasource_id

    def register(self, datasource_id: str, executor: BaseExecutor): ...
    def get(self, datasource_id: str) -> BaseExecutor: ...
    def remove(self, datasource_id: str): ...
```

Initialized at app startup from DataSource nodes in Neo4j. When a datasource is added/edited via the API, the registry is updated in-place (no restart needed).

### Query Flow Change

```
Before:  compile_metric() -> athena_executor.execute(sql)
After:   compile_metric() -> registry.get(metric.datasource_id).execute(sql)
```

### Redshift Serverless Executor

Uses `boto3` `redshift-data` API:
- `execute_statement()` to submit SQL
- `describe_statement()` to poll for completion
- `get_statement_result()` to fetch rows
- Auth via IAM (workgroup-based, no password needed) or username/password via Secrets Manager

### File Structure

```
src/executors/
  base.py          # BaseExecutor ABC + ExecutionResult models
  registry.py      # ExecutorRegistry singleton
  athena.py        # Existing logic refactored from athena_executor.py
  redshift.py      # New Redshift Serverless executor
```

---

## 2. DataSource Graph Model & Storage

### Neo4j Node Schema

```cypher
(:DataSource {
    datasource_id: "ds_001",
    name: "Sales Redshift",
    type: "redshift_serverless",
    endpoint: "sales-workgroup",
    database: "sales_db",
    region: "us-east-1",
    secret_arn: "arn:aws:secretsmanager:...",
    status: "healthy",
    last_health_check: datetime,
    enabled: true,
    created_at: datetime
})
```

### Relationships

```cypher
(:Metric)-[:EXECUTES_ON]->(:DataSource)
(:Table)-[:RESIDES_IN]->(:DataSource)
```

The existing `(:Metric)-[:MEASURES]->(:Table)` remains. `EXECUTES_ON` is the explicit binding the compiler uses to resolve which executor to invoke.

### Secrets Manager Contract

Two auth modes for Redshift Serverless:
- **IAM auth (recommended):** No secret needed. Executor uses app's IAM role + workgroup name.
- **Username/password:** Secret in Secrets Manager with structure `{"username": "...", "password": "..."}`. The `secret_arn` on the DataSource node references it.

Backend fetches & caches secrets with a 5-minute TTL.

### Migration from Current State

Existing Athena config becomes a default DataSource node seeded on first startup:
```cypher
(:DataSource {datasource_id: "ds_default_athena", type: "athena", name: "Default Athena", ...})
```

Existing metrics without a `datasource_id` are automatically bound to this default node.

---

## 3. Health Polling & Metric Auto-Disable

### Background Health Poller

```python
class HealthPoller:
    interval: int = 30  # seconds, configurable
    _running: bool = False

    async def start(self):
        """Launched in FastAPI lifespan event"""
        while self._running:
            for datasource_id, executor in registry.items():
                status = await executor.health_check()
                await self._update_status(datasource_id, status)
            await asyncio.sleep(self.interval)

    async def _update_status(self, datasource_id, status):
        previous = self._cached_status.get(datasource_id)
        if previous != status:
            graph.update_datasource_status(datasource_id, status)
            if status == "unhealthy":
                graph.disable_metrics_for_datasource(datasource_id)
            elif status == "healthy":
                graph.enable_metrics_for_datasource(datasource_id)
```

### Metric Enable/Disable Cypher

```cypher
-- Disable all metrics bound to unhealthy datasource
MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $ds_id})
SET m.enabled = false, m.disabled_reason = "datasource_unhealthy"

-- Re-enable when datasource recovers (only poller-disabled ones)
MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $ds_id})
WHERE m.disabled_reason = "datasource_unhealthy"
SET m.enabled = true, m.disabled_reason = null
```

Only metrics disabled by the poller (`disabled_reason = "datasource_unhealthy"`) are re-enabled. Manually disabled metrics are untouched.

### Flapping Protection

```python
FAILURE_THRESHOLD = 3  # consecutive failures before marking unhealthy

async def _update_status(self, datasource_id, status):
    if status == "unhealthy":
        self._failure_counts[datasource_id] += 1
        if self._failure_counts[datasource_id] < FAILURE_THRESHOLD:
            return  # don't toggle yet
    else:
        self._failure_counts[datasource_id] = 0
    # proceed with status update...
```

### MCP Layer Short-Circuit

In `src/mcp/server.py`, before forwarding `execute_query`:

```python
if resolved_metric and not resolved_metric.enabled:
    return {"error": f"Metric '{metric_name}' is currently unavailable - datasource offline",
            "disabled_reason": resolved_metric.disabled_reason}
```

### Health Check Implementation (Redshift)

```python
async def health_check(self) -> HealthStatus:
    try:
        response = client.execute_statement(
            WorkgroupName=self.workgroup, Database=self.database, Sql="SELECT 1"
        )
        # Poll with short timeout (5s)
        return "healthy"
    except Exception:
        return "unhealthy"
```

---

## 4. Test-Connection Async Job

### API Endpoints

```
POST /datasources/{datasource_id}/test       -> { job_id: "tc_abc123" }
GET  /datasources/{datasource_id}/test/{job_id} -> { status, steps[], error? }
```

### Job Model

```python
@dataclass
class ConnectionTestJob:
    job_id: str
    datasource_id: str
    status: str  # "pending" | "running" | "success" | "failed"
    steps: list[ConnectionTestStep]
    error: str | None
    started_at: datetime
    completed_at: datetime | None

@dataclass
class ConnectionTestStep:
    name: str       # "resolve_endpoint", "authenticate", "execute_probe", "verify_permissions"
    status: str     # "pending" | "running" | "success" | "failed"
    duration_ms: int | None
    error: str | None
```

### Execution Steps (Redshift Serverless)

1. **resolve_endpoint** - Validate workgroup exists via `describe_workgroup()`
2. **authenticate** - Fetch secret (if password auth) or validate IAM role
3. **execute_probe** - Run `SELECT 1` via `execute_statement()`
4. **verify_permissions** - Run `SELECT * FROM information_schema.tables LIMIT 1`

### Storage

Jobs stored in-memory with 10-minute TTL expiry. Ephemeral diagnostic operations don't need persistence.

### UI Polling

Frontend polls every 2 seconds until terminal state. Displays step-by-step progress with durations.

---

## 5. Datasource CRUD API

### Endpoints

```
GET    /datasources                     -> list all datasources with status
POST   /datasources                     -> create new datasource
GET    /datasources/{id}                -> get details (sans secrets)
PUT    /datasources/{id}                -> update connection details
DELETE /datasources/{id}                -> remove (fails if metrics bound)
GET    /datasources/{id}/metrics        -> list metrics bound to this datasource
POST   /datasources/{id}/test           -> trigger test-connection job
GET    /datasources/{id}/test/{job_id}  -> poll test-connection status
```

### Request Body

```python
@dataclass
class DataSourceRequest:
    name: str
    type: str                    # "athena" | "redshift_serverless"
    endpoint: str                # workgroup name
    database: str
    region: str = "us-east-1"
    secret_arn: str | None       # optional, for password auth
```

### Routing at Query Time

```
execute_query(question)
  -> router.route_query(question)
  -> disambiguator resolves metric
  -> compiler.compile_metric(metric_id)
  -> graph lookup: (metric)-[:EXECUTES_ON]->(datasource)
  -> registry.get(datasource.datasource_id)
  -> executor.execute(sql)
```

If no `EXECUTES_ON` relationship exists (legacy metrics), falls back to the default Athena datasource.

### Delete Protection

Cannot delete a datasource with bound metrics. Returns 409 with count of bound metrics.

---

## 6. Frontend - Datasources Page

### Page Layout

- Card-per-datasource showing: name, type, endpoint, database, metric count, status indicator, last health check time
- Status indicators: green (healthy), red (unhealthy with disabled metric count), grey (unknown)
- Actions per card: Test, Edit, Delete
- Top-level "Add New" button

### Add/Edit Modal

Fields: Name, Type (dropdown), Workgroup, Database, Region (dropdown), Secret ARN (optional)
Actions: Cancel, Save & Test

### Test Connection Progress Modal

Displays step-by-step progress:
- Each step shows name, status icon, and duration when complete
- Poll every 2s until terminal state
- Cancel button available

### Metrics Form Change

New required "Datasource" dropdown field populated from `GET /datasources`. Existing metrics default to "Default Athena".

### Auto-refresh

Datasources list polls `GET /datasources` every 10 seconds to reflect health changes.

---

## 7. Error Handling & Edge Cases

| Scenario | Behavior |
|----------|----------|
| Datasource goes down mid-poll | Next health check (<=30s) marks unhealthy, disables bound metrics |
| Datasource goes down mid-query | Executor catches error, returns structured error. Next poll disables metrics. |
| Datasource recovers | Health poll detects healthy, re-enables metrics with `disabled_reason = "datasource_unhealthy"` |
| Datasource flapping | 3 consecutive failures required before marking unhealthy. Single success re-enables. |
| MCP request for disabled metric | Short-circuits with structured error, no backend round-trip |
| MCP request, no metric match (ungoverned) | Router checks datasource health of resolved tables. Returns error if unhealthy. |
| Secret rotation in-flight | Cached secret expires (5min TTL), next request fetches fresh. Fetch failure marks unhealthy. |
| Delete datasource with running queries | Reject with 409. In-flight queries complete or timeout naturally. |

### Executor Error Response

```python
@dataclass
class ExecutionResult:
    success: bool
    columns: list[str] | None
    rows: list[dict] | None
    row_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    error_code: str | None = None  # "connection_failed", "timeout", "permission_denied", "query_error"
    datasource_id: str | None = None
```

### MCP Error Response

```json
{
  "error": "Cannot execute metric 'total_revenue' - datasource 'Sales Redshift' is currently unhealthy",
  "metric_id": "m_001",
  "datasource_id": "ds_redshift_sales",
  "datasource_status": "unhealthy",
  "last_healthy": "2026-05-28T10:15:00Z",
  "suggestion": "Try again later or contact your admin. Use list_metrics() to see available metrics."
}
```

---

## 8. Prerequisites (Outside Rosetta)

### AWS Infrastructure Required

| Prerequisite | Detail |
|---|---|
| Redshift Serverless workgroup | Must exist with namespace and database |
| IAM role for Rosetta | Needs redshift-data and secretsmanager permissions |
| VPC/networking | Network path from Rosetta to Redshift workgroup |
| Secrets Manager secret (optional) | Only for username/password auth |
| Data in Redshift | Tables must exist for metrics to query |

### IAM Policy (minimal)

```json
{
  "Effect": "Allow",
  "Action": [
    "redshift-data:ExecuteStatement",
    "redshift-data:DescribeStatement",
    "redshift-data:GetStatementResult",
    "redshift-data:CancelStatement",
    "redshift-serverless:GetWorkgroup",
    "secretsmanager:GetSecretValue"
  ],
  "Resource": "*"
}
```

Scope `Resource` to specific ARNs in production.

### Configuration Seeding

```yaml
datasources:
  - datasource_id: ds_redshift_sales
    type: redshift_serverless
    name: Sales Redshift
    endpoint: sales-workgroup
    database: sales_db
    region: us-east-1
    secret_arn: arn:aws:secretsmanager:us-east-1:123456:secret:redshift-sales
```

---

## 9. Change Summary

### New Files

| File | Layer | Purpose |
|---|---|---|
| `src/executors/base.py` | Backend | BaseExecutor ABC, ExecutionResult, HealthStatus models |
| `src/executors/registry.py` | Backend | ExecutorRegistry singleton, startup loader |
| `src/executors/athena.py` | Backend | Refactored from athena_executor.py |
| `src/executors/redshift.py` | Backend | Redshift Serverless executor |
| `src/api/routes_datasources.py` | Backend | CRUD + test-connection endpoints |
| `src/health/poller.py` | Backend | Background health polling + metric toggle |
| `src/health/test_connection.py` | Backend | Async test-connection job runner |
| `ui/src/pages/Datasources.tsx` | Frontend | New datasources management page |
| `ui/src/components/TestConnectionModal.tsx` | Frontend | Test connection progress modal |

### Modified Files

| File | Change |
|---|---|
| `src/main.py` | Register datasources router, start health poller in lifespan |
| `src/config.py` | Add datasources config section |
| `src/mcp/server.py` | Add health short-circuit check before execute_query |
| `src/metrics/compiler.py` | Resolve executor via EXECUTES_ON relationship |
| `src/query/router.py` | Pass datasource context through to executor selection |
| `src/query/athena_executor.py` | Deprecate (replaced by src/executors/athena.py) |
| `src/graph/schema.py` | Add DataSource node constraints + indexes |
| `src/graph/queries.py` | Add datasource CRUD + metric toggle Cypher queries |
| `src/graph/loader.py` | Seed default datasources on startup |
| `src/metrics/loader.py` | Support datasource_id field in metrics YAML |
| `sample/metrics.yaml` | Add datasource_id to sample metrics |
| `sample/config.yaml` | Add datasources section |
| `ui/src/App.tsx` | Add Datasources route |
| `ui/src/api.ts` | Add datasource API methods |
| `ui/src/pages/Metrics.tsx` | Add datasource selector to form |

### Implementation Order

1. Executor abstraction (base.py, registry.py)
2. Refactor Athena into executor pattern (athena.py)
3. DataSource graph model (schema, queries, loader)
4. Datasource CRUD API (routes_datasources.py)
5. Health poller + metric auto-disable
6. Redshift executor (redshift.py)
7. Test-connection job system
8. MCP short-circuit logic
9. Frontend datasources page
10. Metrics form datasource selector
11. End-to-end testing

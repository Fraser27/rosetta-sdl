# Rosetta SDL — Semantic & Governance Remediation: STATE FILE + Execution Plan

> This is the **living state file** for closing the semantic-modeling, governance, correctness,
> and operability gaps found after the security audit (`docs/security-audit-remediation.md`, all
> phases DONE). Every task starts `[ ] NOT STARTED`. Update status inline:
> `[~] IN PROGRESS`, `[x] DONE`, `[!] BLOCKED`.
> Repo: `/Users/fraseque/Fraser/Playground/rosetta-sdl` (branch `feature/redshift-serverless-integration`).
> Local stack: podman (backend :8000, UI :3000); AWS creds via gitignored `docker-compose.override.yml`.

## Context

The security remediation closed injection/auth/hardcoding holes. This plan closes the **next layer**:
the layer is called "governed" and "deterministic" but is missing the semantic machinery
(time grains, additivity, units, fan-out safety), the governance machinery (audit log, versioning,
correct engine binding), and a set of correctness/scale gaps. Fixes are grouped into phases
(A→D) and split into **file-ownership lanes** so parallel fix-agents never touch the same file.
`src/metrics/compiler.py`, `src/api/routes_metrics.py`, `src/catalog/models.py`, and
`ui/src/pages/Metrics.tsx` are hotspots — tasks touching them serialize within their lane.

## Critical execution rule: no two concurrent agents edit the same file

Hotspots: `compiler.py`, `routes_metrics.py`, `catalog/models.py`, `graph/queries.py`, `Metrics.tsx`.
Tasks are assigned to lanes by owning-file. Phases run sequentially where later phases re-touch
hotspot files. After each phase: VERIFY + SANITY + TEST-COVERAGE before the next phase starts.

---

## PROGRESS LOG
- **Setup (2026-07-31):** Baseline green — 103 unit tests pass locally, backend healthy in podman
  (`rosetta-sdl-rosetta-1` :8000). Test strategy: unit tests run locally (container doesn't mount
  tests/); live integration against the running container. AWS creds come from `~/.aws/credentials`
  (acct 444206144756); the gitignored override injects creds + `ATHENA_OUTPUT_BUCKET` from the shell
  at launch, so **backend rebuilds must re-inject creds** — relaunch with:
  `export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id) AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key) ATHENA_OUTPUT_BUCKET=s3://aws-athena-query-results-444206144756-us-east-1/rosetta/ && podman compose up -d rosetta`.
- **A1 DONE & LIVE-VERIFIED (2026-07-31):** time_grains now compiles to `DATE_TRUNC`. Compiler
  (`compiler.py`): added `_TIME_GRAIN_UNITS`, `_fetch_column_types`, `_pick_temporal_dimension`,
  `_apply_time_grain` (returns separate SELECT-alias + bare GROUP-BY forms since Trino rejects GROUP BY
  on an output alias); `compile_metric` gained `time_grain` param; `_fetch_metric_def` now returns
  `time_grains`. Temporal dimension is auto-detected by catalog data_type (date/timestamp/time).
  Requested grain validated against declared `time_grains` (empty = any supported). Wired through
  `MetricQueryRequest` (query + compile endpoints), `MetricSummary.time_grains`, and the UI create form
  (new "Time grains — allowed roll-ups" input). Tests: +7 in test_compiler.py (110 total pass), tsc
  clean. LIVE: created temp metric on `aemo.price_demand`, ran time_grain=month → rows bucketed to
  month-start with correct AVG; day grain works; undeclared grain (week) → 400; cleaned up.
- **A2 DONE & LIVE-VERIFIED (2026-07-31):** additivity semantics. Design note: the compose CTEs
  recompute each metric from base rows at the shared grain (never re-aggregate a pre-aggregated
  value), so the classic avg-of-averages trap is already largely avoided — the genuinely-wrong case
  declared semantics can catch is SUM of a *semi-additive* snapshot across time, which the compiler
  now rejects. non_additive components in a composition produce a warning (values are correct at the
  queried grain but must not be re-summed by consumers). Added `aggregation` to model/queries/create
  API/compiler/UI. LIVE: semi_additive SUM + time_grain=month → 400; without time_grain → compiles;
  invalid value → 400. Unit suite 115 pass, tsc clean.
- **A3 DONE & LIVE-VERIFIED (2026-07-31):** value_type/unit/format metadata end-to-end
  (model, queries, create API + validation, compiler fetch, UI inputs). Mixed-unit compositions warn.
  LIVE: create currency/USD metric → round-trips; invalid value_type → 400. 116 tests, tsc clean.
- **A4+A5 DONE & LIVE-VERIFIED (2026-07-31):** fan-out guard warns only on positive evidence (target
  has PK metadata + join key ≠ PK + fan-out-sensitive aggregate); silent when PK unknown. Compose now
  uses FULL OUTER JOIN + COALESCE'd dimensions so no CTE drops rows. LIVE: composed 2 temp metrics on
  aemo.price_demand with execute=true → FULL OUTER JOIN SQL, 1 row returned, non_additive warning
  fired. **Phase A COMPLETE.** 122 unit tests pass, tsc clean.
- **Phase C parallelized (2026-07-31):** launched 3 background agents for the disjoint-file tasks —
  C3 (executors), C4 (LLM utils/text_utils), C5 (loader). C3+C4 succeeded first pass; C5 hit a transient
  API error and was relaunched, then succeeded. All verified locally (agent tests: 6+18+7).
- **B1+B3+C1+C2 DONE & LIVE-VERIFIED (2026-07-31):** worked the hot-file cluster
  (routes_metrics/routes_query/graph.queries/main) serially by hand while agents ran. B1 audit trail
  (new src/audit/), B3 ungoverned datasource binding, C1 fail-closed existence checks, C2 atomic
  create + async embedding. Caught + fixed a NameError (routes_metrics referenced
  `_resolve_datasource_id_for_metric` which only existed in routes_query — added it) via live smoke —
  an integration gap unit tests didn't cover. LIVE: audit records query+mutation, dup create→409, bad
  ref→400. Full suite **163 tests pass**. Only B2 remains in Phase B; Phase D not started.
- **B2 DONE & LIVE-VERIFIED (2026-07-31):** metric lifecycle (Level 1). status/version/updated_by/at on
  model+graph; create→draft v1, update→version+1+draft, POST /metrics/{id}/status approve/deprecate;
  NL routing gated to approved across router/disambiguator/vector queries; UI status badge + buttons.
  LIVE: full lifecycle + gate + audit-ordered transitions verified. **Phases A, B, C COMPLETE.** 164
  unit tests, tsc clean. Remaining: Phase D (D1-D5, scale/operability).

## TASK LEDGER

### Phase A — Semantic correctness (the product's core promise)
- [x] **A1** DONE — time_grains → real `DATE_TRUNC` bucketing — `src/metrics/compiler.py`,
  `routes_metrics.py`, `catalog/models.py`, `ui/src/pages/Metrics.tsx`. Compiler buckets the
  auto-detected temporal dimension via DATE_TRUNC when `time_grain` is requested; validated against
  declared grains. Live-verified on aemo.price_demand. +7 tests.
- [x] **A2** DONE — Additivity / aggregation semantics — `catalog/models.py`, `compiler.py`,
  `routes_metrics.py`, `routes_query.py`, `graph/queries.py`, `ui/*`. Added `aggregation`
  (additive|semi_additive|non_additive) stored/returned end-to-end. Compiler rejects SUM of a
  semi_additive metric across a time grain (double-count guard); compose annotates (warnings, not
  block) non_additive components. API validates the value (400). UI: additivity dropdown.
  Live-verified. +5 tests.
- [x] **A3** DONE — Units / formatting / value_type on metrics — `catalog/models.py`,
  `routes_metrics.py`, `graph/queries.py`, `compiler.py`, `ui/*`. Added `value_type`
  (number|currency|percent|ratio|count|duration), `unit`, `format` end-to-end. API validates
  value_type (400). compose_metrics warns when composing metrics with different units. Live-verified
  round-trip. +1 test.
- [x] **A4** DONE — Fan-out / join-multiplication guard — `compiler.py`, `routes_metrics.py`.
  Warns (via `CompilationResult.warnings`) when a fan-out-sensitive aggregate (SUM/COUNT/AVG, not
  COUNT DISTINCT) joins to a table whose join key is NOT that table's primary key. Evidence-based:
  silent when target has no PK metadata (Glue rarely has PKs) to avoid false alarms. Warnings surfaced
  in query/compile responses. Live-verified. +4 tests.
- [x] **A5** DONE — Compose join correctness (NULL dimension drop) — `compiler.py`. Replaced ordered
  `LEFT JOIN ... ON first.dim = cte.dim` with `FULL OUTER JOIN` + `COALESCE(all_ctes.dim) AS dim`
  (n-way: each CTE joins the COALESCE of preceding CTEs). No CTE's rows are dropped now. ORDER BY uses
  the coalesced alias. Live-verified executing on aemo.price_demand. +2 tests.

### Phase B — Governance machinery
- [x] **B1** DONE — Durable audit log — new `src/audit/` (recorder + query API `routes_audit.py`),
  hooked into `routes_metrics.py` (metric_query + create/update/delete) and `routes_query.py`
  (nl_query, compose, direct_sql). Append-only `:AuditEvent` nodes: user, ts, action, query_type,
  metric_id, datasource_id, SQL, firewall_verdict, row_count, duration_ms, error. Recorder fails safe
  (never breaks the audited request). Read API `GET /audit/events` with filters + pagination.
  Live-verified: metric_query + mutation events recorded, category filter works. +5 tests.
- [x] **B2** DONE (Level 1) — Metric versioning / approval — `catalog/models.py`, `routes_metrics.py`,
  `graph/queries.py`, `router.py`, `disambiguator.py`, `ui/*`. Added `status`
  (draft|approved|deprecated), `version`, `updated_by`, `updated_at`. Create → draft v1; update →
  version+1 and reset to draft (edited defn needs re-approval); `POST /metrics/{id}/status` transitions.
  **Governance gate:** NL routing (router full-text + disambiguator full-text + both vector queries)
  only surfaces `approved` metrics (legacy no-status → COALESCE 'approved'); draft/deprecated still
  directly queryable by id. UI: status badge + Approve/Deprecate buttons. Status changes audited.
  Live-verified: create→draft, approve, update→draft v2, invalid→400, audit trail ordered. +1 test.
  **Level 2 (immutable :MetricVersion snapshots) deferred** — Level 1 delivers the approval gate;
  snapshots are a follow-up and pair with the audit trail (record compiled version). Level 3 (approver
  RBAC) needs a role model that doesn't exist yet.
- [x] **B3** DONE — Ungoverned path datasource binding — `routes_query.py`. Added `_tables_in_sql`
  (sqlglot, mirrors firewall extraction), `_resolve_datasource_for_sql` (single→id, multiple→400,
  untagged→None), `_execute_ungoverned` (routes via registry, else Athena default). Ungoverned LLM SQL
  now binds to the datasource of the tables it references; LLM never picks the engine. +5 tests.

### Phase C — Correctness / robustness
- [x] **C1** DONE — Existence checks fail-CLOSED — `routes_metrics.py`. `_exists` now raises 503 on
  graph error instead of returning True (fail-open). Live-verified: bad refs → 400, valid unaffected.
- [x] **C2** DONE — Metric create uniqueness + async embedding — `routes_metrics.py`, `graph/queries.py`.
  Atomic `CREATE_METRIC_NODE` relies on the `metric_unique` constraint → ConstraintError → 409 (closes
  the check-then-write race). Embedding moved to FastAPI BackgroundTasks (off request path). Live: dup
  create → 409.
- [x] **C3** DONE (agent) — Surface truncation — `executors/base.py`, `athena.py`, `redshift.py`.
  Added `truncated: bool` on ExecutionResult; both executors fetch max_rows+1 to detect overflow, trim,
  set flag. +6 tests (first executor tests in the repo).
- [x] **C4** DONE (agent) — Robust LLM parsing + Bedrock retry — `text_utils.py` (`extract_sql`,
  `extract_json`, `retry_bedrock`), wired into `generator.py`, `enrichment.py`, `embeddings.py`.
  Tolerant fence/JSON extraction + exponential-backoff retry on throttling/timeout. +18 tests.
- [x] **C5** DONE (agent) — YAML loader hardening — `loader.py`. Pydantic-validates each entry (skip+warn
  on bad), detects duplicate metric_ids (keep first + warn), warns on missing file / errors on malformed
  YAML with line info (no silent `[]`). +7 tests.

### Phase D — Scale / operability
- [ ] **D1** Pagination — `routes_metrics.py`, `routes_catalog.py` (list_tables, graph_data).
  - limit/offset (or cursor); cap graph_data node/edge counts. Lane: **ROUTES**.
- [ ] **D2** Glue scan resilience — `src/discovery/glue_scanner.py`.
  - Rate-limit Glue API; collect + report per-database failures instead of silent skip; cache db list.
    Lane: **DISCOVERY**.
- [ ] **D3** Thread-safe job stores + cleanup — `src/discovery/enrichment.py`,
  `src/health/test_connection.py`.
  - Lock access; scheduled TTL cleanup; bound concurrency. Lane: **JOBS**.
- [ ] **D4** Query result cache + cost controls — `src/executors/*`.
  - Optional result cache keyed by (datasource, sql, max_rows); Athena bytes-scanned surfacing/limits.
    Lane: **EXECUTORS** (after C3).
- [ ] **D5** Executor registry thread-safety — `src/executors/registry.py`, `routes_datasources.py`.
  - Guard mutation with a lock; atomic re-register on datasource update (no downtime window). Lane: **REGISTRY**.

### Cross-cutting agents (run per phase)
- [ ] **VERIFY** — rebuild backend, `tsc --noEmit`, `vite build`, hit key endpoints, screenshot UI.
- [ ] **TEST-COVERAGE** — extend `tests/unit/`; add first API/executor/loader tests (currently 0).
- [ ] **SANITY** — regression check: metric CRUD, scan, sample-load, NL query, UI render.

---

## DECISIONS (resolve before/while building)
- **Grain vs time_grains (A1):** `grain` = default GROUP BY dims (works today, `compiler.py:338`).
  `time_grains` = allowed time roll-ups declared per metric; caller selects one at query time and the
  compiler emits `DATE_TRUNC`. UI: add a "time grains (allowed roll-ups)" multi-select under Grain on
  the create form, and a "roll up by" selector on the run/query panel. Alternative considered: drop
  `time_grains` entirely and treat grain as fixed — rejected because per-query re-bucketing is wanted.
- **Audit sink (B1):** Neo4j `:AuditEvent` for durable/queryable compliance record. OTEL→Langfuse is
  ADDITIVE for LLM-call observability (ungoverned generate_sql, enrichment, embeddings) — not a
  replacement for the audit log.
- **Versioning depth (B2):** ship Level 1 (status/approval) first; Level 2 (immutable version history)
  after, and pairs with B1 (audit records compiled version). Level 3 (approver RBAC) needs a real role
  model that doesn't exist yet — out of scope until RBAC lands.

## AUDIT FINDINGS (evidence)
### Semantic
- time_grains stored/loaded/returned but compiler emits zero `DATE_TRUNC` (`compiler.py`).
- No additivity field; `compose_metrics` (`compiler.py:550`) can avg-of-avg silently.
- No unit/format/value_type on `MetricDefinition` (`catalog/models.py`).
- `compile_metric` SUMs over joins with no fan-out detection (`compiler.py:364,369-375`).
- `compose_metrics` ordered LEFT JOIN on same-named dims drops NULL-dim rows (`compiler.py:655-663`).
### Governance
- No audit trail anywhere (`grep audit` empty); user_email captured but unused (`auth.py:156`).
- Metrics edited in place; no status/version/history.
- Ungoverned SQL hardcodes Athena regardless of table datasource (`routes_query.py:218`).
### Correctness
- Existence checks fail-open on graph error (`routes_metrics.py`, `routes_datasources.py`).
- No uniqueness guard on metric create; synchronous embedding blocks response.
- Executors truncate at max_rows with no has_more flag (`athena.py`, `redshift.py`).
- Fragile ```sql/```json fence parsing (`generator.py:68`, `enrichment.py`); no Bedrock retry.
- Loader: no schema validation, no dup detection, silent `[]` on missing file (`loader.py`).
### Scale/ops
- No pagination: list_metrics, list_tables, graph_data (unbounded).
- Glue scan N+1, no rate limit, silent per-db skip (`glue_scanner.py`).
- In-memory job stores ~10 cap, not thread-safe, write-only cleanup (`enrichment.py`, `test_connection.py`).
- No result cache / Athena cost control on executors.
- Executor registry unlocked singleton; update re-register has downtime window (`registry.py`).

# Rosetta SDL — Audit Remediation: STATE FILE + Execution Plan

> This is the **living state file**. Every task starts `[ ] NOT STARTED`.
> Orchestrating agent updates status inline: `[~] IN PROGRESS`, `[x] DONE`, `[!] BLOCKED`.
> Repo: `/Users/fraseque/Fraser/Playground/rosetta-sdl` (branch `feature/redshift-serverless-integration`).
> Local stack runs via podman (backend :8000, UI :3000); AWS creds already wired via gitignored `docker-compose.override.yml`.

## PROGRESS LOG (post-audit feature work)
- **DOCUMENT METADATA UPLOAD + SEMANTIC ROUTING + PARALLEL 'both' — COMPLETE & VERIFIED** (2026-07-31):
  v1 scope (txt/md only, whole file as one blob, capped 1000 words). POST
  /catalog/documents/{s3_key}/metadata-file reads UTF-8 text, truncates to 1000 words, embeds with the
  configured s3vectors model, stores metadata_embedding (1024-dim) + metadata_text on the Document node.
  New document_embedding vector index (schema.py, fixed 1024-dim — changing embed model dims requires
  reindex, documented). Router gains a document vector-fallback mirroring the metric one
  (VECTOR_SEARCH_DOCUMENTS_SIMPLE) — fixes 'matching a document node means nothing'. natural_language_query
  now runs structured + unstructured CONCURRENTLY via asyncio.gather (vector search offloaded to a thread
  since boto3 is sync); return_exceptions isolates failures. routes_catalog.init now takes config. UI:
  txt/md upload control in Documents detail modal. Verified live: .md upload → 1024-dim embedding stored,
  .csv rejected 400, index exists, 164 unit tests pass, tsc+build clean.
  Plan-vs-execute already honored by existing /query/plan (details) vs /query/natural-language (results).
  NOT DONE (user will handle): API on/off toggles. DEFERRED: CSV/xlsx rows, writing into S3 Vectors,
  NL auto-composition of multiple metrics.

- **DATASOURCE ENABLE/DISABLE CASCADE + S3VECTORS EMBEDDING CONFIG — COMPLETE & VERIFIED** (2026-07-31):
  (A) PATCH /datasources/{id}/enabled flips ds.enabled and cascades: disable turns off all enabled
  metrics on it (disabled_reason='datasource_disabled' sentinel); enable restores ONLY those (individually
  -disabled metrics stay off). UI: Disable/Enable button + red 'disabled' badge + dimmed card on Datasources.
  Verified: metric FALSE/'datasource_disabled' on disable → TRUE/NULL on enable.
  (B) UI-selectable S3 Vectors search embedding model: EmbeddingConfig.s3vectors_model_id (default Titan v2),
  persisted on the same SystemConfig node (UPSERT_S3VECTORS_EMBEDDING_MODEL, separate SET so it coexists with
  query_model), GET/PUT /admin/config/s3vectors-model (validates against AVAILABLE_EMBEDDING_MODELS, 400 on
  unknown), startup hydration, search_vectors call site passes it. UI: 2nd dropdown in Configurations card
  with 'must match ingest model' warning. Verified: GET/PUT/invalid-reject/Neo4j-persist/survives-restart
  (both models hydrate). Unit suite 164 passed, tsc clean, vite build ok.
  DEFERRED (task #26): Document metadata upload (CSV/xlsx/txt/md → vectorize → match) — needs scoping.

- **CONFIGURATIONS UI — selectable ungoverned query model COMPLETE & VERIFIED** (2026-07-31):
  New `GET/PUT /admin/config/query-model` — GET returns current + `AVAILABLE_QUERY_MODELS`
  (constants.py); PUT validates against the allowlist (400 on unknown), persists to a Neo4j
  `:SystemConfig {key:'system_config'}` singleton (UPSERT_SYSTEM_CONFIG), and mutates the shared
  in-memory `_config.bedrock.query_model` so it applies immediately (routes share the config by
  reference). Startup hydration in main.py reloads the override from Neo4j. UI: "Configurations"
  card (first) in Admin.tsx with a model dropdown (auto-saves on change) + api.getQueryModel/
  setQueryModel. Verified live: GET/PUT/invalid-reject, in-memory apply, Neo4j persistence, and
  **survives container restart** (hydration logged). tsc clean, vite build ok. Governed metrics
  unaffected (they compile deterministically, no LLM).

- **COLUMN ENRICHMENT FEATURE COMPLETE & VERIFIED** (2026-07-31): (1) 50-word cap — new
  `src/text_utils.py`; manual description PATCH rejects >50 words (422), LLM enrichment output
  truncated silently. (2) `is_deprecated` on Column — model, MERGE_COLUMN (coalesce so re-scan
  won't clobber a manual flag), GET_TABLE_DETAILS, loader, new PATCH `/columns/{c}/deprecation`.
  (3) Deprecation precedence: generator schema context emits `-- DEPRECATED: avoid using this
  column` in place of the description, steering the LLM away (verified live). (4) UI: TableDetail
  shows struck-through name + red marker + deprecated badge + Deprecate/Restore toggle + client
  word counter. Also FIXED a pre-existing route-shadowing bug: `/tables/{path}/description` greedily
  swallowed `/columns/{c}/description` (column edits were never reachable) — reordered so the
  specific column routes register first. Unit suite **103 passed** (+10).
  DEFERRED next (task #23): Configurations UI for selectable ungoverned query model, Neo4j-persisted.

## Context

An audit (see findings below) surfaced SQL injection, an auth-bypass posture, multi-datasource
execution/validation gaps, and pervasive hardcoding. This plan fixes **all** of it, tracked here.
Fixes are grouped into priority phases (P0→P3). Within a phase, work is split into **file-ownership
lanes** so parallel fix-agents never touch the same file. After each phase, verify + sanity +
test-coverage agents run before the next phase starts.

## Critical execution rule: no two concurrent agents edit the same file

Hotspots `src/metrics/compiler.py` and `src/main.py` are touched by several findings. Tasks are
assigned to lanes by owning-file so parallel agents are conflict-free. Phases run sequentially
because later phases depend on / re-touch hotspot files.

---

## PROGRESS LOG
- **P0 COMPLETE & VERIFIED** (2026-07-30): P0-1 + P0-2 done. Live tests: injection escaped/rejected,
  legit metrics compile. SANITY: no regressions (NL routing, derived CTEs, CRUD all OK). Full unit
  suite **81 passed** in container (34 new security tests + 2 stale tests updated to assert secure behavior).
  Note: metrics from seed YAML have empty datasource_id (P1/P2 work); container clock skew ~9min causes
  Athena signature errors on execution (cosmetic, doesn't affect compile/routing).

- **P1 COMPLETE & VERIFIED** (2026-07-30): P1-1 auth fail-closed (ALLOW_INSECURE_NO_AUTH gate) + CORS
  allowlist; P1-2 compose routes via executor; P1-3 cross-datasource join/derived validation (permissive
  when untagged); P1-4 Neo4j password default removed (env-driven). Also fixed 2 bugs found during
  integration: (a) firewall flagged CTE names as tables → excluded CTE aliases; (b) latent NameError
  `execute_query` unimported in routes_query.py /sql endpoint → added import. Added ALLOW_INSECURE_NO_AUTH=1
  + CORS to gitignored override for local dev. Verified: compose produces valid routed CTE SQL; auth
  fail-closed toggles correctly; unit suite **82 passed**.

- **P2 COMPLETE & VERIFIED** (2026-07-30): P2-1 constants (src/constants.py: DEFAULT_DATASOURCE_ID,
  DEFAULT_AWS_REGION, workgroup, BEDROCK_ANTHROPIC_VERSION) + config-driven QUERY_TIMEOUT_SECONDS;
  P2-2 boundary validation (type→422, datasource/table/base_metric/join existence→400, secret_arn
  excluded from responses); P2-3 secret_arn preserved on edit (frontend omits-when-blank + backend
  preserve-if-omitted via model_fields_set) — verified secret retained; P2-4 robust table parsing +
  TableSummary.datasource_id added to api.ts. Verified: validation smoke tests pass, normal create 200,
  tsc clean, vite build ok, unit suite **82 passed**.
  Remaining follow-ups for P3/later: routes_query.py ds_default_athena literal, ui Metrics.tsx literal,
  poller.py failure_threshold param, CDK/agentcore region hardcoding.

- **P3 + CROSS-CUTTING COMPLETE** (2026-07-30): P3-1 health_check logging; P3-3 JWKS TTL env-driven;
  P3-4 compose response includes datasource_id (+ replaced ds_default_athena literal w/ constant);
  P3-5 removed `as any` cast. P3-2 (unify max_query_rows) SKIPPED — would change API default page
  size 100→500 (real behavior change). Final SANITY sweep: all 12 checks PASS, no 500s, no security
  regressions, no secret_arn leaks. Full unit suite **93 passed** (+11 cross-datasource/provider tests).
  ALL PHASES DONE. Not yet committed — awaiting user go-ahead.

## TASK LEDGER (all NOT STARTED)

### Phase P0 — SQL injection + firewall backstop (BLOCKS everything; do first)
- [x] **P0-1** DONE — Compiler injection hardening — `src/metrics/compiler.py`. Verified live: hostile filter value escaped (`''`), hostile column rejected 400, hostile order_by rejected 400, legit metric compiles.
  - Escape/parameterize filter string values (`_build_filter_clauses`, :534-550).
  - Validate `filter.column`, `dimensions`, `order_by` against real catalog columns
    (reuse `_fetch_table_columns`, :91). Reject unknown identifiers.
  - Validate `expression` and stored metric `filters` parse to a safe shape via `sqlglot`
    (already a dep); reject subquery/`;`/comment injection. Applies to `compile_metric`,
    `_compile_metric_cte`, `compose_metrics`, `compile_sql`.
  - Lane: **COMPILER** (sole owner of compiler.py this phase).
- [x] **P0-2** DONE — Firewall real-by-default + literal inspection — `src/query/firewall.py`, `src/main.py`. Empty allowlist now = deny-all; FIREWALL_MODE=catalog (default) uses lazy graph provider (30s cache) so scanned tables auto-allow; disabled mode logs loud warning.
  - Default `allowed_tables` to the catalog's known tables (or fail-closed) instead of empty no-op
    (`main.py:43`). Extend firewall to inspect subqueries/CTEs/literals, not just `exp.Table`.
  - Lane: **FIREWALL** (owns firewall.py; touches main.py — P0 runs alone so no main.py conflict).

### Phase P1 — Auth posture + datasource-correct execution
- [ ] **P1-1** Auth fail-closed + CORS tightening — `src/auth.py`, `src/main.py`
  - Require auth unless an explicit `ALLOW_INSECURE_LOCAL=1` is set; unset pool in prod = fail closed.
  - Restrict CORS origins (config-driven allowlist) instead of `["*"]` + credentials.
  - Add explicit auth/authz dependency to destructive `/admin/*` routes.
  - Lane: **AUTH** (owns auth.py + main.py).
- [ ] **P1-2** Compose path executor routing — `src/api/routes_query.py`
  - Route `/query/compose` through `_resolve_executor_for_metric` / `_execute_on_datasource`
    (:130-153) instead of hardcoded `execute_query` (:454-460). Reuse existing helpers.
  - Lane: **QUERY** (owns routes_query.py).
- [ ] **P1-3** Datasource-consistency validation — `src/metrics/compiler.py` OR `src/api/routes_metrics.py`
  - Reject metrics whose joins reference tables on a different datasource than the source table.
  - Reject derived metrics whose base metrics span datasources.
  - Lane: **COMPILER** — MUST run after P0-1 (same file). Do at end of P0 or start of P1 serially
    with COMPILER lane, never concurrently with P0-1.
- [ ] **P1-4** Neo4j default-password hardening — `src/config.py`, `docker-compose.yml`, `sample/config.yaml`
  - Remove hardcoded `semantic-layer` default; require env/secret; keep local dev working via override.
  - Lane: **CONFIG** (owns config.py + compose/sample). ⚠ config.py also in P2-1 → serialize CONFIG lane.

### Phase P2 — De-hardcoding + validation/UX
- [ ] **P2-1** Centralize magic values — `src/config.py`, `src/executors/*`, `src/executors/registry.py`, constants module
  - Single source for `ds_default_athena`, region, workgroup, Bedrock model IDs + `anthropic_version`.
  - Make query timeouts and health `failure_threshold` config-driven (poller currently ignores config).
  - Lane: **CONFIG** (serialize after P1-4).
- [ ] **P2-2** API boundary validation — `src/api/routes_metrics.py`, `src/api/routes_datasources.py`
  - Existence checks for `datasource_id`, `source_table`, join tables, `base_metrics`;
    `DataSourceRequest.type` → `Literal[...]`. Explicitly exclude `secret_arn` from responses.
  - Lane: **ROUTES** (owns routes_metrics.py + routes_datasources.py). ⚠ routes_metrics also P1? No —
    P1-3 lands in compiler; safe. But serialize vs P1-2 only if it touches routes_query (it doesn't).
- [ ] **P2-3** Datasource edit preserves `secret_arn` — `ui/src/pages/Datasources.tsx`, maybe `ui/src/api.ts`
  - Leave-unchanged-if-blank on edit; don't blank stored secret.
  - Lane: **UI** (owns ui/*).
- [ ] **P2-4** Robust table-name parsing — `src/metrics/compiler.py`, `ui/src/pages/Metrics.tsx`
  - Handle non-`db.table` shapes safely. ⚠ compiler.py → serialize in COMPILER lane after P1-3.

### Phase P3 — Hygiene / observability
- [ ] **P3-1** Log swallowed exceptions in `health_check()` — `src/executors/athena.py`, `redshift.py`.
- [ ] **P3-2** Unify `max_query_rows` defaults across routes + MCP.
- [ ] **P3-3** Config-driven JWKS cache TTL — `src/auth.py`. ⚠ serialize after P1-1 (same file).
- [ ] **P3-4** Compose response includes resolved datasource/executor — `src/api/routes_query.py` (after P1-2).
- [ ] **P3-5** Remove stale `as any` casts / minor UI polish — `ui/*` (after P2-3).

### Cross-cutting agents (run per phase)
- [ ] **VERIFY** — after each phase: rebuild podman backend, `tsc --noEmit`, `vite build`,
  hit key endpoints (create hostile metric → expect rejection; Redshift composed metric → correct engine),
  screenshot UI where relevant. Report pass/fail.
- [ ] **TEST-COVERAGE** — author/extend `tests/unit/` (existing: test_firewall, test_compiler,
  test_disambiguator, test_router) to cover every fixed behavior + broad feature coverage of the
  platform (compiler, firewall, routing, executors, datasource CRUD, metric CRUD). Add pytest to
  the container/venv to run them.
- [ ] **SANITY** — independent regression check that nothing previously working broke
  (metric CRUD, scan, sample-data load, natural-language query, UI pages render).

---

## Orchestration strategy

1. **Create the real working state file** (copy of this ledger) that fix-agents update as they go.
2. **Phase P0 (serial pair):** launch COMPILER (P0-1) and FIREWALL (P0-2) — different files → parallel OK.
   Then VERIFY + SANITY + TEST-COVERAGE on P0.
3. **Phase P1 (parallel lanes):** AUTH (P1-1), QUERY (P1-2), CONFIG (P1-4) in parallel (disjoint files);
   COMPILER (P1-3) runs serially after P0-1. Then VERIFY + SANITY + TEST-COVERAGE.
4. **Phase P2 (parallel lanes):** CONFIG (P2-1, after P1-4), ROUTES (P2-2), UI (P2-3), COMPILER (P2-4).
   Disjoint except COMPILER/CONFIG which serialize within their lanes. Then VERIFY + SANITY + TEST.
5. **Phase P3 (parallel):** disjoint small fixes, honoring lane serialization notes. Then final
   VERIFY + SANITY + full TEST-COVERAGE pass.
6. Each fix-agent gets: exact file:line targets, the specific finding, "reuse existing helpers"
   guidance, and "edit ONLY your owned file(s)" constraint. I review each agent's actual diff
   (not just its summary) before marking `[x] DONE`.
7. No commit until all phases pass VERIFY+SANITY; then a single reviewed commit (ask before commit).

---

## AUDIT FINDINGS (evidence; [VERIFIED] = confirmed in current code)

### P0 — CRITICAL: SQL injection, unguarded by default
- **[VERIFIED]** Filter values unescaped — `compiler.py:546-547` (`{op} '{f.value}'`).
- **[VERIFIED]** Filter column names unvalidated for param-less metrics — `compiler.py:194-208`.
- **[VERIFIED]** `order_by` interpolated raw — `compiler.py:525-526`, `275-278`.
- **[VERIFIED]** `expression` interpolated raw into SELECT — `compiler.py` ~247/323/370.
- **[VERIFIED]** Stored metric `filters` interpolated raw — `routes_metrics.py` + `compiler.py:265`.
- **[VERIFIED]** Firewall no-op by default — `main.py:43` + `config.py:82` + `firewall.py:39-40`;
  only inspects `exp.Table` (`firewall.py:57`), so literal subqueries evade even when enabled.

### P1 — HIGH
- **[VERIFIED]** Auth bypass when `COGNITO_USER_POOL_ID` unset — `auth.py:96-97`; UI mirrors `auth.ts`.
- **[VERIFIED]** CORS `allow_origins=["*"]` + `allow_credentials=True` — `main.py:125-131`.
- **[VERIFIED]** `/query/compose` hardcodes Athena — `routes_query.py:454-460` (single-metric path
  correctly resolves executor at :130-153/195).
- Cross-datasource joins silently produced — `MetricJoin` has no datasource; compiler emits joins unchecked.
- Derived metrics don't validate shared datasource — `compose_metrics` takes `source_tables[0]`.
- **[VERIFIED]** Default Neo4j password `semantic-layer` in `config.py:16`, `docker-compose.yml:8,26`,
  `sample/config.yaml`, `sample/seed_graph.cypher`.
- Destructive `/admin/*` routes rely solely on global middleware — no explicit authz.

### P2 — MEDIUM (hardcoding/portability/validation)
- Region `us-east-1` hardcoded ~9 places (`config.py:60`, `auth.py:28`, `main.py:81,95`,
  `executors/athena.py:31`, `executors/redshift.py:30`, `routes_datasources.py:53`, UI, `cdk/bin/app.ts:7`,
  `agentcore/deploy_agent.py:34`).
- Magic string `ds_default_athena` ×6 (`main.py`, `registry.py`, `routes_query.py:127`, `Metrics.tsx:132`).
- Workgroup `primary` default vs prod `semantic-layer-wg` (`cdk/lib/rosetta-sdl-stack.ts:310`).
- Bedrock model IDs / `anthropic_version` hardcoded+duplicated (`config.py:40-41`, compose/sample;
  `enrichment.py:107`, `generator.py:58`).
- No Redshift table discovery (only `glue_scanner.py`).
- **[VERIFIED-ish]** Datasource edit drops `secret_arn` (`Datasources.tsx handleEdit`); response omits it too.
- Weak boundary validation; `DataSourceRequest.type` free string.
- Fragile `split(".")` table parsing (`compiler.py:48`, `Metrics.tsx:47`).
- Hardcoded 30s timeouts (`athena.py:69`, `redshift.py:50`); poller ignores configurable failure_threshold.

### P3 — LOW
- Silent `except` in `health_check()` w/o logging.
- Scattered `max_query_rows` (500/100/20).
- JWKS TTL 3600s hardcoded.
- Compose response lacks executor/datasource hint.
- Stale `as any` casts / minor UI polish; no per-datasource table browse view.

## Verification (end-to-end)
- `pytest tests/` (add pytest to image/venv), `tsc --noEmit`, `vite build`.
- Podman stack: hostile-filter metric rejected; Redshift-bound composed metric runs on correct engine;
  auth fail-closed when pool unset + `ALLOW_INSECURE_LOCAL` absent; UI pages render (screenshots).
- SANITY: metric CRUD, scan, sample-data load, natural-language query still work.

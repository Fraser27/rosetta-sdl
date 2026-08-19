"""Query API routes — natural language and direct SQL query execution."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.audit import record_query, user_from_request
from src.catalog.models import QueryPlan, QueryResponse
from src.config import SemanticLayerConfig
from src.constants import DEFAULT_DATASOURCE_ID
from src.executors.base import HealthStatus
from src.executors.registry import registry
from src.governance import BLOCK_REASON, record_blocked_query
from src.graph.client import GraphClient
from src.metrics.compiler import compile_metric, compose_metrics, FilterClause as CompilerFilter
from src.query.athena_executor import execute_query, execute_query as athena_execute_query
from src.query.disambiguator import disambiguate
from src.query.firewall import SQLFirewall
from src.query.generator import generate_sql
from src.query.router import route_query
from src.query.vectors_executor import search_vectors
from src.text_utils import strip_fulltext_stopwords

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["query"])

_graph: GraphClient | None = None
_config: SemanticLayerConfig | None = None
_firewall: SQLFirewall | None = None


def init(graph: GraphClient, config: SemanticLayerConfig, firewall: SQLFirewall) -> None:
    global _graph, _config, _firewall
    _graph = graph
    _config = config
    _firewall = firewall


def _get_graph() -> GraphClient:
    if _graph is None:
        raise HTTPException(503, "Graph client not initialized")
    return _graph


class NLQueryRequest(BaseModel):
    question: str
    filters: list[dict] = Field(default_factory=list, description="Explicit filters for governed metrics (e.g., [{column: 'user_id', operator: '=', value: 'user_a'}])")
    dimensions: list[str] = Field(default_factory=list, description="Dimension columns for governed metrics (e.g., ['order_date'])")
    time_grain: str | None = Field(default=None, description="Roll the metric's time axis up to this grain (e.g. 'month'); must be one of the metric's declared time_grains")
    max_rows: int = Field(default=100, ge=1, le=1000)
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


class SQLQueryRequest(BaseModel):
    sql: str
    database: str | None = None
    catalog: str | None = None
    max_rows: int = Field(default=100, ge=1, le=1000)
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


@router.post("/natural-language", response_model=QueryResponse)
async def natural_language_query(request: NLQueryRequest, http_request: Request):
    """Full natural language query pipeline.

    1. Route (graph-based: structured, unstructured, or both)
    2. For structured: disambiguate → check metrics → compile or generate SQL → firewall → execute
    3. For unstructured: search S3 Vectors
    """
    graph = _get_graph()
    user = user_from_request(http_request)

    # 1. Route the query
    route_result = route_query(request.question, graph, embedding_config=_config.embedding)
    response = QueryResponse(route=route_result.route)

    workgroup = request.workgroup or _config.athena.workgroup

    # Parse explicit filters for governed metrics
    filter_clauses = [
        CompilerFilter(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in request.filters
    ] if request.filters else None

    want_structured = route_result.route in ("structured", "both")
    want_unstructured = route_result.route in ("unstructured", "both")

    # Run the structured (SQL) and unstructured (vector) paths CONCURRENTLY for
    # 'both'. The vector search is sync/blocking boto3, so offload it to a thread;
    # return_exceptions keeps one path's failure from cancelling the other.
    structured_coro = (
        _handle_structured(
            request.question, route_result, graph,
            workgroup=workgroup, filters=filter_clauses,
            dimensions=request.dimensions or None,
            time_grain=request.time_grain,
            user=user,
        ) if want_structured else _noop()
    )
    unstructured_coro = (
        asyncio.to_thread(
            search_vectors, request.question, graph,
            model_id=_config.embedding.s3vectors_model_id,
        ) if want_unstructured else _noop()
    )
    sql_result, vector_results = await asyncio.gather(
        structured_coro, unstructured_coro, return_exceptions=True,
    )

    # 2. Apply structured result
    if want_structured:
        if isinstance(sql_result, Exception):
            logger.error("Structured query failed: %s", sql_result)
            response.error = str(sql_result)
            record_query(action="nl_query", user=user, query_type="", sql="", error=str(sql_result))
        else:
            response.intent = sql_result.get("intent", "analytical")
            response.query_type = sql_result.get("query_type", "ungoverned")
            response.metric_name = sql_result.get("metric_name")
            response.sql = sql_result.get("sql")
            response.results = sql_result.get("results")
            response.hint = sql_result.get("hint")
            _results = sql_result.get("results") or {}
            record_query(
                action="nl_query", user=user, query_type=response.query_type or "",
                metric_id=sql_result.get("metric_id", ""),
                datasource_id=sql_result.get("datasource_id", ""),
                sql=response.sql or "", firewall_verdict="allowed",
                row_count=int(_results.get("row_count") or 0),
                duration_ms=int(_results.get("duration_ms") or 0),
                error=str(_results.get("error") or ""),
            )

    # 3. Apply unstructured result
    if want_unstructured:
        if isinstance(vector_results, Exception):
            logger.error("Vector search failed: %s", vector_results)
            if not response.error:
                response.error = str(vector_results)
        else:
            response.vector_results = vector_results
            if not response.intent:
                response.intent = "document"
                response.query_type = "document"

    return response


async def _noop():
    """Placeholder coroutine for a route path that isn't taken."""
    return None


def _unapproved_metric_hint(question: str, graph: GraphClient) -> str | None:
    """Detect a governed metric that matches the question but was skipped by routing
    because it isn't approved (e.g. draft/deprecated).

    NL routing only serves approved metrics, so a matching draft silently falls
    through to ungoverned SQL. This mirrors the router's matching (full-text, then
    vector-embedding fallback) WITHOUT the status gate — inverting it to non-approved
    metrics — so the nudge fires on natural questions, not just terse keyword ones.
    (A bare "fraud rate" scores ~0.31 on full-text, but "what is the fraud rate?"
    drops to ~0.18 as stopwords dilute the Lucene score; the vector path catches it.)
    """
    m = None
    ft_query = strip_fulltext_stopwords(question)
    if ft_query:
        try:
            hits = graph.query(
                "CALL db.index.fulltext.queryNodes('metric_search', $q) YIELD node, score "
                "WHERE score > 0.3 AND COALESCE(node.status, 'approved') <> 'approved' "
                "RETURN node.name AS name, COALESCE(node.status, 'approved') AS status "
                "ORDER BY score DESC LIMIT 1",
                {"q": ft_query},
            )
            m = hits[0] if hits else None
        except Exception as e:
            logger.debug("Unapproved-metric hint full-text lookup failed: %s", e)

    # Vector fallback — embed the question and kNN over metric embeddings, keeping
    # only non-approved matches above the configured similarity floor.
    if m is None and _config and _config.embedding.enabled:
        try:
            from src.query.embeddings import get_embedding

            question_vec = get_embedding(
                question, _config.embedding.model_id, _config.embedding.dimensions
            )
            if question_vec:
                vhits = graph.query(
                    "CALL db.index.vector.queryNodes('metric_embedding', 5, $vec) "
                    "YIELD node, score "
                    "WHERE score > $min_score AND COALESCE(node.status, 'approved') <> 'approved' "
                    "RETURN node.name AS name, COALESCE(node.status, 'approved') AS status "
                    "ORDER BY score DESC LIMIT 1",
                    {"vec": question_vec, "min_score": _config.embedding.vector_min_score},
                )
                m = vhits[0] if vhits else None
        except Exception as e:
            logger.debug("Unapproved-metric hint vector lookup failed: %s", e)

    if m is None:
        return None
    return (
        f"A governed metric '{m['name']}' matches this question but is '{m['status']}', "
        f"not approved — so this answer used ungoverned LLM-generated SQL. "
        f"Approve the metric to get the deterministic governed result."
    )


def _resolve_datasource_id_for_metric(metric_id: str, graph: GraphClient) -> str:
    """Resolve the datasource_id a metric executes on, or the default."""
    results = graph.query(
        "MATCH (m:Metric {metric_id: $mid})-[:EXECUTES_ON]->(ds:DataSource) "
        "RETURN ds.datasource_id AS datasource_id",
        {"mid": metric_id},
    )
    if results:
        return results[0]["datasource_id"]
    return DEFAULT_DATASOURCE_ID


def _resolve_executor_for_metric(metric_id: str, graph: GraphClient):
    """Resolve the executor for a metric via its EXECUTES_ON relationship."""
    return registry.get(_resolve_datasource_id_for_metric(metric_id, graph))


async def _execute_on_datasource(sql: str, metric_id: str | None, graph: GraphClient, max_rows: int) -> dict:
    """Execute SQL using the appropriate executor (resolved from metric or default)."""
    executor = None
    if metric_id:
        executor = _resolve_executor_for_metric(metric_id, graph)

    if executor:
        result = await executor.execute(sql, max_rows=max_rows)
        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "query_execution_id": result.query_execution_id,
            "error": result.error,
        }
    else:
        # Fallback to legacy athena executor
        return athena_execute_query(
            sql=sql,
            workgroup=_config.athena.workgroup,
            output_location=_config.athena.output_bucket,
            max_rows=max_rows,
        )


def _tables_in_sql(sql: str) -> list[str]:
    """Extract fully-qualified table references from SQL via sqlglot (Trino).

    Mirrors the firewall's extraction so datasource binding agrees with what the
    firewall validates. CTE names are excluded (internal aliases, not real tables).
    """
    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse(sql, dialect="trino")
    except sqlglot.errors.ParseError:
        return []
    tables: list[str] = []
    for statement in parsed:
        if statement is None:
            continue
        cte_names = {
            cte.alias_or_name.lower()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        for t in statement.find_all(exp.Table):
            if not t.catalog and not t.db and t.name.lower() in cte_names:
                continue
            parts = [p for p in (t.catalog, t.db, t.name) if p]
            tables.append(".".join(parts))
    return tables


def _resolve_datasource_for_sql(sql: str, graph: GraphClient) -> str | None:
    """Deterministically resolve the datasource for ungoverned SQL from its tables.

    Returns a single datasource_id when all tagged tables agree; None when every
    referenced table is untagged (→ caller uses the Athena default). Raises 400 if
    the referenced tables span multiple datasources (a single query can't run
    cross-engine). The LLM never chooses the engine — this is derived from the graph.
    """
    tables = _tables_in_sql(sql)
    found: set[str] = set()
    for full_name in tables:
        rows = graph.query(
            "MATCH (t:Table {full_name: $fn}) RETURN t.datasource_id AS ds",
            {"fn": full_name},
        )
        ds = rows[0]["ds"] if rows and rows[0].get("ds") else None
        if ds:
            found.add(ds)
    if len(found) > 1:
        raise HTTPException(
            400,
            f"Generated query references tables across multiple datasources {sorted(found)}; "
            f"a single query cannot run cross-engine.",
        )
    return next(iter(found)) if found else None


async def _execute_ungoverned(sql: str, datasource_id: str | None, wg: str | None, max_rows: int) -> dict:
    """Execute ungoverned SQL on the resolved datasource's executor, else Athena default."""
    executor = registry.get(datasource_id) if datasource_id else None
    if executor:
        result = await executor.execute(sql, max_rows=max_rows)
        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "query_execution_id": result.query_execution_id,
            "error": result.error,
        }
    return athena_execute_query(
        sql=sql,
        workgroup=wg or _config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        max_rows=max_rows,
    )


async def _handle_structured(
    question: str, route_result, graph: GraphClient,
    workgroup: str | None = None,
    filters: list[CompilerFilter] | None = None,
    dimensions: list[str] | None = None,
    time_grain: str | None = None,
    user: str = "unknown",
) -> dict:
    """Handle the structured query path."""
    wg = workgroup or _config.athena.workgroup
    # Disambiguate (with vector fallback for metric matching)
    disambiguation = disambiguate(question, graph, embedding_config=_config.embedding)

    # Check if a metric matches
    if disambiguation.metrics:
        best_metric = disambiguation.metrics[0]

        # Check if metric is disabled
        metric_enabled = graph.query(
            "MATCH (m:Metric {metric_id: $mid}) RETURN m.enabled AS enabled, m.disabled_reason AS reason",
            {"mid": best_metric["metric_id"]},
        )
        if metric_enabled and metric_enabled[0].get("enabled") is False:
            reason = metric_enabled[0].get("reason", "unknown")
            raise HTTPException(503, f"Metric '{best_metric.get('name', best_metric['metric_id'])}' is disabled: {reason}")

        compiled = compile_metric(
            metric_id=best_metric["metric_id"],
            graph=graph,
            dimensions=dimensions,
            filters=filters,
            limit=_config.max_query_rows,
            time_grain=time_grain,
        )
        if compiled.is_valid:
            # Firewall check
            if _firewall:
                fw = _firewall.validate(compiled.sql)
                if not fw.allowed:
                    raise HTTPException(403, fw.reason)

            # Execute via resolved executor
            result = await _execute_on_datasource(compiled.sql, best_metric["metric_id"], graph, _config.max_query_rows)
            return {
                "intent": "metric",
                "query_type": "governed",
                "metric_name": compiled.metric_name,
                "metric_id": best_metric["metric_id"],
                "datasource_id": _resolve_datasource_id_for_metric(best_metric["metric_id"], graph),
                "sql": compiled.sql,
                "results": result,
            }
        # The metric matched but its own rules refused this request (e.g. an
        # undeclared time_grain). Surface that instead of falling through to
        # ungoverned LLM SQL, which would answer the question the governance
        # just rejected — and against whatever table the LLM happens to pick.
        raise HTTPException(
            400,
            f"Governed metric '{compiled.metric_name or best_metric['metric_id']}' "
            f"cannot answer this request: {'; '.join(compiled.errors)}",
        )

    # No metric match. If the ungoverned kill switch is on, refuse before calling
    # the LLM at all — the question is recorded so admins can see which metrics
    # users are asking for.
    if _config.block_ungoverned_queries:
        record_blocked_query(
            graph, question=question, user=user, route=route_result.route,
        )
        raise HTTPException(403, BLOCK_REASON)

    # No metric match — generate SQL via LLM, grounded in the full catalog schema.
    sql = generate_sql(question, graph, _config.bedrock.query_model)

    # Firewall check
    if _firewall:
        fw = _firewall.validate(sql)
        if not fw.allowed:
            raise HTTPException(403, fw.reason)

    # Ungoverned SQL is still bound to the correct engine: resolve the datasource
    # deterministically from the tables the generated SQL references (the LLM never
    # picks an engine). Single datasource → run there; multiple → reject; untagged
    # → legacy Athena default.
    resolved_ds = _resolve_datasource_for_sql(sql, graph)
    result = await _execute_ungoverned(sql, resolved_ds, wg, _config.max_query_rows)
    return {
        "intent": "analytical",
        "query_type": "ungoverned",
        "datasource_id": resolved_ds or "",
        "sql": sql,
        "results": result,
        "hint": _unapproved_metric_hint(question, graph),
    }


@router.post("/sql")
async def direct_sql_query(request: SQLQueryRequest, http_request: Request):
    """Execute a direct SQL query (with firewall validation)."""
    user = user_from_request(http_request)
    # Firewall check
    if _firewall:
        fw = _firewall.validate(request.sql)
        if not fw.allowed:
            record_query(
                action="direct_sql", user=user, query_type="ungoverned",
                sql=request.sql, firewall_verdict="blocked", error=fw.reason,
            )
            raise HTTPException(403, fw.reason)

    result = execute_query(
        sql=request.sql,
        workgroup=request.workgroup or _config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        database=request.database,
        catalog=request.catalog,
        max_rows=request.max_rows,
    )
    record_query(
        action="direct_sql", user=user, query_type="ungoverned",
        sql=request.sql, firewall_verdict="allowed",
        row_count=int(result.get("row_count") or 0),
        duration_ms=int(result.get("duration_ms") or 0),
        error=str(result.get("error") or ""),
    )
    return {"sql": request.sql, "results": result}


@router.post("/plan", response_model=QueryPlan)
async def plan_query_endpoint(request: NLQueryRequest, http_request: Request):
    """Plan a query without executing it.

    Returns the SQL, route, matched tables, join paths, and vector search params
    so an external agent can execute them via separate MCP servers (Athena, S3Vectors).
    """
    graph = _get_graph()
    user = user_from_request(http_request)

    route_result = route_query(request.question, graph, embedding_config=_config.embedding)
    plan = QueryPlan(route=route_result.route)

    # Parse explicit filters
    filter_clauses = [
        CompilerFilter(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in request.filters
    ] if request.filters else None

    # Structured path — produce SQL without executing
    if route_result.route in ("structured", "both"):
        try:
            disambiguation = disambiguate(request.question, graph, embedding_config=_config.embedding)
            plan.tables = disambiguation.tables
            plan.join_paths = disambiguation.join_paths

            # Check if a metric matches
            governance_refused = False
            if disambiguation.metrics:
                best_metric = disambiguation.metrics[0]

                compiled = compile_metric(
                    metric_id=best_metric["metric_id"],
                    graph=graph,
                    dimensions=request.dimensions or None,
                    filters=filter_clauses,
                    limit=request.max_rows,
                    time_grain=request.time_grain,
                )
                if compiled.is_valid:
                    plan.intent = "metric"
                    plan.query_type = "governed"
                    plan.metric_name = compiled.metric_name
                    plan.sql = compiled.sql
                else:
                    # Matched a governed metric that refused this request — report it
                    # rather than planning ungoverned SQL for the same question.
                    plan.intent = "metric"
                    plan.query_type = "governed"
                    plan.metric_name = compiled.metric_name
                    plan.error = (
                        f"Governed metric '{compiled.metric_name or best_metric['metric_id']}' "
                        f"cannot answer this request: {'; '.join(compiled.errors)}"
                    )
                    governance_refused = True

            # No metric match. Honour the same kill switch as execution — planning
            # ungoverned SQL here would hand an agent the very query the block is
            # meant to prevent, for it to run on its own executor.
            if not plan.sql and not governance_refused and _config.block_ungoverned_queries:
                record_blocked_query(
                    graph, question=request.question, user=user,
                    route=route_result.route,
                )
                plan.intent = "analytical"
                plan.query_type = "ungoverned"
                plan.error = BLOCK_REASON
                governance_refused = True

            # No metric match — generate SQL via LLM, grounded in the full catalog schema.
            if not plan.sql and not governance_refused:
                sql = generate_sql(request.question, graph, _config.bedrock.query_model)
                plan.intent = "analytical"
                plan.query_type = "ungoverned"
                plan.sql = sql
                plan.hint = _unapproved_metric_hint(request.question, graph)

            # Firewall check — include result in plan, don't throw
            if plan.sql and _firewall:
                fw = _firewall.validate(plan.sql)
                if not fw.allowed:
                    plan.firewall = "blocked"
                    plan.firewall_reason = fw.reason
                    plan.denied_tables = fw.denied_tables

        except Exception as e:
            logger.error("Plan structured failed: %s", e)
            plan.error = str(e)

    # Unstructured path — return vector search params without executing
    if route_result.route in ("unstructured", "both"):
        docs = graph.query(
            "MATCH (d:Document) WHERE d.vector_bucket IS NOT NULL "
            "RETURN d.vector_bucket AS bucket, d.vector_index AS index_name"
        )
        plan.vector_searches = [
            {"bucket": d["bucket"], "index": d["index_name"]}
            for d in docs if d.get("bucket") and d.get("index_name")
        ]
        if not plan.intent:
            plan.intent = "document"
            plan.query_type = "document"

    return plan


class SimilarityTestRequest(BaseModel):
    question: str


@router.post("/similarity-test")
async def similarity_test(request: SimilarityTestRequest):
    """Test metric matching: returns both full-text and vector results with scores.

    Useful for tuning thresholds and understanding how queries are resolved.
    """
    graph = _get_graph()

    # Full-text search over content words only, mirroring the live routing path.
    ft_query = strip_fulltext_stopwords(request.question)
    fulltext_hits = (
        graph.query(
            "CALL db.index.fulltext.queryNodes('metric_search', $q) YIELD node, score "
            "WHERE score > 0.1 "
            "WITH node AS m, score "
            "OPTIONAL MATCH (m)-[:MEASURES]->(t:Table) "
            "RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition, "
            "m.synonyms AS synonyms, COALESCE(t.full_name, '') AS source_table, score "
            "ORDER BY score DESC LIMIT 10",
            {"q": ft_query},
        )
        if ft_query
        else []
    )

    # Vector search (if enabled)
    vector_hits = []
    if _config.embedding.enabled:
        try:
            from src.query.embeddings import get_embedding

            question_vec = get_embedding(
                request.question, _config.embedding.model_id, _config.embedding.dimensions
            )
            if question_vec:
                vector_hits = graph.query(
                    "CALL db.index.vector.queryNodes('metric_embedding', 10, $vec) "
                    "YIELD node, score "
                    "WHERE score > 0.1 "
                    "WITH node AS m, score "
                    "OPTIONAL MATCH (m)-[:MEASURES]->(t:Table) "
                    "RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition, "
                    "m.synonyms AS synonyms, COALESCE(t.full_name, '') AS source_table, score "
                    "ORDER BY score DESC LIMIT 10",
                    {"vec": question_vec},
                )
        except Exception as e:
            logger.debug("Similarity test vector search failed: %s", e)

    # Determine which would be selected by the current routing logic
    ft_threshold = _config.embedding.fulltext_confidence_threshold
    vec_min = _config.embedding.vector_min_score
    match_min = _config.embedding.metric_match_min_score
    best_ft = fulltext_hits[0] if fulltext_hits else None
    best_vec = vector_hits[0] if vector_hits else None

    if best_ft and best_ft.get("score", 0) >= ft_threshold:
        resolution = "fulltext"
        selected = best_ft["name"]
    elif best_vec and best_vec.get("score", 0) >= vec_min:
        resolution = "vector"
        selected = best_vec["name"]
    elif best_ft:
        resolution = "fulltext_weak"
        selected = best_ft["name"]
    else:
        resolution = "none"
        selected = None

    # The live router (disambiguate) governs a question whenever ANY metric clears
    # metric_match_min_score — it has no notion of a "weak" match. Report that
    # verdict separately so this page reflects real behaviour rather than implying
    # a weak full-text hit would fall through to ungoverned SQL.
    would_be_governed = bool(
        (best_ft and best_ft.get("score", 0) > match_min)
        or (best_vec and best_vec.get("score", 0) >= vec_min)
    )

    return {
        "question": request.question,
        "fulltext_query": ft_query,
        "fulltext_results": fulltext_hits,
        "vector_results": vector_hits,
        "resolution": resolution,
        "selected_metric": selected,
        "would_be_governed": would_be_governed,
        "thresholds": {
            "metric_match_min_score": match_min,
            "fulltext_confidence": ft_threshold,
            "vector_min_score": vec_min,
        },
    }


class ComposeRequest(BaseModel):
    """Compose multiple metrics into a single CTE query."""
    metric_ids: list[str]
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    time_grain: str | None = Field(default=None, description="Roll each metric's time axis up to this grain; must be declared on the metrics")
    execute: bool = False
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


@router.post("/compose")
async def compose_metrics_endpoint(request: ComposeRequest, http_request: Request):
    """Compose multiple governed metrics into a CTE query, optionally execute it."""
    graph = _get_graph()
    user = user_from_request(http_request)

    if len(request.metric_ids) < 2:
        raise HTTPException(400, "At least 2 metric IDs required for composition")

    filter_clauses = [
        CompilerFilter(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in request.filters
    ]

    compiled = compose_metrics(
        metric_ids=request.metric_ids,
        graph=graph,
        dimensions=request.dimensions,
        filters=filter_clauses,
        order_by=request.order_by,
        limit=request.limit or _config.max_query_rows,
        time_grain=request.time_grain,
    )

    if not compiled.is_valid:
        raise HTTPException(400, f"Compilation error: {'; '.join(compiled.errors)}")

    # Firewall check
    if _firewall:
        fw = _firewall.validate(compiled.sql)
        if not fw.allowed:
            raise HTTPException(403, fw.reason)

    response = {
        "metric": compiled.metric_name,
        "sql": compiled.sql,
        "query_type": "governed",
        "datasource_id": _resolve_datasource_id_for_metric(request.metric_ids[0], graph),
        "warnings": compiled.warnings,
    }

    if request.execute:
        # Route through the datasource-aware executor, resolving from the first
        # base metric (all base metrics in a valid composition share a datasource).
        result = await _execute_on_datasource(
            compiled.sql,
            request.metric_ids[0],
            graph,
            request.limit or _config.max_query_rows,
        )
        response["results"] = result
        record_query(
            action="compose", user=user, query_type="governed",
            metric_id=",".join(request.metric_ids),
            datasource_id=response["datasource_id"], sql=compiled.sql,
            firewall_verdict="allowed",
            row_count=int((result or {}).get("row_count") or 0),
            duration_ms=int((result or {}).get("duration_ms") or 0),
            error=str((result or {}).get("error") or ""),
        )
    else:
        record_query(
            action="compose", user=user, query_type="governed",
            metric_id=",".join(request.metric_ids),
            datasource_id=response["datasource_id"], sql=compiled.sql,
            firewall_verdict="allowed",
        )

    return response

"""Query API routes — natural language and direct SQL query execution."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.catalog.models import QueryPlan, QueryResponse
from src.config import SemanticLayerConfig
from src.graph.client import GraphClient
from src.metrics.compiler import compile_metric, compose_metrics, FilterClause as CompilerFilter
from src.query.athena_executor import execute_query
from src.query.disambiguator import disambiguate
from src.query.firewall import SQLFirewall
from src.query.generator import generate_sql
from src.query.router import route_query
from src.query.vectors_executor import search_vectors

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
    max_rows: int = Field(default=100, ge=1, le=1000)
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


class SQLQueryRequest(BaseModel):
    sql: str
    database: str | None = None
    catalog: str | None = None
    max_rows: int = Field(default=100, ge=1, le=1000)
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


@router.post("/natural-language", response_model=QueryResponse)
async def natural_language_query(request: NLQueryRequest):
    """Full natural language query pipeline.

    1. Route (graph-based: structured, unstructured, or both)
    2. For structured: disambiguate → check metrics → compile or generate SQL → firewall → execute
    3. For unstructured: search S3 Vectors
    """
    graph = _get_graph()

    # 1. Route the query
    route_result = route_query(request.question, graph, embedding_config=_config.embedding)
    response = QueryResponse(route=route_result.route)

    workgroup = request.workgroup or _config.athena.workgroup

    # Parse explicit filters for governed metrics
    filter_clauses = [
        CompilerFilter(column=f["column"], operator=f.get("operator", "="), value=f["value"])
        for f in request.filters
    ] if request.filters else None

    # 2. Handle structured path
    if route_result.route in ("structured", "both"):
        try:
            sql_result = _handle_structured(
                request.question, route_result, graph,
                workgroup=workgroup, filters=filter_clauses,
                dimensions=request.dimensions or None,
            )
            response.intent = sql_result.get("intent", "analytical")
            response.query_type = sql_result.get("query_type", "ungoverned")
            response.metric_name = sql_result.get("metric_name")
            response.sql = sql_result.get("sql")
            response.results = sql_result.get("results")
        except Exception as e:
            logger.error("Structured query failed: %s", e)
            response.error = str(e)

    # 3. Handle unstructured path
    if route_result.route in ("unstructured", "both"):
        try:
            vector_results = search_vectors(request.question, graph)
            response.vector_results = vector_results
            if not response.intent:
                response.intent = "document"
                response.query_type = "document"
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            if not response.error:
                response.error = str(e)

    return response


def _handle_structured(
    question: str, route_result, graph: GraphClient,
    workgroup: str | None = None,
    filters: list[CompilerFilter] | None = None,
    dimensions: list[str] | None = None,
) -> dict:
    """Handle the structured query path."""
    wg = workgroup or _config.athena.workgroup
    # Disambiguate (with vector fallback for metric matching)
    disambiguation = disambiguate(question, graph, embedding_config=_config.embedding)

    # Check if a metric matches
    if disambiguation.metrics:
        best_metric = disambiguation.metrics[0]

        compiled = compile_metric(
            metric_id=best_metric["metric_id"],
            graph=graph,
            dimensions=dimensions,
            filters=filters,
            limit=_config.max_query_rows,
        )
        if compiled.is_valid:
            # Firewall check
            if _firewall:
                fw = _firewall.validate(compiled.sql)
                if not fw.allowed:
                    raise HTTPException(403, fw.reason)

            result = execute_query(
                sql=compiled.sql,
                workgroup=wg,
                output_location=_config.athena.output_bucket,
                max_rows=_config.max_query_rows,
            )
            return {
                "intent": "metric",
                "query_type": "governed",
                "metric_name": compiled.metric_name,
                "sql": compiled.sql,
                "results": result,
            }

    # No metric match — generate SQL via LLM
    sql = generate_sql(question, disambiguation, graph, _config.bedrock.query_model)

    # Firewall check
    if _firewall:
        fw = _firewall.validate(sql)
        if not fw.allowed:
            raise HTTPException(403, fw.reason)

    result = execute_query(
        sql=sql,
        workgroup=wg,
        output_location=_config.athena.output_bucket,
        max_rows=_config.max_query_rows,
    )
    return {
        "intent": "analytical",
        "query_type": "ungoverned",
        "sql": sql,
        "results": result,
    }


@router.post("/sql")
async def direct_sql_query(request: SQLQueryRequest):
    """Execute a direct SQL query (with firewall validation)."""
    # Firewall check
    if _firewall:
        fw = _firewall.validate(request.sql)
        if not fw.allowed:
            raise HTTPException(403, fw.reason)

    result = execute_query(
        sql=request.sql,
        workgroup=request.workgroup or _config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        database=request.database,
        catalog=request.catalog,
        max_rows=request.max_rows,
    )
    return {"sql": request.sql, "results": result}


@router.post("/plan", response_model=QueryPlan)
async def plan_query_endpoint(request: NLQueryRequest):
    """Plan a query without executing it.

    Returns the SQL, route, matched tables, join paths, and vector search params
    so an external agent can execute them via separate MCP servers (Athena, S3Vectors).
    """
    graph = _get_graph()

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
            if disambiguation.metrics:
                best_metric = disambiguation.metrics[0]

                compiled = compile_metric(
                    metric_id=best_metric["metric_id"],
                    graph=graph,
                    dimensions=request.dimensions or None,
                    filters=filter_clauses,
                    limit=request.max_rows,
                )
                if compiled.is_valid:
                    plan.intent = "metric"
                    plan.query_type = "governed"
                    plan.metric_name = compiled.metric_name
                    plan.sql = compiled.sql

            # No metric match — generate SQL via LLM
            if not plan.sql:
                sql = generate_sql(request.question, disambiguation, graph, _config.bedrock.query_model)
                plan.intent = "analytical"
                plan.query_type = "ungoverned"
                plan.sql = sql

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

    # Full-text search
    fulltext_hits = graph.query(
        "CALL db.index.fulltext.queryNodes('metric_search', $q) YIELD node, score "
        "WHERE score > 0.1 "
        "WITH node AS m, score "
        "OPTIONAL MATCH (m)-[:MEASURES]->(t:Table) "
        "RETURN m.metric_id AS metric_id, m.name AS name, m.definition AS definition, "
        "m.synonyms AS synonyms, COALESCE(t.full_name, '') AS source_table, score "
        "ORDER BY score DESC LIMIT 10",
        {"q": request.question},
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

    return {
        "question": request.question,
        "fulltext_results": fulltext_hits,
        "vector_results": vector_hits,
        "resolution": resolution,
        "selected_metric": selected,
        "thresholds": {
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
    execute: bool = False
    workgroup: str | None = Field(default=None, description="Athena workgroup override (defaults to config value, or 'primary')")


@router.post("/compose")
async def compose_metrics_endpoint(request: ComposeRequest):
    """Compose multiple governed metrics into a CTE query, optionally execute it."""
    graph = _get_graph()

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
    }

    if request.execute:
        result = execute_query(
            sql=compiled.sql,
            workgroup=request.workgroup or _config.athena.workgroup,
            output_location=_config.athena.output_bucket,
            max_rows=request.limit or _config.max_query_rows,
        )
        response["results"] = result

    return response

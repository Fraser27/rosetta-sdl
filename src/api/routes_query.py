"""Query API routes — natural language and direct SQL query execution."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.catalog.models import QueryResponse
from src.config import SemanticLayerConfig
from src.graph.client import GraphClient
from src.metrics.compiler import compile_metric
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
    max_rows: int = Field(default=100, ge=1, le=1000)


class SQLQueryRequest(BaseModel):
    sql: str
    database: str | None = None
    catalog: str | None = None
    max_rows: int = Field(default=100, ge=1, le=1000)


@router.post("/natural-language", response_model=QueryResponse)
async def natural_language_query(request: NLQueryRequest):
    """Full natural language query pipeline.

    1. Route (graph-based: structured, unstructured, or both)
    2. For structured: disambiguate → check metrics → compile or generate SQL → firewall → execute
    3. For unstructured: search S3 Vectors
    """
    graph = _get_graph()

    # 1. Route the query
    route_result = route_query(request.question, graph)
    response = QueryResponse(route=route_result.route)

    # 2. Handle structured path
    if route_result.route in ("structured", "both"):
        try:
            sql_result = _handle_structured(request.question, route_result, graph)
            response.intent = sql_result.get("intent", "analytical")
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
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            if not response.error:
                response.error = str(e)

    return response


def _handle_structured(question: str, route_result, graph: GraphClient) -> dict:
    """Handle the structured query path."""
    # Disambiguate
    disambiguation = disambiguate(question, graph)

    # Check if a metric matches
    if disambiguation.metrics:
        best_metric = disambiguation.metrics[0]
        compiled = compile_metric(
            metric_id=best_metric["metric_id"],
            graph=graph,
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
                workgroup=_config.athena.workgroup,
                output_location=_config.athena.output_bucket,
                max_rows=_config.max_query_rows,
            )
            return {
                "intent": "metric",
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
        workgroup=_config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        max_rows=_config.max_query_rows,
    )
    return {
        "intent": "analytical",
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
        workgroup=_config.athena.workgroup,
        output_location=_config.athena.output_bucket,
        database=request.database,
        catalog=request.catalog,
        max_rows=request.max_rows,
    )
    return {"sql": request.sql, "results": result}

"""FastAPI application — main entry point with Mangum adapter for Lambda."""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import routes_admin, routes_catalog, routes_metrics, routes_query
from src.auth import CognitoAuthMiddleware
from src.config import load_config
from src.graph.client import GraphClient
from src.graph.schema import init_schema
from src.query.firewall import SQLFirewall

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("semantic-layer")

# Load config
config = load_config()

# Initialize Neo4j
graph = GraphClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)

# Initialize schema (constraints + indexes)
try:
    init_schema(graph)
except Exception as e:
    logger.warning("Could not init schema (Neo4j may not be ready): %s", e)

# Initialize SQL firewall
allowed_tables: set[str] | None = set(config.allowed_tables) if config.allowed_tables else None
firewall = SQLFirewall(allowed_tables)

# Create FastAPI app
app = FastAPI(
    title="Semantic Layer API",
    description="Domain-agnostic semantic layer with Neo4j ontology for AWS data lakes",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cognito auth — disabled when COGNITO_USER_POOL_ID is not set (local dev)
app.add_middleware(CognitoAuthMiddleware)

# Initialize route modules with shared dependencies
routes_catalog.init(graph)
routes_metrics.init(graph, config, firewall)
routes_query.init(graph, config, firewall)
routes_admin.init(graph, config)

# Mount routers
app.include_router(routes_catalog.router)
app.include_router(routes_metrics.router)
app.include_router(routes_query.router)
app.include_router(routes_admin.router)


@app.get("/health")
async def health():
    neo4j_ok = graph.verify_connectivity()
    return {
        "status": "healthy" if neo4j_ok else "degraded",
        "service": "semantic-layer",
        "version": "0.1.0",
        "neo4j": "connected" if neo4j_ok else "disconnected",
    }


@app.get("/")
async def root():
    return {
        "service": "Semantic Layer API",
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "catalog": "/catalog/*",
            "metrics": "/metrics/*",
            "query": "/query/*",
            "admin": "/admin/*",
        },
    }


# Mangum handler for AWS Lambda
try:
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except ImportError:
    handler = None

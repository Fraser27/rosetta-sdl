"""FastAPI application — main entry point with Mangum adapter for Lambda."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import routes_admin, routes_catalog, routes_datasources, routes_metrics, routes_query
from src.auth import CognitoAuthMiddleware
from src.config import load_config
from src.executors.registry import registry
from src.graph.client import GraphClient
from src.graph.schema import init_schema
from src.health.poller import HealthPoller
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

# Initialize health poller
health_poller = HealthPoller(graph, interval=config.health_poller.interval)


def _seed_datasources():
    """Seed datasource executors from config and/or graph."""
    from src.executors.athena import AthenaExecutor
    from src.executors.redshift import RedshiftServerlessExecutor

    executor_types = {
        "athena": AthenaExecutor,
        "redshift_serverless": RedshiftServerlessExecutor,
    }

    # Seed from config file datasources
    for ds_cfg in config.datasources:
        if not ds_cfg.datasource_id:
            continue
        ds_config = {
            "endpoint": ds_cfg.endpoint,
            "database": ds_cfg.database,
            "region": ds_cfg.region,
            "secret_arn": ds_cfg.secret_arn,
            "output_location": ds_cfg.output_location,
        }
        cls = executor_types.get(ds_cfg.type)
        if cls:
            executor = cls(ds_cfg.datasource_id, ds_cfg.name, ds_config)
            registry.register(ds_cfg.datasource_id, executor)

    # Also seed a default Athena executor if no datasources configured
    if len(registry) == 0 and config.athena.workgroup:
        default_config = {
            "endpoint": config.athena.workgroup,
            "output_location": config.athena.output_bucket,
            "region": "us-east-1",
        }
        executor = AthenaExecutor("ds_default_athena", "Default Athena", default_config)
        registry.register("ds_default_athena", executor)
        # Ensure the default datasource node exists in graph
        from src.graph.queries import UPSERT_DATASOURCE_FULL
        from datetime import datetime, timezone
        try:
            graph.write(UPSERT_DATASOURCE_FULL, {
                "datasource_id": "ds_default_athena",
                "name": "Default Athena",
                "type": "athena",
                "endpoint": config.athena.workgroup,
                "database": "",
                "region": "us-east-1",
                "secret_arn": None,
                "status": "unknown",
                "enabled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_health_check": None,
            })
        except Exception as e:
            logger.warning("Could not seed default Athena datasource: %s", e)


_seed_datasources()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle — start/stop health poller."""
    await health_poller.start()
    yield
    await health_poller.stop()


# Create FastAPI app
app = FastAPI(
    title="Semantic Layer API",
    description="Domain-agnostic semantic layer with Neo4j ontology for AWS data lakes",
    version="0.1.0",
    lifespan=lifespan,
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
routes_datasources.init(graph, poller=health_poller)

# Mount routers
app.include_router(routes_catalog.router)
app.include_router(routes_metrics.router)
app.include_router(routes_query.router)
app.include_router(routes_admin.router)
app.include_router(routes_datasources.router)


@app.get("/health")
async def health():
    neo4j_ok = graph.verify_connectivity()
    datasource_health = {
        ds_id: status.value for ds_id, status in health_poller.cached_status.items()
    }
    return {
        "status": "healthy" if neo4j_ok else "degraded",
        "service": "semantic-layer",
        "version": "0.1.0",
        "neo4j": "connected" if neo4j_ok else "disconnected",
        "datasources": datasource_health,
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

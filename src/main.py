"""FastAPI application — main entry point with Mangum adapter for Lambda."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import routes_admin, routes_audit, routes_catalog, routes_datasources, routes_metrics, routes_query
from src.audit import init_audit
from src.auth import AUTH_DISABLED, COGNITO_USER_POOL_ID, CognitoAuthMiddleware
from src.config import load_config
from src.constants import DEFAULT_AWS_REGION, DEFAULT_DATASOURCE_ID
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

# Hydrate runtime config overrides persisted in the graph (survives restart).
try:
    from src.constants import SYSTEM_CONFIG_KEY
    from src.graph.queries import GET_SYSTEM_CONFIG
    rows = graph.query(GET_SYSTEM_CONFIG, {"key": SYSTEM_CONFIG_KEY})
    if rows:
        if rows[0].get("query_model"):
            config.bedrock.query_model = rows[0]["query_model"]
            logger.info("Loaded persisted query_model override: %s", config.bedrock.query_model)
        if rows[0].get("s3vectors_embedding_model"):
            config.embedding.s3vectors_model_id = rows[0]["s3vectors_embedding_model"]
            logger.info("Loaded persisted s3vectors embedding model: %s", config.embedding.s3vectors_model_id)
        if rows[0].get("enrichment_model"):
            config.bedrock.enrichment_model = rows[0]["enrichment_model"]
            logger.info("Loaded persisted enrichment model: %s", config.bedrock.enrichment_model)
        if rows[0].get("block_ungoverned_queries") is not None:
            config.block_ungoverned_queries = bool(rows[0]["block_ungoverned_queries"])
            logger.info(
                "Ungoverned queries are %s",
                "BLOCKED" if config.block_ungoverned_queries else "allowed",
            )
        for key, target in (
            ("metric_match_min_score", "metric_match_min_score"),
            ("fulltext_confidence_threshold", "fulltext_confidence_threshold"),
            ("vector_min_score", "vector_min_score"),
        ):
            if rows[0].get(key) is not None:
                setattr(config.embedding, target, float(rows[0][key]))
                logger.info("Loaded persisted %s: %s", key, getattr(config.embedding, target))
except Exception as e:
    logger.warning("Could not load persisted system config: %s", e)

# Initialize SQL firewall.
#
# FIREWALL_MODE controls how the table allowlist is derived (default "catalog"):
#   * "catalog"  — allow only tables currently known to the graph catalog.
#                  The allowlist is resolved LAZILY on each validate() (with
#                  light caching), so tables added by a scan AFTER startup
#                  become allowed automatically, and a fresh/empty catalog at
#                  boot does not brick the instance (it simply denies queries
#                  until the catalog is populated — fail-closed, not fail-open).
#                  Any explicit config.allowed_tables are unioned in as well.
#   * "explicit" — enforce ONLY the static config.allowed_tables set.
#   * "disabled" — opt-out; allow everything (logs a loud warning).
#
# Note: an EMPTY allowlist now means "deny all", never "allow all". The only
# way to allow arbitrary tables is the explicit "disabled" mode. This ensures
# an out-of-the-box deploy never silently permits unauthorized table access.
_firewall_mode = os.environ.get("FIREWALL_MODE", "catalog").strip().lower()
_static_allowed: set[str] = {t for t in config.allowed_tables} if config.allowed_tables else set()


def _catalog_allowlist() -> set[str]:
    """Return the set of table full_names currently known to the graph catalog."""
    from src.graph.queries import LIST_TABLES

    rows = graph.query(LIST_TABLES)
    return {row["full_name"] for row in rows if row.get("full_name")}


if _firewall_mode == "disabled":
    logger.warning(
        "SQL FIREWALL IS DISABLED (FIREWALL_MODE=disabled) — all table access "
        "is permitted. This is unsafe outside local development."
    )
    firewall = SQLFirewall(allow_all=True)
elif _firewall_mode == "explicit":
    firewall = SQLFirewall(_static_allowed)
else:  # "catalog" (default) and any unrecognized value fall back to catalog mode
    if _firewall_mode != "catalog":
        logger.warning("Unknown FIREWALL_MODE=%r — defaulting to 'catalog'", _firewall_mode)
    firewall = SQLFirewall(_static_allowed, allowlist_provider=_catalog_allowlist)

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
        _default_region = (
            os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or DEFAULT_AWS_REGION
        )
        default_config = {
            "endpoint": config.athena.workgroup,
            "output_location": config.athena.output_bucket,
            "region": _default_region,
        }
        executor = AthenaExecutor(DEFAULT_DATASOURCE_ID, "Default Athena", default_config)
        registry.register(DEFAULT_DATASOURCE_ID, executor)
        # Ensure the default datasource node exists in graph
        from src.graph.queries import UPSERT_DATASOURCE_FULL
        from datetime import datetime, timezone
        try:
            graph.write(UPSERT_DATASOURCE_FULL, {
                "datasource_id": DEFAULT_DATASOURCE_ID,
                "name": "Default Athena",
                "type": "athena",
                "endpoint": config.athena.workgroup,
                "database": "",
                "region": _default_region,
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

# CORS — allowlist read from CORS_ALLOWED_ORIGINS (comma-separated). Defaults to
# a safe local set. A wildcard "*" is only honored if explicitly configured via
# the env var, and credentials are disabled in that case (browsers forbid the
# combination of wildcard origin + credentials anyway).
_default_cors_origins = ["http://localhost:3000", "http://localhost:5173"]
_cors_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] or _default_cors_origins
_cors_allow_credentials = "*" not in _cors_origins
if not _cors_allow_credentials:
    logger.warning(
        "CORS_ALLOWED_ORIGINS contains '*' — allow_credentials disabled "
        "(wildcard origin + credentials is not permitted)."
    )
logger.info("CORS allowed origins: %s", _cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cognito auth — fail-closed unless a user pool is configured, or auth is
# explicitly disabled for local dev via ALLOW_INSECURE_NO_AUTH.
if AUTH_DISABLED:
    logger.warning(
        "AUTHENTICATION IS DISABLED (no COGNITO_USER_POOL_ID + "
        "ALLOW_INSECURE_NO_AUTH is set) — every non-public endpoint is OPEN, "
        "including destructive /admin/* routes. LOCAL DEVELOPMENT ONLY."
    )
elif not COGNITO_USER_POOL_ID:
    logger.warning(
        "Cognito is not configured and ALLOW_INSECURE_NO_AUTH is not set — "
        "auth is FAIL-CLOSED: non-public requests will be rejected with 503. "
        "Set COGNITO_USER_POOL_ID, or ALLOW_INSECURE_NO_AUTH=1 for local dev."
    )
app.add_middleware(CognitoAuthMiddleware)

# Initialize route modules with shared dependencies
init_audit(graph)
routes_catalog.init(graph, config)
routes_metrics.init(graph, config, firewall)
routes_query.init(graph, config, firewall)
routes_admin.init(graph, config)
routes_datasources.init(graph, poller=health_poller)
routes_audit.init(graph)

# Mount routers
app.include_router(routes_catalog.router)
app.include_router(routes_metrics.router)
app.include_router(routes_query.router)
app.include_router(routes_admin.router)
app.include_router(routes_datasources.router)
app.include_router(routes_audit.router)


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

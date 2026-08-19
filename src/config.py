"""Configuration loader — reads config.yaml + environment variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.constants import DEFAULT_ATHENA_WORKGROUP, DEFAULT_AWS_REGION


@dataclass
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""  # no baked-in default; supply via NEO4J_PASSWORD env var


@dataclass
class DatabaseConfig:
    name: str = ""
    glue_database: str = ""
    catalog_type: str = "glue"  # glue | iceberg | federated


@dataclass
class VectorBucketConfig:
    name: str = ""
    bucket: str = ""


@dataclass
class AthenaConfig:
    workgroup: str = DEFAULT_ATHENA_WORKGROUP
    output_bucket: str = ""


@dataclass
class BedrockConfig:
    # Nova 2 Lite via cross-region inference profile; invoked through the Converse
    # API so any provider works. Both models are editable in the Configurations UI.
    query_model: str = "us.amazon.nova-2-lite-v1:0"
    enrichment_model: str = "us.amazon.nova-2-lite-v1:0"


@dataclass
class EmbeddingConfig:
    model_id: str = "amazon.titan-embed-text-v2:0"
    dimensions: int = 1024
    # The gate that actually decides governed vs ungoverned: a question is served by
    # a governed metric iff some approved metric clears this Lucene score. Raise it to
    # make governance stricter (more questions fall through to LLM SQL); lower it to
    # catch looser phrasings at the risk of false matches.
    metric_match_min_score: float = 0.3
    fulltext_confidence_threshold: float = 1.0  # below this Lucene score, try vector
    vector_min_score: float = 0.6  # minimum cosine similarity to accept
    enabled: bool = True  # kill-switch for vector search
    # Model used to embed the query before an S3 Vectors kNN search. Configurable
    # in the UI so it can be matched to whatever the vectors were ingested with.
    s3vectors_model_id: str = "amazon.titan-embed-text-v2:0"


@dataclass
class DataSourceConfig:
    datasource_id: str = ""
    name: str = ""
    type: str = "athena"  # "athena" | "redshift_serverless"
    endpoint: str = ""  # workgroup name
    database: str = ""
    region: str = DEFAULT_AWS_REGION
    secret_arn: str = ""
    output_location: str = ""  # S3 output (Athena only)


@dataclass
class HealthPollerConfig:
    interval: int = 30  # seconds between health checks
    failure_threshold: int = 3  # consecutive failures before unhealthy


@dataclass
class SemanticLayerConfig:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    databases: list[DatabaseConfig] = field(default_factory=list)
    vector_buckets: list[VectorBucketConfig] = field(default_factory=list)
    athena: AthenaConfig = field(default_factory=AthenaConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    datasources: list[DataSourceConfig] = field(default_factory=list)
    health_poller: HealthPollerConfig = field(default_factory=HealthPollerConfig)
    metrics_file: str = ""
    allowed_tables: list[str] = field(default_factory=list)
    max_query_rows: int = 500
    # When true, questions matching no governed metric are refused instead of
    # answered with LLM-generated SQL. Toggled from the Governance page and
    # persisted on the :SystemConfig node, so it survives restarts.
    block_ungoverned_queries: bool = False


def load_config(config_path: str | None = None) -> SemanticLayerConfig:
    """Load config from YAML file, with env var overrides."""
    cfg = SemanticLayerConfig()

    # Try loading YAML file
    path = config_path or os.environ.get("CONFIG_FILE", "config.yaml")
    if Path(path).exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        if "neo4j" in data:
            cfg.neo4j = Neo4jConfig(**data["neo4j"])
        if "databases" in data:
            cfg.databases = [DatabaseConfig(**db) for db in data["databases"]]
        if "vector_buckets" in data:
            cfg.vector_buckets = [VectorBucketConfig(**vb) for vb in data["vector_buckets"]]
        if "athena" in data:
            cfg.athena = AthenaConfig(**data["athena"])
        if "bedrock" in data:
            cfg.bedrock = BedrockConfig(**data["bedrock"])
        if "embedding" in data:
            cfg.embedding = EmbeddingConfig(**data["embedding"])
        if "datasources" in data:
            cfg.datasources = [DataSourceConfig(**ds) for ds in data["datasources"]]
        if "health_poller" in data:
            cfg.health_poller = HealthPollerConfig(**data["health_poller"])
        cfg.metrics_file = data.get("metrics_file", cfg.metrics_file)
        cfg.allowed_tables = data.get("allowed_tables", cfg.allowed_tables)
        cfg.max_query_rows = data.get("max_query_rows", cfg.max_query_rows)

    # Environment variable overrides
    if v := os.environ.get("NEO4J_URI"):
        cfg.neo4j.uri = v
    if v := os.environ.get("NEO4J_USER"):
        cfg.neo4j.user = v
    if v := os.environ.get("NEO4J_PASSWORD"):
        cfg.neo4j.password = v
    if v := os.environ.get("GLUE_DATABASES"):
        cfg.databases = [
            DatabaseConfig(name=db.strip(), glue_database=db.strip())
            for db in v.split(",") if db.strip()
        ]
    if v := os.environ.get("VECTOR_BUCKETS"):
        cfg.vector_buckets = [
            VectorBucketConfig(name=b.strip(), bucket=b.strip())
            for b in v.split(",") if b.strip()
        ]
    if v := os.environ.get("ATHENA_WORKGROUP"):
        cfg.athena.workgroup = v
    if v := os.environ.get("ATHENA_OUTPUT_BUCKET"):
        cfg.athena.output_bucket = v
    if v := os.environ.get("METRICS_FILE"):
        cfg.metrics_file = v
    if os.environ.get("LOAD_SAMPLE_DATA", "").lower() in ("true", "1", "yes"):
        cfg.metrics_file = "sample/metrics.yaml"
    if v := os.environ.get("BEDROCK_QUERY_MODEL"):
        cfg.bedrock.query_model = v
    if v := os.environ.get("BEDROCK_ENRICHMENT_MODEL"):
        cfg.bedrock.enrichment_model = v
    if v := os.environ.get("EMBEDDING_MODEL_ID"):
        cfg.embedding.model_id = v
    if v := os.environ.get("EMBEDDING_DIMENSIONS"):
        cfg.embedding.dimensions = int(v)
    if v := os.environ.get("EMBEDDING_METRIC_MATCH_MIN_SCORE"):
        cfg.embedding.metric_match_min_score = float(v)
    if v := os.environ.get("EMBEDDING_FULLTEXT_THRESHOLD"):
        cfg.embedding.fulltext_confidence_threshold = float(v)
    if v := os.environ.get("EMBEDDING_VECTOR_MIN_SCORE"):
        cfg.embedding.vector_min_score = float(v)
    if os.environ.get("EMBEDDING_ENABLED", "").lower() in ("false", "0", "no"):
        cfg.embedding.enabled = False

    return cfg

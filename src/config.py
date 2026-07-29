"""Configuration loader — reads config.yaml + environment variable overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "semantic-layer"


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
    workgroup: str = "primary"
    output_bucket: str = ""


@dataclass
class BedrockConfig:
    query_model: str = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    enrichment_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


@dataclass
class EmbeddingConfig:
    model_id: str = "amazon.titan-embed-text-v2:0"
    dimensions: int = 1024
    fulltext_confidence_threshold: float = 1.0  # below this Lucene score, try vector
    vector_min_score: float = 0.6  # minimum cosine similarity to accept
    enabled: bool = True  # kill-switch for vector search


@dataclass
class SemanticLayerConfig:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    databases: list[DatabaseConfig] = field(default_factory=list)
    vector_buckets: list[VectorBucketConfig] = field(default_factory=list)
    athena: AthenaConfig = field(default_factory=AthenaConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    metrics_file: str = ""
    allowed_tables: list[str] = field(default_factory=list)
    max_query_rows: int = 500
    aws_region: str = ""


def _load_neo4j_secret(secret_name: str, region: str | None) -> dict[str, str]:
    """Fetch Neo4j credentials from AWS Secrets Manager.

    Kept in its own function with a lazy import so the default config path
    stays a pure env/file reader with no network calls or AWS dependency at
    import time. Only invoked when NEO4J_SECRET_NAME is set (the deployed Aura
    path), so the credentials are never written to disk on the instance. Runs
    before the client factory is configured, so it takes the region explicitly.
    """
    import boto3

    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def load_config(config_path: str | None = None) -> SemanticLayerConfig:
    """Load config from YAML file, with env var overrides."""
    # Populate os.environ from a local .env file (if present) before overrides
    # run. Existing environment variables take precedence over .env values.
    load_dotenv()

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
        cfg.metrics_file = data.get("metrics_file", cfg.metrics_file)
        cfg.allowed_tables = data.get("allowed_tables", cfg.allowed_tables)
        cfg.max_query_rows = data.get("max_query_rows", cfg.max_query_rows)
        cfg.aws_region = data.get("aws_region", cfg.aws_region)

    # Environment variable overrides
    # Region is resolved first so the secret fetch below can use it. Prefer
    # AWS_DEFAULT_REGION (what boto3 honors natively); fall back to AWS_REGION so
    # a shell with only AWS_REGION set still resolves a region instead of none.
    if v := os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION"):
        cfg.aws_region = v
    if v := os.environ.get("NEO4J_URI"):
        cfg.neo4j.uri = v
    if v := os.environ.get("NEO4J_USER"):
        cfg.neo4j.user = v
    if v := os.environ.get("NEO4J_PASSWORD"):
        cfg.neo4j.password = v
    # When pointed at a Secrets Manager secret, it is the source of truth for
    # the Neo4j credentials and takes precedence over the individual env vars.
    if secret_name := os.environ.get("NEO4J_SECRET_NAME"):
        creds = _load_neo4j_secret(secret_name, cfg.aws_region or None)
        cfg.neo4j.uri = creds["uri"]
        cfg.neo4j.user = creds["user"]
        cfg.neo4j.password = creds["password"]
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
    if v := os.environ.get("EMBEDDING_FULLTEXT_THRESHOLD"):
        cfg.embedding.fulltext_confidence_threshold = float(v)
    if v := os.environ.get("EMBEDDING_VECTOR_MIN_SCORE"):
        cfg.embedding.vector_min_score = float(v)
    if os.environ.get("EMBEDDING_ENABLED", "").lower() in ("false", "0", "no"):
        cfg.embedding.enabled = False

    return cfg

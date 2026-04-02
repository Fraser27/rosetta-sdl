"""Configuration loader — reads config.yaml + environment variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
class SemanticLayerConfig:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    databases: list[DatabaseConfig] = field(default_factory=list)
    vector_buckets: list[VectorBucketConfig] = field(default_factory=list)
    athena: AthenaConfig = field(default_factory=AthenaConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    metrics_file: str = "metrics.yaml"
    allowed_tables: list[str] = field(default_factory=list)
    max_query_rows: int = 500


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
    if v := os.environ.get("BEDROCK_QUERY_MODEL"):
        cfg.bedrock.query_model = v
    if v := os.environ.get("BEDROCK_ENRICHMENT_MODEL"):
        cfg.bedrock.enrichment_model = v

    return cfg

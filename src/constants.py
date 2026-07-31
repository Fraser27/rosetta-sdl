"""Shared magic-value constants for the semantic data layer.

Kept tiny and dependency-free so it can be imported anywhere without cycles.
"""

from __future__ import annotations

# Conventional datasource_id for the default Athena executor that owns
# Glue-scanned tables. Repeated across seeding, registry, routes, and UI.
DEFAULT_DATASOURCE_ID = "ds_default_athena"

# Default AWS region fallback when none is configured or present in the
# AWS_DEFAULT_REGION / AWS_REGION environment.
DEFAULT_AWS_REGION = "us-east-1"

# Default Athena workgroup name.
DEFAULT_ATHENA_WORKGROUP = "primary"

# Bedrock Anthropic API version sent in invoke_model request bodies.
BEDROCK_ANTHROPIC_VERSION = "bedrock-2023-05-31"

# Bedrock models offered in the Configurations UI for ungoverned SQL generation.
# `id` is the Bedrock modelId passed to invoke_model; `label` is for display.
AVAILABLE_QUERY_MODELS = [
    {"id": "global.anthropic.claude-opus-5", "label": "Claude Opus 5 (most capable)"},
    {"id": "global.anthropic.claude-sonnet-5", "label": "Claude Sonnet 5 (balanced)"},
    {"id": "global.anthropic.claude-opus-4-1-20250805-v1:0", "label": "Claude Opus 4.1"},
    {"id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "label": "Claude Sonnet 4.5 (default)"},
    {"id": "global.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 (fast, cheaper)"},
    {"id": "anthropic.claude-3-5-sonnet-20241022-v2:0", "label": "Claude 3.5 Sonnet v2"},
    {"id": "anthropic.claude-3-haiku-20240307-v1:0", "label": "Claude 3 Haiku"},
]

# Singleton graph node key for persisted runtime config overrides.
SYSTEM_CONFIG_KEY = "system_config"

# Default embedding model used to embed the query before an S3 Vectors kNN search.
# NOTE: for search to be meaningful, this must match the model the vectors were
# INGESTED with — hence it's user-configurable in the Configurations UI.
DEFAULT_S3VECTORS_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

# Bedrock embedding models offered in the Configurations UI for S3 Vectors search.
AVAILABLE_EMBEDDING_MODELS = [
    {"id": "amazon.titan-embed-text-v2:0", "label": "Titan Text Embeddings v2 (default, 1024-dim)"},
    {"id": "amazon.titan-embed-text-v1", "label": "Titan Text Embeddings v1 (1536-dim)"},
    {"id": "cohere.embed-english-v3", "label": "Cohere Embed English v3 (1024-dim)"},
    {"id": "cohere.embed-multilingual-v3", "label": "Cohere Embed Multilingual v3 (1024-dim)"},
]

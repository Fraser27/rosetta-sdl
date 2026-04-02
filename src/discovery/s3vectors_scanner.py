"""S3 Vectors scanner — discovers vector buckets, indexes, and metadata schema."""

from __future__ import annotations

import logging

import boto3

from src.catalog.models import ColumnMeta, DocumentMeta
from src.config import VectorBucketConfig

logger = logging.getLogger(__name__)

# Number of vectors to sample when inferring metadata keys
_SAMPLE_SIZE = 10


def discover_all_vector_buckets() -> list[DocumentMeta]:
    """Auto-discover ALL S3 Vector buckets and their indexes + metadata schema."""
    client = boto3.client("s3vectors")
    all_docs: list[DocumentMeta] = []

    logger.info("Auto-discovering all S3 Vector buckets...")
    bucket_names: list[str] = []

    paginator = client.get_paginator("list_vector_buckets")
    for page in paginator.paginate():
        for bucket in page.get("vectorBuckets", []):
            bucket_names.append(bucket["vectorBucketName"])

    logger.info("Found %d vector buckets: %s", len(bucket_names), bucket_names)

    for bucket_name in bucket_names:
        docs = _scan_bucket(client, bucket_name)
        all_docs.extend(docs)

    logger.info("Discovered %d vector indexes across %d buckets", len(all_docs), len(bucket_names))
    return all_docs


def scan_vector_buckets(buckets: list[VectorBucketConfig]) -> list[DocumentMeta]:
    """Scan configured S3 Vector buckets and return document metadata with schema."""
    client = boto3.client("s3vectors")
    all_docs: list[DocumentMeta] = []

    for bucket_config in buckets:
        docs = _scan_bucket(client, bucket_config.bucket)
        all_docs.extend(docs)

    logger.info("Discovered %d vector indexes across %d buckets", len(all_docs), len(buckets))
    return all_docs


def _scan_bucket(client, bucket_name: str) -> list[DocumentMeta]:
    """Scan a single vector bucket for indexes and their metadata schema."""
    docs: list[DocumentMeta] = []
    logger.info("Scanning S3 Vector bucket: %s", bucket_name)

    try:
        idx_paginator = client.get_paginator("list_indexes")
        for page in idx_paginator.paginate(vectorBucketName=bucket_name):
            for index_summary in page.get("indexes", []):
                index_name = index_summary["indexName"]
                doc = _scan_index(client, bucket_name, index_name)
                docs.append(doc)
    except Exception as e:
        logger.error("Error scanning vector bucket '%s': %s", bucket_name, e)

    return docs


def _scan_index(client, bucket_name: str, index_name: str) -> DocumentMeta:
    """Get index details and sample vectors to discover metadata keys."""
    # Get index schema (dimension, metric, non-filterable keys)
    non_filterable_keys: set[str] = set()
    try:
        idx_detail = client.get_index(
            vectorBucketName=bucket_name,
            indexName=index_name,
        )
        index_info = idx_detail.get("index", {})
        dimension = index_info.get("dimension", 0)
        distance_metric = index_info.get("distanceMetric", "")
        meta_config = index_info.get("metadataConfiguration", {})
        non_filterable_keys = set(meta_config.get("nonFilterableMetadataKeys", []))
    except Exception as e:
        logger.warning("Could not get_index for %s/%s: %s", bucket_name, index_name, e)
        dimension = 0
        distance_metric = ""

    # Sample vectors to discover all metadata keys and their types
    metadata_keys = _sample_metadata_keys(client, bucket_name, index_name, non_filterable_keys)

    description = f"Vector index '{index_name}' in bucket '{bucket_name}'"
    if dimension:
        description += f" (dim={dimension}, metric={distance_metric})"
    if metadata_keys:
        key_names = [k.name for k in metadata_keys]
        description += f" | metadata: {', '.join(key_names)}"

    return DocumentMeta(
        name=index_name,
        s3_key=f"s3vectors://{bucket_name}/{index_name}",
        vector_bucket=bucket_name,
        vector_index=index_name,
        description=description,
        type="vector_index",
        metadata_keys=metadata_keys,
    )


def _sample_metadata_keys(
    client,
    bucket_name: str,
    index_name: str,
    non_filterable_keys: set[str],
) -> list[ColumnMeta]:
    """Sample a few vectors to discover metadata key names and types."""
    key_types: dict[str, str] = {}

    try:
        response = client.list_vectors(
            vectorBucketName=bucket_name,
            indexName=index_name,
            maxResults=_SAMPLE_SIZE,
            returnMetadata=True,
            returnData=False,  # skip embeddings
        )
        for vector in response.get("vectors", []):
            metadata = vector.get("metadata")
            if not metadata or not isinstance(metadata, dict):
                continue
            for k, v in metadata.items():
                if k not in key_types:
                    key_types[k] = _infer_type(v)
    except Exception as e:
        logger.warning("Could not sample vectors from %s/%s: %s", bucket_name, index_name, e)

    # Build ColumnMeta list, marking filterable vs non-filterable
    columns: list[ColumnMeta] = []
    for key_name, data_type in sorted(key_types.items()):
        columns.append(ColumnMeta(
            name=key_name,
            data_type=data_type,
            description="non-filterable" if key_name in non_filterable_keys else "filterable",
            is_partition=False,
            is_primary_key=False,
        ))

    logger.info(
        "Index %s/%s: discovered %d metadata keys (%d filterable, %d non-filterable)",
        bucket_name, index_name, len(columns),
        sum(1 for c in columns if c.description == "filterable"),
        sum(1 for c in columns if c.description == "non-filterable"),
    )
    return columns


def _infer_type(value: object) -> str:
    """Infer a simple type string from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "string"

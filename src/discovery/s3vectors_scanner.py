"""S3 Vectors scanner — discovers vector buckets/indexes for document metadata."""

from __future__ import annotations

import logging

import boto3

from src.catalog.models import DocumentMeta
from src.config import VectorBucketConfig

logger = logging.getLogger(__name__)


def scan_vector_buckets(buckets: list[VectorBucketConfig]) -> list[DocumentMeta]:
    """Scan S3 Vector buckets and return document metadata."""
    client = boto3.client("s3vectors")
    all_docs: list[DocumentMeta] = []

    for bucket_config in buckets:
        bucket_name = bucket_config.bucket
        logger.info("Scanning S3 Vector bucket: %s", bucket_name)

        try:
            # List all vector indexes in this bucket
            response = client.list_indexes(vectorBucketName=bucket_name)
            for index in response.get("indexes", []):
                index_name = index.get("indexName", "")
                doc = DocumentMeta(
                    name=index_name,
                    s3_key=f"s3://{bucket_name}/{index_name}",
                    vector_bucket=bucket_name,
                    vector_index=index_name,
                    description=f"Vector index '{index_name}' in bucket '{bucket_name}'",
                    type="document",
                )
                all_docs.append(doc)
        except Exception as e:
            logger.error("Error scanning vector bucket '%s': %s", bucket_name, e)

    logger.info("Discovered %d vector indexes across %d buckets", len(all_docs), len(buckets))
    return all_docs

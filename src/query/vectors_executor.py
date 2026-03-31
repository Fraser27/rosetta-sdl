"""S3 Vectors semantic search executor."""

from __future__ import annotations

import json
import logging

import boto3

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


def search_vectors(
    question: str,
    graph: GraphClient,
    model_id: str = "amazon.titan-embed-text-v2:0",
    max_results: int = 5,
) -> list[dict]:
    """Search S3 Vectors for documents matching the question.

    1. Get document metadata from graph (vector bucket + index)
    2. Generate embedding for the question
    3. Query S3 Vectors
    """
    # Get all document vector configs from graph
    docs = graph.query(
        "MATCH (d:Document) WHERE d.vector_bucket IS NOT NULL "
        "RETURN d.vector_bucket AS bucket, d.vector_index AS index_name, "
        "d.name AS name, d.s3_key AS s3_key"
    )

    if not docs:
        logger.info("No vector indexes found in graph")
        return []

    # Generate embedding for the question
    bedrock = boto3.client("bedrock-runtime")
    embed_response = bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": question}),
    )
    embed_result = json.loads(embed_response["body"].read())
    query_vector = embed_result.get("embedding", [])

    if not query_vector:
        logger.error("Failed to generate embedding")
        return []

    # Search each vector index
    s3vectors = boto3.client("s3vectors")
    all_results: list[dict] = []

    for doc in docs:
        try:
            response = s3vectors.query_vectors(
                vectorBucketName=doc["bucket"],
                indexName=doc["index_name"],
                queryVector=query_vector,
                topK=max_results,
            )
            for hit in response.get("vectors", []):
                all_results.append({
                    "source": doc["name"],
                    "bucket": doc["bucket"],
                    "index": doc["index_name"],
                    "key": hit.get("key", ""),
                    "score": hit.get("distance", 0.0),
                    "metadata": hit.get("metadata", {}),
                    "data": hit.get("data", {}),
                })
        except Exception as e:
            logger.error("Vector search failed for %s/%s: %s", doc["bucket"], doc["index_name"], e)

    # Sort by score (lower distance = better match for cosine)
    all_results.sort(key=lambda r: r.get("score", float("inf")))
    return all_results[:max_results]

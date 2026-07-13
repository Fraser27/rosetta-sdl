"""Reusable embedding utilities for Neo4j vector search."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src import aws_clients

logger = logging.getLogger(__name__)

# Lazy-initialized Bedrock client (reused across calls). The lock guards the
# first-time creation because get_embeddings_batch calls this from a
# ThreadPoolExecutor, so multiple workers could otherwise race on the initial
# aws_clients.client() call (boto3 client creation is not thread-safe).
_bedrock_client: Any = None
_bedrock_lock = threading.Lock()


def _get_bedrock_client() -> Any:
    global _bedrock_client
    if _bedrock_client is None:
        with _bedrock_lock:
            if _bedrock_client is None:
                _bedrock_client = aws_clients.client("bedrock-runtime")
    return _bedrock_client


def get_embedding(
    text: str,
    model_id: str = "amazon.titan-embed-text-v2:0",
    dimensions: int = 1024,
) -> list[float]:
    """Generate an embedding vector for a single text using Bedrock Titan.

    Returns an empty list on error (caller should check).
    """
    try:
        client = _get_bedrock_client()
        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text, "dimensions": dimensions}),
        )
        result = json.loads(response["body"].read())
        return result.get("embedding", [])
    except Exception as e:
        logger.warning("Failed to generate embedding: %s", e)
        return []


def get_embeddings_batch(
    texts: list[str],
    model_id: str = "amazon.titan-embed-text-v2:0",
    dimensions: int = 1024,
    max_workers: int = 5,
) -> list[list[float]]:
    """Generate embeddings for multiple texts in parallel.

    Returns a list of embedding vectors in the same order as the input texts.
    Failed embeddings are returned as empty lists.
    """
    if not texts:
        return []

    results: list[list[float]] = [[] for _ in texts]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(get_embedding, text, model_id, dimensions): i
            for i, text in enumerate(texts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                logger.warning("Batch embedding failed for index %d: %s", idx, e)

    return results


def build_metric_embedding_text(
    name: str, definition: str, synonyms: list[str]
) -> str:
    """Build a consistent text string for metric embedding.

    Combines name, definition, and synonyms into a single string
    suitable for embedding generation.
    """
    parts = [name]
    if definition:
        parts.append(definition)
    if synonyms:
        parts.append(f"Also known as: {', '.join(synonyms)}")
    return ". ".join(parts)

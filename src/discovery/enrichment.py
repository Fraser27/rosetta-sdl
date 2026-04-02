"""LLM-based metadata enrichment — generates descriptions and extracts concepts."""

from __future__ import annotations

import json
import logging

import boto3

from src.graph import queries
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


def enrich_tables(graph: GraphClient, model_id: str, force: bool = False) -> dict:
    """Enrich tables using LLM. By default only enriches tables without descriptions."""
    bedrock = boto3.client("bedrock-runtime")
    where = "" if force else "WHERE t.description IS NULL OR t.description = '' "
    tables = graph.query(
        f"MATCH (t:Table) {where}"
        "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column) "
        "RETURN t.full_name AS full_name, t.name AS name, "
        "collect({name: c.name, type: c.data_type}) AS columns"
    )

    enriched = 0
    for table in tables:
        schema_text = ", ".join(
            f"{c['name']} ({c['type']})" for c in table["columns"] if c["name"]
        )
        prompt = (
            f"Given this database table '{table['name']}' with columns: {schema_text}\n\n"
            "Generate:\n"
            "1. A 1-sentence business description of this table\n"
            "2. A 1-sentence description for each column\n"
            "3. A list of business terms this table relates to\n\n"
            "Respond in JSON: {\"table_description\": \"...\", "
            "\"columns\": {\"col_name\": \"description\", ...}, "
            "\"business_terms\": [\"term1\", \"term2\"]}"
        )

        try:
            response = bedrock.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            result = json.loads(response["body"].read())
            text = result["content"][0]["text"]

            # Parse JSON from response (handle markdown code blocks)
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            data = json.loads(text.strip())

            # Update table description
            graph.write(
                "MATCH (t:Table {full_name: $fn}) SET t.description = $desc",
                {"fn": table["full_name"], "desc": data.get("table_description", "")},
            )

            # Update column descriptions
            for col_name, col_desc in data.get("columns", {}).items():
                graph.write(
                    "MATCH (c:Column {name: $name, table: $table}) SET c.description = $desc",
                    {"name": col_name, "table": table["full_name"], "desc": col_desc},
                )

            # Create business term nodes
            for term in data.get("business_terms", []):
                graph.write(queries.MERGE_BUSINESS_TERM, {
                    "name": term.lower(),
                    "definition": "",
                    "synonyms": [],
                })

            enriched += 1
        except Exception as e:
            logger.error("Failed to enrich table '%s': %s", table["full_name"], e)

    return {"enriched_tables": enriched, "total_tables": len(tables)}


def enrich_documents(graph: GraphClient, model_id: str, force: bool = False) -> dict:
    """Enrich documents by extracting concepts and linking to tables."""
    bedrock = boto3.client("bedrock-runtime")
    where = "" if force else "WHERE d.description IS NULL OR d.description = '' "
    docs = graph.query(
        f"MATCH (d:Document) {where}"
        "RETURN d.s3_key AS s3_key, d.name AS name"
    )

    # Get all table names for context
    tables = graph.query("MATCH (t:Table) RETURN collect(t.full_name) AS names")
    table_names = tables[0]["names"] if tables else []

    enriched = 0
    for doc in docs:
        prompt = (
            f"Given a document named '{doc['name']}' in a data lake that also contains "
            f"these tables: {', '.join(table_names)}\n\n"
            "Generate:\n"
            "1. A 1-sentence description of what this document likely contains\n"
            "2. Which tables it might relate to (from the list above)\n"
            "3. Key business concepts this document covers\n\n"
            "Respond in JSON: {\"description\": \"...\", "
            "\"related_tables\": [\"table1\", ...], "
            "\"concepts\": [\"concept1\", \"concept2\"]}"
        )

        try:
            response = bedrock.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            result = json.loads(response["body"].read())
            text = result["content"][0]["text"]

            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            data = json.loads(text.strip())

            # Update document description
            graph.write(
                "MATCH (d:Document {s3_key: $key}) SET d.description = $desc",
                {"key": doc["s3_key"], "desc": data.get("description", "")},
            )

            # Link to related tables
            for table_name in data.get("related_tables", []):
                if table_name in table_names:
                    graph.write(queries.LINK_DOCUMENT_TO_TABLE, {
                        "s3_key": doc["s3_key"],
                        "table_full_name": table_name,
                    })

            # Create concept nodes and link
            for concept in data.get("concepts", []):
                graph.write(queries.MERGE_CONCEPT, {
                    "name": concept.lower(),
                    "definition": "",
                })
                graph.write(queries.LINK_DOCUMENT_TO_CONCEPT, {
                    "s3_key": doc["s3_key"],
                    "concept_name": concept.lower(),
                })

            enriched += 1
        except Exception as e:
            logger.error("Failed to enrich document '%s': %s", doc["name"], e)

    return {"enriched_documents": enriched, "total_documents": len(docs)}

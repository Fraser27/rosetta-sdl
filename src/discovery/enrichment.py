"""LLM-based metadata enrichment — generates descriptions and extracts concepts."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

import boto3

from src.graph import queries
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)


# ── Job tracker ──────────────────────────────────────────────

@dataclass
class EnrichmentJob:
    job_id: str
    status: str = "pending"  # pending | running | completed | failed
    started_at: float = 0
    finished_at: float = 0
    datasources: list[str] = field(default_factory=list)
    force: bool = False
    tables_total: int = 0
    tables_enriched: int = 0
    tables_skipped: int = 0
    tables_failed: int = 0
    documents_total: int = 0
    documents_enriched: int = 0
    current_table: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id,
            "status": self.status,
            "datasources": self.datasources,
            "force": self.force,
            "tables": {
                "total": self.tables_total,
                "enriched": self.tables_enriched,
                "skipped": self.tables_skipped,
                "failed": self.tables_failed,
            },
            "documents": {
                "total": self.documents_total,
                "enriched": self.documents_enriched,
            },
            "current_table": self.current_table,
        }
        if self.started_at:
            d["elapsed_seconds"] = round((self.finished_at or time.time()) - self.started_at, 1)
        if self.error:
            d["error"] = self.error
        return d


# In-memory job store (keeps last 10 jobs)
_jobs: dict[str, EnrichmentJob] = {}
_jobs_lock = threading.Lock()


def _store_job(job: EnrichmentJob) -> None:
    with _jobs_lock:
        _jobs[job.job_id] = job
        # Prune old jobs
        if len(_jobs) > 10:
            oldest = sorted(_jobs.values(), key=lambda j: j.started_at)
            for j in oldest[:len(_jobs) - 10]:
                del _jobs[j.job_id]


def get_job(job_id: str) -> EnrichmentJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    with _jobs_lock:
        return [j.to_dict() for j in sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)]


# ── Enrichment logic ─────────────────────────────────────────

def _parse_llm_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


def _call_bedrock(bedrock, model_id: str, prompt: str, max_tokens: int = 1024) -> dict:
    """Call Bedrock and return parsed JSON."""
    response = bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    result = json.loads(response["body"].read())
    text = result["content"][0]["text"]
    return _parse_llm_json(text)


def _enrich_single_table(
    graph: GraphClient, bedrock, model_id: str,
    table: dict, force: bool,
) -> str:
    """Enrich a single table + its columns. Returns 'enriched', 'skipped', or 'failed'."""
    full_name = table["full_name"]
    table_desc = table.get("description") or ""
    columns = [c for c in table["columns"] if c.get("name")]

    # Identify which columns need enrichment
    if force:
        cols_to_enrich = columns
    else:
        cols_to_enrich = [c for c in columns if not c.get("description")]

    table_needs_desc = force or not table_desc.strip()

    # Skip if nothing to do
    if not table_needs_desc and not cols_to_enrich:
        return "skipped"

    # Build column list for the prompt (send all columns for context,
    # but only ask for descriptions of those that need it)
    all_cols_text = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
    cols_needing_desc = [c["name"] for c in cols_to_enrich]

    prompt_parts = [
        f"Given this database table '{table['name']}' with columns: {all_cols_text}\n",
    ]

    if table_needs_desc:
        prompt_parts.append("1. A 1-sentence business description of this table")

    if cols_needing_desc:
        prompt_parts.append(
            f"{'2' if table_needs_desc else '1'}. A 1-sentence description for ONLY these columns: "
            f"{', '.join(cols_needing_desc)}"
        )

    prompt_parts.append(
        f"{'3' if table_needs_desc and cols_needing_desc else '2' if table_needs_desc or cols_needing_desc else '1'}. "
        "A list of business terms this table relates to"
    )

    prompt_parts.append(
        '\nRespond in JSON: {"table_description": "...", '
        '"columns": {"col_name": "description", ...}, '
        '"business_terms": ["term1", "term2"]}'
    )

    prompt = "\nGenerate:\n".join([prompt_parts[0], "\n".join(prompt_parts[1:])])

    try:
        data = _call_bedrock(bedrock, model_id, prompt)

        # Update table description
        if table_needs_desc and data.get("table_description"):
            graph.write(
                "MATCH (t:Table {full_name: $fn}) SET t.description = $desc",
                {"fn": full_name, "desc": data["table_description"]},
            )

        # Update column descriptions (only those we asked for)
        for col_name, col_desc in data.get("columns", {}).items():
            if col_name in cols_needing_desc:
                graph.write(
                    "MATCH (c:Column {name: $name, table: $table}) SET c.description = $desc",
                    {"name": col_name, "table": full_name, "desc": col_desc},
                )

        # Create business term nodes
        for term in data.get("business_terms", []):
            graph.write(queries.MERGE_BUSINESS_TERM, {
                "name": term.lower(),
                "definition": "",
                "synonyms": [],
            })

        return "enriched"
    except Exception as e:
        logger.error("Failed to enrich table '%s': %s", full_name, e)
        return "failed"


def _run_enrichment(job: EnrichmentJob, graph: GraphClient, model_id: str) -> None:
    """Background enrichment runner."""
    try:
        job.status = "running"
        job.started_at = time.time()
        bedrock = boto3.client("bedrock-runtime")

        # ── Tables ──
        # Build datasource filter
        ds_filter = ""
        if job.datasources:
            ds_list = ", ".join(f"'{ds}'" for ds in job.datasources)
            ds_filter = f"MATCH (ds:DataSource)-[:CONTAINS]->(t) WHERE ds.name IN [{ds_list}] "
        else:
            ds_filter = "MATCH (t:Table) "

        # Fetch tables with columns and their descriptions
        table_query = (
            f"{ds_filter}"
            "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column) "
            "RETURN t.full_name AS full_name, t.name AS name, "
            "t.description AS description, "
            "collect({name: c.name, type: c.data_type, description: c.description}) AS columns"
        )
        tables = graph.query(table_query)
        job.tables_total = len(tables)

        for table in tables:
            job.current_table = table["full_name"]
            result = _enrich_single_table(graph, bedrock, model_id, table, job.force)
            if result == "enriched":
                job.tables_enriched += 1
            elif result == "skipped":
                job.tables_skipped += 1
            else:
                job.tables_failed += 1

        # ── Documents ──
        job.current_table = ""
        where = "" if job.force else "WHERE d.description IS NULL OR d.description = '' "
        docs = graph.query(
            f"MATCH (d:Document) {where}"
            "RETURN d.s3_key AS s3_key, d.name AS name"
        )
        job.documents_total = len(docs)

        table_names_result = graph.query("MATCH (t:Table) RETURN collect(t.full_name) AS names")
        table_names = table_names_result[0]["names"] if table_names_result else []

        for doc in docs:
            try:
                prompt = (
                    f"Given a document named '{doc['name']}' in a data lake that also contains "
                    f"these tables: {', '.join(table_names)}\n\n"
                    "Generate:\n"
                    "1. A 1-sentence description of what this document likely contains\n"
                    "2. Which tables it might relate to (from the list above)\n"
                    "3. Key business concepts this document covers\n\n"
                    'Respond in JSON: {"description": "...", '
                    '"related_tables": ["table1", ...], '
                    '"concepts": ["concept1", "concept2"]}'
                )
                data = _call_bedrock(bedrock, model_id, prompt, max_tokens=512)

                graph.write(
                    "MATCH (d:Document {s3_key: $key}) SET d.description = $desc",
                    {"key": doc["s3_key"], "desc": data.get("description", "")},
                )

                for table_name in data.get("related_tables", []):
                    if table_name in table_names:
                        graph.write(queries.LINK_DOCUMENT_TO_TABLE, {
                            "s3_key": doc["s3_key"],
                            "table_full_name": table_name,
                        })

                for concept in data.get("concepts", []):
                    graph.write(queries.MERGE_CONCEPT, {
                        "name": concept.lower(),
                        "definition": "",
                    })
                    graph.write(queries.LINK_DOCUMENT_TO_CONCEPT, {
                        "s3_key": doc["s3_key"],
                        "concept_name": concept.lower(),
                    })

                job.documents_enriched += 1
            except Exception as e:
                logger.error("Failed to enrich document '%s': %s", doc["name"], e)

        job.status = "completed"
    except Exception as e:
        logger.error("Enrichment job %s failed: %s", job.job_id, e)
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = time.time()
        job.current_table = ""


def start_enrichment(
    graph: GraphClient,
    model_id: str,
    datasources: list[str] | None = None,
    force: bool = False,
) -> EnrichmentJob:
    """Start an async enrichment job. Returns the job immediately."""
    job = EnrichmentJob(
        job_id=str(uuid.uuid4())[:8],
        datasources=datasources or [],
        force=force,
    )
    _store_job(job)

    thread = threading.Thread(
        target=_run_enrichment,
        args=(job, graph, model_id),
        daemon=True,
    )
    thread.start()

    return job


# Keep legacy sync functions for backward compat (MCP tools, etc.)
def enrich_tables(graph: GraphClient, model_id: str, force: bool = False) -> dict:
    """Sync enrichment — enriches all tables."""
    bedrock = boto3.client("bedrock-runtime")
    table_query = (
        "MATCH (t:Table) "
        "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column) "
        "RETURN t.full_name AS full_name, t.name AS name, "
        "t.description AS description, "
        "collect({name: c.name, type: c.data_type, description: c.description}) AS columns"
    )
    tables = graph.query(table_query)
    enriched = 0
    for table in tables:
        result = _enrich_single_table(graph, bedrock, model_id, table, force)
        if result == "enriched":
            enriched += 1
    return {"enriched_tables": enriched, "total_tables": len(tables)}


def enrich_documents(graph: GraphClient, model_id: str, force: bool = False) -> dict:
    """Sync enrichment — enriches all documents."""
    bedrock = boto3.client("bedrock-runtime")
    where = "" if force else "WHERE d.description IS NULL OR d.description = '' "
    docs = graph.query(
        f"MATCH (d:Document) {where}"
        "RETURN d.s3_key AS s3_key, d.name AS name"
    )

    table_names_result = graph.query("MATCH (t:Table) RETURN collect(t.full_name) AS names")
    table_names = table_names_result[0]["names"] if table_names_result else []

    enriched = 0
    for doc in docs:
        try:
            prompt = (
                f"Given a document named '{doc['name']}' in a data lake that also contains "
                f"these tables: {', '.join(table_names)}\n\n"
                "Generate:\n"
                "1. A 1-sentence description of what this document likely contains\n"
                "2. Which tables it might relate to (from the list above)\n"
                "3. Key business concepts this document covers\n\n"
                'Respond in JSON: {"description": "...", '
                '"related_tables": ["table1", ...], '
                '"concepts": ["concept1", "concept2"]}'
            )
            data = _call_bedrock(bedrock, model_id, prompt, max_tokens=512)

            graph.write(
                "MATCH (d:Document {s3_key: $key}) SET d.description = $desc",
                {"key": doc["s3_key"], "desc": data.get("description", "")},
            )

            for table_name in data.get("related_tables", []):
                if table_name in table_names:
                    graph.write(queries.LINK_DOCUMENT_TO_TABLE, {
                        "s3_key": doc["s3_key"],
                        "table_full_name": table_name,
                    })

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

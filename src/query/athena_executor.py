"""Athena query executor — submits SQL and returns results."""

from __future__ import annotations

import logging
import time

import boto3

logger = logging.getLogger(__name__)


def execute_query(
    sql: str,
    workgroup: str,
    output_location: str,
    database: str | None = None,
    catalog: str | None = None,
    max_rows: int = 500,
    timeout_seconds: int = 30,
) -> dict:
    """Execute a SQL query on Athena and return results.

    Returns:
        {
            "columns": ["col1", "col2"],
            "rows": [[val1, val2], ...],
            "row_count": int,
            "duration_ms": float,
            "query_execution_id": str,
        }
    """
    athena = boto3.client("athena")
    start = time.time()

    # Build execution context
    context: dict = {}
    if database:
        context["Database"] = database
    if catalog:
        context["Catalog"] = catalog

    params: dict = {
        "QueryString": sql,
        "WorkGroup": workgroup,
        "ResultConfiguration": {"OutputLocation": output_location},
    }
    if context:
        params["QueryExecutionContext"] = context

    response = athena.start_query_execution(**params)
    query_id = response["QueryExecutionId"]

    # Poll for completion
    elapsed = 0.0
    wait = 0.5
    while elapsed < timeout_seconds:
        status = athena.get_query_execution(QueryExecutionId=query_id)
        state = status["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "duration_ms": (time.time() - start) * 1000,
                "query_execution_id": query_id,
                "error": f"Query {state}: {reason}",
            }

        time.sleep(wait)
        elapsed += wait
        wait = min(wait * 1.5, 3.0)

    if elapsed >= timeout_seconds:
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "duration_ms": (time.time() - start) * 1000,
            "query_execution_id": query_id,
            "error": f"Query timed out after {timeout_seconds}s",
        }

    # Fetch results
    columns: list[str] = []
    rows: list[list] = []

    paginator = athena.get_paginator("get_query_results")
    page_count = 0
    for page in paginator.paginate(QueryExecutionId=query_id):
        result_set = page["ResultSet"]

        # Extract column names from first page
        if page_count == 0:
            columns = [
                col["Label"] if col.get("Label") else col["Name"]
                for col in result_set["ResultSetMetadata"]["ColumnInfo"]
            ]

        for i, row in enumerate(result_set["Rows"]):
            # Skip header row on first page
            if page_count == 0 and i == 0:
                continue
            values = [field.get("VarCharValue", "") for field in row["Data"]]
            rows.append(values)

            if len(rows) >= max_rows:
                break

        page_count += 1
        if len(rows) >= max_rows:
            break

    duration_ms = (time.time() - start) * 1000
    logger.info("Athena query %s completed in %.0fms, %d rows", query_id, duration_ms, len(rows))

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "duration_ms": duration_ms,
        "query_execution_id": query_id,
    }

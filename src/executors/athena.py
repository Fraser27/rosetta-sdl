"""Athena executor — implements BaseExecutor for AWS Athena."""

from __future__ import annotations

import logging
import time

import boto3

from src.executors.base import (
    BaseExecutor,
    ConnectionTestResult,
    ConnectionTestStep,
    ExecutionResult,
    HealthStatus,
)

logger = logging.getLogger(__name__)


class AthenaExecutor(BaseExecutor):
    """Executor for AWS Athena queries."""

    datasource_type = "athena"

    def __init__(self, datasource_id: str, datasource_name: str, config: dict):
        super().__init__(datasource_id, datasource_name, config)
        self._workgroup = config.get("endpoint", "primary")
        self._output_location = config.get("output_location", "")
        self._database = config.get("database")
        self._region = config.get("region", "us-east-1")
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("athena", region_name=self._region)
        return self._client

    async def execute(self, sql: str, max_rows: int = 500) -> ExecutionResult:
        """Execute SQL on Athena with polling."""
        client = self._get_client()
        start = time.time()

        context: dict = {}
        if self._database:
            context["Database"] = self._database

        params: dict = {
            "QueryString": sql,
            "WorkGroup": self._workgroup,
            "ResultConfiguration": {"OutputLocation": self._output_location},
        }
        if context:
            params["QueryExecutionContext"] = context

        try:
            response = client.start_query_execution(**params)
        except Exception as e:
            return ExecutionResult(
                success=False, error=str(e), error_code="connection_failed",
                datasource_id=self.datasource_id, duration_ms=(time.time() - start) * 1000,
            )

        query_id = response["QueryExecutionId"]

        # Poll for completion
        elapsed = 0.0
        wait = 0.5
        timeout = 30
        while elapsed < timeout:
            status = client.get_query_execution(QueryExecutionId=query_id)
            state = status["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                break
            elif state in ("FAILED", "CANCELLED"):
                reason = status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
                return ExecutionResult(
                    success=False, error=f"Query {state}: {reason}", error_code="query_error",
                    query_execution_id=query_id, datasource_id=self.datasource_id,
                    duration_ms=(time.time() - start) * 1000,
                )

            time.sleep(wait)
            elapsed += wait
            wait = min(wait * 1.5, 3.0)

        if elapsed >= timeout:
            return ExecutionResult(
                success=False, error=f"Query timed out after {timeout}s", error_code="timeout",
                query_execution_id=query_id, datasource_id=self.datasource_id,
                duration_ms=(time.time() - start) * 1000,
            )

        # Fetch results
        columns: list[str] = []
        rows: list[list] = []

        paginator = client.get_paginator("get_query_results")
        page_count = 0
        for page in paginator.paginate(QueryExecutionId=query_id):
            result_set = page["ResultSet"]

            if page_count == 0:
                columns = [
                    col["Label"] if col.get("Label") else col["Name"]
                    for col in result_set["ResultSetMetadata"]["ColumnInfo"]
                ]

            for i, row in enumerate(result_set["Rows"]):
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

        return ExecutionResult(
            success=True, columns=columns, rows=rows, row_count=len(rows),
            duration_ms=duration_ms, query_execution_id=query_id,
            datasource_id=self.datasource_id,
        )

    async def test_connection(self) -> ConnectionTestResult:
        """Test Athena connection step by step."""
        steps = [
            ConnectionTestStep(name="resolve_workgroup"),
            ConnectionTestStep(name="execute_probe"),
            ConnectionTestStep(name="verify_permissions"),
        ]

        try:
            # Step 1: Resolve workgroup
            steps[0].status = "running"
            start = time.time()
            client = self._get_client()
            client.get_work_group(WorkGroup=self._workgroup)
            steps[0].duration_ms = (time.time() - start) * 1000
            steps[0].status = "success"
        except Exception as e:
            steps[0].status = "failed"
            steps[0].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        try:
            # Step 2: Execute probe
            steps[1].status = "running"
            start = time.time()
            result = await self.execute("SELECT 1", max_rows=1)
            steps[1].duration_ms = (time.time() - start) * 1000
            if result.success:
                steps[1].status = "success"
            else:
                steps[1].status = "failed"
                steps[1].error = result.error
                return ConnectionTestResult(success=False, steps=steps, error=result.error)
        except Exception as e:
            steps[1].status = "failed"
            steps[1].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        try:
            # Step 3: Verify permissions
            steps[2].status = "running"
            start = time.time()
            result = await self.execute("SHOW TABLES", max_rows=1)
            steps[2].duration_ms = (time.time() - start) * 1000
            if result.success:
                steps[2].status = "success"
            else:
                steps[2].status = "failed"
                steps[2].error = result.error
                return ConnectionTestResult(success=False, steps=steps, error=result.error)
        except Exception as e:
            steps[2].status = "failed"
            steps[2].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        return ConnectionTestResult(success=True, steps=steps)

    async def health_check(self) -> HealthStatus:
        """Quick health check via SELECT 1."""
        try:
            result = await self.execute("SELECT 1", max_rows=1)
            return HealthStatus.HEALTHY if result.success else HealthStatus.UNHEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY

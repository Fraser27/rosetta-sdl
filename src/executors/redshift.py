"""Redshift Serverless executor — implements BaseExecutor using boto3 redshift-data API."""

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


class RedshiftServerlessExecutor(BaseExecutor):
    """Executor for Amazon Redshift Serverless via the redshift-data API."""

    datasource_type = "redshift_serverless"

    def __init__(self, datasource_id: str, datasource_name: str, config: dict):
        super().__init__(datasource_id, datasource_name, config)
        self._workgroup = config.get("endpoint", "")
        self._database = config.get("database", "")
        self._region = config.get("region", "us-east-1")
        self._secret_arn = config.get("secret_arn")
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("redshift-data", region_name=self._region)
        return self._client

    def _build_execute_params(self, sql: str) -> dict:
        """Build params for execute_statement."""
        params: dict = {
            "Sql": sql,
            "Database": self._database,
            "WorkgroupName": self._workgroup,
        }
        if self._secret_arn:
            params["SecretArn"] = self._secret_arn
        return params

    def _poll_statement(self, statement_id: str, timeout: int = 30) -> dict:
        """Poll until statement completes or times out."""
        client = self._get_client()
        elapsed = 0.0
        wait = 0.5
        while elapsed < timeout:
            desc = client.describe_statement(Id=statement_id)
            status = desc["Status"]

            if status == "FINISHED":
                return {"status": "FINISHED", "description": desc}
            elif status in ("FAILED", "ABORTED"):
                error = desc.get("Error", "Unknown error")
                return {"status": status, "error": error}

            time.sleep(wait)
            elapsed += wait
            wait = min(wait * 1.5, 3.0)

        return {"status": "TIMEOUT", "error": f"Statement timed out after {timeout}s"}

    async def execute(self, sql: str, max_rows: int = 500) -> ExecutionResult:
        """Execute SQL on Redshift Serverless."""
        client = self._get_client()
        start = time.time()

        try:
            response = client.execute_statement(**self._build_execute_params(sql))
        except Exception as e:
            return ExecutionResult(
                success=False, error=str(e), error_code="connection_failed",
                datasource_id=self.datasource_id, duration_ms=(time.time() - start) * 1000,
            )

        statement_id = response["Id"]

        # Poll for completion
        poll_result = self._poll_statement(statement_id)

        if poll_result["status"] == "TIMEOUT":
            return ExecutionResult(
                success=False, error=poll_result["error"], error_code="timeout",
                query_execution_id=statement_id, datasource_id=self.datasource_id,
                duration_ms=(time.time() - start) * 1000,
            )
        elif poll_result["status"] in ("FAILED", "ABORTED"):
            return ExecutionResult(
                success=False, error=poll_result["error"], error_code="query_error",
                query_execution_id=statement_id, datasource_id=self.datasource_id,
                duration_ms=(time.time() - start) * 1000,
            )

        # Fetch results
        columns: list[str] = []
        rows: list[list] = []

        try:
            # Check if there are results (DML statements may not have results)
            desc = poll_result["description"]
            if not desc.get("HasResultSet", False):
                duration_ms = (time.time() - start) * 1000
                return ExecutionResult(
                    success=True, columns=[], rows=[], row_count=0,
                    duration_ms=duration_ms, query_execution_id=statement_id,
                    datasource_id=self.datasource_id,
                )

            # Paginate results
            paginator = client.get_paginator("get_statement_result")
            page_count = 0
            for page in paginator.paginate(Id=statement_id):
                # Extract columns from first page
                if page_count == 0 and "ColumnMetadata" in page:
                    columns = [col.get("label") or col.get("name", f"col_{i}")
                               for i, col in enumerate(page["ColumnMetadata"])]

                for record in page.get("Records", []):
                    row = []
                    for field in record:
                        # redshift-data returns typed fields
                        if "stringValue" in field:
                            row.append(field["stringValue"])
                        elif "longValue" in field:
                            row.append(str(field["longValue"]))
                        elif "doubleValue" in field:
                            row.append(str(field["doubleValue"]))
                        elif "booleanValue" in field:
                            row.append(str(field["booleanValue"]))
                        elif "isNull" in field and field["isNull"]:
                            row.append("")
                        else:
                            row.append("")
                    rows.append(row)

                    if len(rows) >= max_rows:
                        break

                page_count += 1
                if len(rows) >= max_rows:
                    break

        except Exception as e:
            return ExecutionResult(
                success=False, error=f"Failed to fetch results: {e}", error_code="query_error",
                query_execution_id=statement_id, datasource_id=self.datasource_id,
                duration_ms=(time.time() - start) * 1000,
            )

        duration_ms = (time.time() - start) * 1000
        logger.info("Redshift query %s completed in %.0fms, %d rows", statement_id, duration_ms, len(rows))

        return ExecutionResult(
            success=True, columns=columns, rows=rows, row_count=len(rows),
            duration_ms=duration_ms, query_execution_id=statement_id,
            datasource_id=self.datasource_id,
        )

    async def test_connection(self) -> ConnectionTestResult:
        """Test Redshift Serverless connection step by step."""
        steps = [
            ConnectionTestStep(name="resolve_endpoint"),
            ConnectionTestStep(name="authenticate"),
            ConnectionTestStep(name="execute_probe"),
            ConnectionTestStep(name="verify_permissions"),
        ]

        # Step 1: Resolve workgroup
        try:
            steps[0].status = "running"
            start = time.time()
            serverless_client = boto3.client("redshift-serverless", region_name=self._region)
            serverless_client.get_workgroup(workgroupName=self._workgroup)
            steps[0].duration_ms = (time.time() - start) * 1000
            steps[0].status = "success"
        except Exception as e:
            steps[0].status = "failed"
            steps[0].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        # Step 2: Authenticate (validate secret or IAM)
        try:
            steps[1].status = "running"
            start = time.time()
            if self._secret_arn:
                secrets_client = boto3.client("secretsmanager", region_name=self._region)
                secrets_client.get_secret_value(SecretId=self._secret_arn)
            # IAM auth is implicit - if we can call redshift-data, we're authenticated
            steps[1].duration_ms = (time.time() - start) * 1000
            steps[1].status = "success"
        except Exception as e:
            steps[1].status = "failed"
            steps[1].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        # Step 3: Execute probe query
        try:
            steps[2].status = "running"
            start = time.time()
            result = await self.execute("SELECT 1", max_rows=1)
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

        # Step 4: Verify permissions
        try:
            steps[3].status = "running"
            start = time.time()
            result = await self.execute(
                "SELECT * FROM information_schema.tables LIMIT 1", max_rows=1
            )
            steps[3].duration_ms = (time.time() - start) * 1000
            if result.success:
                steps[3].status = "success"
            else:
                steps[3].status = "failed"
                steps[3].error = result.error
                return ConnectionTestResult(success=False, steps=steps, error=result.error)
        except Exception as e:
            steps[3].status = "failed"
            steps[3].error = str(e)
            return ConnectionTestResult(success=False, steps=steps, error=str(e))

        return ConnectionTestResult(success=True, steps=steps)

    async def health_check(self) -> HealthStatus:
        """Quick health check via SELECT 1."""
        try:
            result = await self.execute("SELECT 1", max_rows=1)
            return HealthStatus.HEALTHY if result.success else HealthStatus.UNHEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY

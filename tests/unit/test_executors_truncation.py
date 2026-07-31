"""Tests for result-set truncation detection in the Athena and Redshift executors.

The executors fetch one row beyond ``max_rows`` to detect whether the caller's
row cap dropped any rows. When it did, the extra row is trimmed off and the
``truncated`` flag on the ExecutionResult is set to True.

These are the first executor tests in the suite, so they establish the pattern:
the boto3 client is a plain ``MagicMock`` returned from a patched ``_get_client``,
and the paginator is stubbed to yield a single page of fabricated rows.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from src.executors.athena import AthenaExecutor
from src.executors.redshift import RedshiftServerlessExecutor


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Athena
# --------------------------------------------------------------------------- #

def _athena_page(n_data_rows: int) -> dict:
    """Build a get_query_results page with a header row + n data rows."""
    header = {"Data": [{"VarCharValue": "id"}]}
    data_rows = [{"Data": [{"VarCharValue": str(i)}]} for i in range(n_data_rows)]
    return {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": "id", "Label": "id"}]},
            "Rows": [header] + data_rows,
        }
    }


def _make_athena_client(n_data_rows: int) -> MagicMock:
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "qid-1"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    paginator = MagicMock()
    paginator.paginate.return_value = [_athena_page(n_data_rows)]
    client.get_paginator.return_value = paginator
    return client


def _athena_executor(client: MagicMock) -> AthenaExecutor:
    ex = AthenaExecutor("ds-athena", "Athena", {"output_location": "s3://x/"})
    ex._get_client = MagicMock(return_value=client)
    return ex


def test_athena_truncated_when_more_rows_than_cap():
    # 5 rows available, cap of 3 -> truncated, trimmed to 3.
    client = _make_athena_client(n_data_rows=5)
    ex = _athena_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is True
    assert result.row_count == 3
    assert len(result.rows) == 3


def test_athena_not_truncated_when_exactly_at_cap():
    # Exactly 3 rows available, cap of 3 -> not truncated.
    client = _make_athena_client(n_data_rows=3)
    ex = _athena_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is False
    assert result.row_count == 3


def test_athena_not_truncated_when_fewer_rows_than_cap():
    client = _make_athena_client(n_data_rows=1)
    ex = _athena_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is False
    assert result.row_count == 1


# --------------------------------------------------------------------------- #
# Redshift Serverless
# --------------------------------------------------------------------------- #

def _redshift_page(n_rows: int) -> dict:
    return {
        "ColumnMetadata": [{"name": "id", "label": "id"}],
        "Records": [[{"longValue": i}] for i in range(n_rows)],
    }


def _make_redshift_client(n_rows: int) -> MagicMock:
    client = MagicMock()
    client.execute_statement.return_value = {"Id": "stmt-1"}
    client.describe_statement.return_value = {"Status": "FINISHED", "HasResultSet": True}
    paginator = MagicMock()
    paginator.paginate.return_value = [_redshift_page(n_rows)]
    client.get_paginator.return_value = paginator
    return client


def _redshift_executor(client: MagicMock) -> RedshiftServerlessExecutor:
    ex = RedshiftServerlessExecutor(
        "ds-rs", "Redshift", {"endpoint": "wg", "database": "db"}
    )
    ex._get_client = MagicMock(return_value=client)
    return ex


def test_redshift_truncated_when_more_rows_than_cap():
    client = _make_redshift_client(n_rows=5)
    ex = _redshift_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is True
    assert result.row_count == 3
    assert len(result.rows) == 3


def test_redshift_not_truncated_when_exactly_at_cap():
    client = _make_redshift_client(n_rows=3)
    ex = _redshift_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is False
    assert result.row_count == 3


def test_redshift_not_truncated_when_fewer_rows_than_cap():
    client = _make_redshift_client(n_rows=1)
    ex = _redshift_executor(client)
    result = _run(ex.execute("SELECT id FROM t", max_rows=3))

    assert result.success
    assert result.truncated is False
    assert result.row_count == 1

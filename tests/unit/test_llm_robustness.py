"""Tests for robust LLM-output parsing and Bedrock retry helpers."""

import time

import pytest
from botocore.exceptions import ClientError

from src.text_utils import extract_json, extract_sql, retry_bedrock


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "boom"}}, "InvokeModel"
    )


class TestExtractSql:
    def test_fenced_sql(self):
        text = "Here you go:\n```sql\nSELECT 1 FROM t LIMIT 10\n```\nDone."
        assert extract_sql(text) == "SELECT 1 FROM t LIMIT 10"

    def test_generic_fence(self):
        text = "```\nSELECT 2 FROM t\n```"
        assert extract_sql(text) == "SELECT 2 FROM t"

    def test_bare_unfenced(self):
        assert extract_sql("  SELECT 3 FROM t LIMIT 5  ") == "SELECT 3 FROM t LIMIT 5"

    def test_prefers_sql_tag_over_other_block(self):
        text = "```python\nprint(1)\n```\nand\n```sql\nSELECT 9\n```"
        assert extract_sql(text) == "SELECT 9"

    def test_empty(self):
        assert extract_sql("") == ""


class TestExtractJson:
    def test_fenced_json(self):
        text = '```json\n{"a": 1, "b": [2, 3]}\n```'
        assert extract_json(text) == {"a": 1, "b": [2, 3]}

    def test_generic_fence(self):
        text = '```\n{"x": true}\n```'
        assert extract_json(text) == {"x": True}

    def test_bare_json(self):
        assert extract_json('{"k": "v"}') == {"k": "v"}

    def test_prose_wrapped_object(self):
        text = 'Sure! Here is the result: {"table_description": "orders", "cols": {}} Hope that helps.'
        assert extract_json(text) == {"table_description": "orders", "cols": {}}

    def test_prose_wrapped_array(self):
        text = "The terms are: [\"a\", \"b\", \"c\"] end."
        assert extract_json(text) == ["a", "b", "c"]

    def test_braces_inside_strings_are_ignored(self):
        text = 'prose {"note": "has } and { inside"} tail'
        assert extract_json(text) == {"note": "has } and { inside"}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("no json here at all")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            extract_json("")


class TestRetryBedrock:
    def test_returns_on_first_success(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        assert retry_bedrock(fn, max_attempts=4, base_delay=0.01) == "ok"
        assert calls["n"] == 1

    def test_retries_on_throttling_then_succeeds(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _client_error("ThrottlingException")
            return "done"

        assert retry_bedrock(fn, max_attempts=4, base_delay=0.01) == "done"
        assert calls["n"] == 3
        assert len(sleeps) == 2

    def test_gives_up_after_max_attempts(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _client_error("ServiceUnavailableException")

        with pytest.raises(ClientError):
            retry_bedrock(fn, max_attempts=3, base_delay=0.01)
        assert calls["n"] == 3

    def test_non_retryable_error_propagates_immediately(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _client_error("ValidationException")

        with pytest.raises(ClientError):
            retry_bedrock(fn, max_attempts=4, base_delay=0.01)
        assert calls["n"] == 1

    def test_read_timeout_is_retried(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        class ReadTimeoutError(Exception):
            pass

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise ReadTimeoutError("timed out")
            return "recovered"

        assert retry_bedrock(fn, max_attempts=4, base_delay=0.01) == "recovered"
        assert calls["n"] == 2

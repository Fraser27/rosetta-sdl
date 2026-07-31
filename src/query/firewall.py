"""SQL Firewall — validates SQL queries against a table allowlist using sqlglot AST.

Adapted from Fusion-main/agent/src/clients/sql_firewall.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    allowed: bool
    denied_tables: list[str] = field(default_factory=list)
    reason: str = ""


class SQLFirewall:
    """Validates SQL queries against an allowlist of tables.

    Uses sqlglot AST parsing (not regex) to extract ALL table references
    from FROM, JOIN, subqueries, CTEs, and UNION (find_all is recursive).

    The allowlist can be supplied in two ways:

    * ``allowed_tables`` — a fixed/static set (``explicit`` mode).
    * ``allowlist_provider`` — a zero-arg callable returning the current set of
      allowed table full-names. This lets the firewall track a live catalog
      (e.g. the Neo4j graph) so that tables discovered by a scan *after*
      startup become allowed automatically, without rebuilding the firewall.
      Results are cached for ``cache_ttl`` seconds to avoid hammering the
      backing store on every ``validate()`` call.

    ``allow_all`` explicitly disables enforcement (opt-out ``disabled`` mode);
    it is the ONLY way to get "allow everything" behaviour — an empty allowlist
    is treated as "allow nothing" (fail-closed), never as a no-op.

    Fail-closed on parse errors.
    """

    def __init__(
        self,
        allowed_tables: set[str] | None = None,
        *,
        allowlist_provider: Callable[[], set[str]] | None = None,
        allow_all: bool = False,
        cache_ttl: float = 30.0,
    ) -> None:
        self._static_allowed = {t.lower() for t in (allowed_tables or set())}
        self._provider = allowlist_provider
        self.allow_all = allow_all
        self._cache_ttl = cache_ttl
        self._cache: set[str] | None = None
        self._cache_ts: float = 0.0

    @property
    def allowed_tables(self) -> set[str]:
        """Current effective allowlist (static set unioned with provider results)."""
        allowed = set(self._static_allowed)
        if self._provider is not None:
            now = time.monotonic()
            if self._cache is None or (now - self._cache_ts) >= self._cache_ttl:
                try:
                    self._cache = {t.lower() for t in self._provider() if t}
                    self._cache_ts = now
                except Exception as e:
                    # If the catalog is momentarily unavailable, reuse the last
                    # known snapshot rather than failing open or wiping the list.
                    logger.warning("SQL Firewall: allowlist provider failed — %s", e)
                    if self._cache is None:
                        self._cache = set()
            allowed |= self._cache
        return allowed

    def validate(self, sql: str) -> ValidationResult:
        """Validate a SQL query. Returns allowed=True if all tables are in the allowlist."""
        if self.allow_all:
            return ValidationResult(allowed=True)

        allowed_tables = self.allowed_tables

        try:
            parsed = sqlglot.parse(sql, dialect="trino")
        except sqlglot.errors.ParseError as e:
            logger.warning("SQL Firewall: parse error — %s", e)
            return ValidationResult(
                allowed=False,
                reason=f"Could not parse SQL: {e}",
            )

        denied: list[str] = []

        for statement in parsed:
            if statement is None:
                continue

            # Collect the names of all CTEs DEFINED in this statement. The metric
            # compiler emits CTEs named after metrics (e.g. total_revenue), and
            # sqlglot surfaces references to those names as exp.Table nodes.
            # An unqualified table reference matching a locally-defined CTE name
            # is an internal alias, not an external table, so it must be allowed.
            # Note: only the CTE NAME is exempt — real tables inside a CTE body
            # are still discovered by find_all(exp.Table) and validated normally.
            cte_names = {
                cte.alias_or_name.lower()
                for cte in statement.find_all(exp.CTE)
                if cte.alias_or_name
            }

            for table in statement.find_all(exp.Table):
                parts = []
                if table.catalog:
                    parts.append(table.catalog)
                if table.db:
                    parts.append(table.db)
                parts.append(table.name)
                table_name = ".".join(parts).lower()

                # Unqualified reference (no catalog/db) to a locally-defined CTE
                # name is an internal alias — skip it.
                if (
                    not table.catalog
                    and not table.db
                    and table.name.lower() in cte_names
                ):
                    continue

                if table_name not in allowed_tables:
                    # Check without schema prefix
                    unqualified = table_name.split(".")[-1]
                    if not any(
                        t.endswith(f".{unqualified}") for t in allowed_tables
                    ) and unqualified not in allowed_tables:
                        denied.append(".".join(parts))

        if denied:
            return ValidationResult(
                allowed=False,
                denied_tables=list(set(denied)),
                reason=f"Unauthorized tables: {', '.join(sorted(set(denied)))}",
            )

        return ValidationResult(allowed=True)

"""SQL Firewall — validates SQL queries against a table allowlist using sqlglot AST.

Adapted from Fusion-main/agent/src/clients/sql_firewall.py.
"""

from __future__ import annotations

import logging
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
    from FROM, JOIN, subqueries, CTEs, and UNION.

    If allowed_tables is empty, all queries pass (admin mode).
    Fail-closed on parse errors.
    """

    def __init__(self, allowed_tables: set[str] | None = None) -> None:
        self.allowed_tables = {t.lower() for t in (allowed_tables or set())}

    def validate(self, sql: str) -> ValidationResult:
        """Validate a SQL query. Returns allowed=True if all tables are in the allowlist."""
        if not self.allowed_tables:
            return ValidationResult(allowed=True)

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

            for table in statement.find_all(exp.Table):
                parts = []
                if table.catalog:
                    parts.append(table.catalog)
                if table.db:
                    parts.append(table.db)
                parts.append(table.name)
                table_name = ".".join(parts).lower()

                if table_name not in self.allowed_tables:
                    # Check without schema prefix
                    unqualified = table_name.split(".")[-1]
                    if not any(
                        t.endswith(f".{unqualified}") for t in self.allowed_tables
                    ) and unqualified not in self.allowed_tables:
                        denied.append(".".join(parts))

        if denied:
            return ValidationResult(
                allowed=False,
                denied_tables=list(set(denied)),
                reason=f"Unauthorized tables: {', '.join(sorted(set(denied)))}",
            )

        return ValidationResult(allowed=True)

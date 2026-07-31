"""Executor plugin architecture — abstract base and shared models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ExecutionResult:
    """Result from executing a query on any datasource."""
    success: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False  # True when the row cap was hit and more rows may exist
    duration_ms: float = 0.0
    query_execution_id: str = ""
    error: str | None = None
    error_code: str | None = None  # "connection_failed", "timeout", "permission_denied", "query_error"
    datasource_id: str | None = None


@dataclass
class ConnectionTestStep:
    """A single step in a connection test."""
    name: str
    status: str = "pending"  # "pending" | "running" | "success" | "failed"
    duration_ms: float | None = None
    error: str | None = None


@dataclass
class ConnectionTestResult:
    """Result of a full connection test."""
    success: bool
    steps: list[ConnectionTestStep] = field(default_factory=list)
    error: str | None = None


class BaseExecutor(ABC):
    """Abstract base for all datasource executors."""

    datasource_type: str = ""
    datasource_id: str = ""
    datasource_name: str = ""

    def __init__(self, datasource_id: str, datasource_name: str, config: dict):
        self.datasource_id = datasource_id
        self.datasource_name = datasource_name
        self._config = config

    @abstractmethod
    async def execute(self, sql: str, max_rows: int = 500) -> ExecutionResult:
        """Execute SQL and return results."""
        ...

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Run a full connection test with step-by-step progress."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Lightweight health check (e.g., SELECT 1)."""
        ...

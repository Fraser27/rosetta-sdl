"""Executor registry — manages datasource executor instances."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.constants import DEFAULT_DATASOURCE_ID

if TYPE_CHECKING:
    from src.executors.base import BaseExecutor

logger = logging.getLogger(__name__)


class ExecutorRegistry:
    """Singleton registry mapping datasource_id -> executor instance."""

    def __init__(self):
        self._executors: dict[str, BaseExecutor] = {}

    def register(self, datasource_id: str, executor: BaseExecutor) -> None:
        """Register an executor for a datasource."""
        self._executors[datasource_id] = executor
        logger.info("Registered executor for datasource '%s' (type: %s)", datasource_id, executor.datasource_type)

    def get(self, datasource_id: str) -> BaseExecutor | None:
        """Get executor by datasource ID. Returns None if not found."""
        return self._executors.get(datasource_id)

    def remove(self, datasource_id: str) -> None:
        """Remove an executor from the registry."""
        if datasource_id in self._executors:
            del self._executors[datasource_id]
            logger.info("Removed executor for datasource '%s'", datasource_id)

    def items(self):
        """Iterate over (datasource_id, executor) pairs."""
        return self._executors.items()

    def default_athena_id(self) -> str:
        """Return the datasource_id of the Athena executor that owns Glue-scanned tables.

        Prefers the conventional DEFAULT_DATASOURCE_ID, else the first registered
        athena executor, else '' when none exists.
        """
        if DEFAULT_DATASOURCE_ID in self._executors:
            return DEFAULT_DATASOURCE_ID
        for ds_id, ex in self._executors.items():
            if getattr(ex, "datasource_type", "") == "athena":
                return ds_id
        return ""

    def __contains__(self, datasource_id: str) -> bool:
        return datasource_id in self._executors

    def __len__(self) -> int:
        return len(self._executors)


# Global registry instance
registry = ExecutorRegistry()

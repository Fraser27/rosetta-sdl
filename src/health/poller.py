"""Background health poller — monitors datasource health and toggles metric availability."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.executors.base import HealthStatus
from src.executors.registry import registry
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3  # consecutive failures before marking unhealthy


class HealthPoller:
    """Periodically checks datasource health and disables/enables metrics accordingly."""

    def __init__(self, graph: GraphClient, interval: int = 30):
        self._graph = graph
        self._interval = interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._failure_counts: dict[str, int] = {}
        self._cached_status: dict[str, HealthStatus] = {}

    @property
    def cached_status(self) -> dict[str, HealthStatus]:
        """Get cached health status for all datasources."""
        return dict(self._cached_status)

    def get_status(self, datasource_id: str) -> HealthStatus:
        """Get cached health status for a specific datasource."""
        return self._cached_status.get(datasource_id, HealthStatus.UNKNOWN)

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Health poller started (interval: %ds)", self._interval)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health poller stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error("Health poll cycle failed: %s", e)
            await asyncio.sleep(self._interval)

    async def _check_all(self) -> None:
        """Check health of all registered datasources."""
        for datasource_id, executor in registry.items():
            try:
                status = await executor.health_check()
                await self._update_status(datasource_id, status)
            except Exception as e:
                logger.warning("Health check failed for '%s': %s", datasource_id, e)
                await self._update_status(datasource_id, HealthStatus.UNHEALTHY)

    async def _update_status(self, datasource_id: str, status: HealthStatus) -> None:
        """Update status with flapping protection."""
        if status == HealthStatus.UNHEALTHY:
            self._failure_counts[datasource_id] = self._failure_counts.get(datasource_id, 0) + 1
            if self._failure_counts[datasource_id] < FAILURE_THRESHOLD:
                logger.debug(
                    "Datasource '%s' failure %d/%d (not yet unhealthy)",
                    datasource_id, self._failure_counts[datasource_id], FAILURE_THRESHOLD,
                )
                return
        else:
            self._failure_counts[datasource_id] = 0

        previous = self._cached_status.get(datasource_id)
        self._cached_status[datasource_id] = status

        if previous == status:
            return  # No change

        logger.info("Datasource '%s' status changed: %s -> %s", datasource_id, previous, status)

        # Update graph
        now = datetime.now(timezone.utc).isoformat()
        self._graph.write(
            "MATCH (ds:DataSource {datasource_id: $ds_id}) "
            "SET ds.status = $status, ds.last_health_check = $now",
            {"ds_id": datasource_id, "status": status.value, "now": now},
        )

        # Toggle metrics
        if status == HealthStatus.UNHEALTHY:
            self._graph.write(
                "MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $ds_id}) "
                "SET m.enabled = false, m.disabled_reason = 'datasource_unhealthy'",
                {"ds_id": datasource_id},
            )
            logger.warning("Disabled metrics for unhealthy datasource '%s'", datasource_id)
        elif status == HealthStatus.HEALTHY:
            self._graph.write(
                "MATCH (m:Metric)-[:EXECUTES_ON]->(ds:DataSource {datasource_id: $ds_id}) "
                "WHERE m.disabled_reason = 'datasource_unhealthy' "
                "SET m.enabled = true, m.disabled_reason = null",
                {"ds_id": datasource_id},
            )
            logger.info("Re-enabled metrics for recovered datasource '%s'", datasource_id)

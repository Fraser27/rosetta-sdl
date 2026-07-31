"""Async test-connection job system with step-by-step progress."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.executors.base import ConnectionTestStep
from src.executors.registry import registry

logger = logging.getLogger(__name__)

# In-memory job store with TTL
_jobs: dict[str, "ConnectionTestJob"] = {}
_JOB_TTL_SECONDS = 600  # 10 minutes


@dataclass
class ConnectionTestJob:
    """Tracks a test-connection job."""
    job_id: str
    datasource_id: str
    status: str = "pending"  # "pending" | "running" | "success" | "failed"
    steps: list[dict] = field(default_factory=list)
    error: str | None = None
    started_at: str = ""
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "datasource_id": self.datasource_id,
            "status": self.status,
            "steps": self.steps,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def start_test(datasource_id: str) -> str:
    """Start a test-connection job. Returns job_id."""
    _cleanup_expired()

    job_id = f"tc_{uuid.uuid4().hex[:12]}"
    job = ConnectionTestJob(
        job_id=job_id,
        datasource_id=datasource_id,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    _jobs[job_id] = job

    # Launch async task
    asyncio.create_task(_run_test(job))
    return job_id


def get_job(job_id: str) -> dict | None:
    """Get job status. Returns None if not found or expired."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    return job.to_dict()


async def _run_test(job: ConnectionTestJob) -> None:
    """Execute the connection test in background."""
    job.status = "running"

    executor = registry.get(job.datasource_id)
    if executor is None:
        job.status = "failed"
        job.error = f"No executor registered for datasource '{job.datasource_id}'"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        return

    try:
        result = await executor.test_connection()
        job.steps = [
            {
                "name": step.name,
                "status": step.status,
                "duration_ms": step.duration_ms,
                "error": step.error,
            }
            for step in result.steps
        ]
        job.status = "success" if result.success else "failed"
        job.error = result.error
    except Exception as e:
        job.status = "failed"
        job.error = str(e)

    job.completed_at = datetime.now(timezone.utc).isoformat()


def _cleanup_expired() -> None:
    """Remove expired jobs."""
    now = datetime.now(timezone.utc)
    expired = []
    for job_id, job in _jobs.items():
        if job.started_at:
            started = datetime.fromisoformat(job.started_at)
            if (now - started).total_seconds() > _JOB_TTL_SECONDS:
                expired.append(job_id)
    for job_id in expired:
        del _jobs[job_id]

"""Central boto3 client factory.

Every AWS client in the app is created here so the region is set once, at
startup, from application config rather than resolved from ambient SDK
conventions. Region resolution otherwise falls through boto3's default chain,
which honors ``AWS_DEFAULT_REGION`` but not ``AWS_REGION``; passing
``region_name`` explicitly removes that dependency.
"""

from __future__ import annotations

from typing import Any

import boto3

# Shared session, built once at startup via configure(). Falls back to a default
# boto3 session when configure() has not run (standalone scripts, tests).
_session: boto3.Session | None = None


def configure(region: str | None) -> None:
    """Build the shared session with an explicit region.

    A falsy region passes ``region_name=None``, which keeps boto3's default
    resolution chain (e.g. EC2 instance metadata) as a last resort.
    """
    global _session
    _session = boto3.Session(region_name=region or None)


def client(service_name: str) -> Any:
    """Return a boto3 client for ``service_name`` from the shared session.

    NOTE: ``boto3.Session.client()`` is not guaranteed thread-safe for
    concurrent first-time creation. Reuse is safe. The one caller that creates
    clients concurrently (``query/embeddings.py`` via a ``ThreadPoolExecutor``)
    guards its lazy init with a lock; add a lock here too if another concurrent
    creation path ever appears.
    """
    session = _session or boto3.Session()
    return session.client(service_name)

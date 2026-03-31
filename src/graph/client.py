"""Neo4j driver wrapper — thin client for Cypher queries."""

from __future__ import annotations

import logging

from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class GraphClient:
    """Thin wrapper around the Neo4j Python driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("Connected to Neo4j at %s", uri)

    def close(self) -> None:
        self._driver.close()

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a read query and return results as list of dicts."""
        with self._driver.session() as session:
            result = session.run(cypher, params or {})
            return [record.data() for record in result]

    def write(self, cypher: str, params: dict | None = None) -> None:
        """Execute a write query inside a transaction."""
        with self._driver.session() as session:
            session.execute_write(lambda tx: tx.run(cypher, params or {}))

    def write_batch(self, cypher: str, batch: list[dict]) -> None:
        """Execute a write query for each item in batch using UNWIND."""
        with self._driver.session() as session:
            session.execute_write(
                lambda tx: tx.run(f"UNWIND $batch AS item {cypher}", {"batch": batch})
            )

    def verify_connectivity(self) -> bool:
        """Check that Neo4j is reachable."""
        try:
            self._driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error("Neo4j connectivity check failed: %s", e)
            return False

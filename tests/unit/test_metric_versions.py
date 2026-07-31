"""Shape checks for metric version-history Cypher.

Full behaviour (snapshot → link → prune-to-10 → restore → cascade-delete) is
verified end-to-end against a live Neo4j; these guard against regressions in the
query text itself (the retention cap, the relationship, cascade cleanup).
"""

from src.graph import queries


def test_snapshot_creates_versioned_node_and_links():
    q = queries.SNAPSHOT_METRIC_VERSION
    assert "CREATE (mv:MetricVersion" in q
    assert "CREATE (m)-[:HAS_VERSION]->(mv)" in q
    # Carries the definition fields needed to restore.
    for field in ("expression", "joins_json", "parameters_json", "grain", "filters"):
        assert field in q


def test_snapshot_prunes_to_last_10():
    q = queries.SNAPSHOT_METRIC_VERSION
    # Keep newest, drop the rest beyond 10.
    assert "ORDER BY old.version DESC" in q
    assert "SKIP 10" in q
    assert "DETACH DELETE old" in q


def test_list_versions_orders_newest_first():
    assert "ORDER BY mv.version DESC" in queries.LIST_METRIC_VERSIONS


def test_get_version_matches_specific_version():
    assert "MetricVersion {version: $version}" in queries.GET_METRIC_VERSION


def test_delete_metric_cascades_versions():
    # Deleting a metric must not orphan its version snapshots.
    assert "HAS_VERSION" in queries.DELETE_METRIC
    assert "MetricVersion" in queries.DELETE_METRIC
    assert "DETACH DELETE m, mv" in queries.DELETE_METRIC

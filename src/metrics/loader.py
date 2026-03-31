"""Metrics YAML loader — parses metric definitions from YAML."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.catalog.models import JoinPath, MetricDefinition

logger = logging.getLogger(__name__)


def load_metrics(metrics_file: str) -> tuple[list[MetricDefinition], list[JoinPath]]:
    """Load metrics and join paths from a YAML file."""
    path = Path(metrics_file)
    if not path.exists():
        logger.warning("Metrics file not found: %s", metrics_file)
        return [], []

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    metrics = []
    for m in data.get("metrics", []):
        metrics.append(MetricDefinition(
            metric_id=m["metric_id"],
            name=m["name"],
            synonyms=m.get("synonyms", []),
            definition=m.get("definition", ""),
            type=m.get("type", "simple"),
            expression=m["expression"],
            source_table=m["source_table"],
            filters=m.get("filters", []),
            grain=m.get("grain", []),
            time_grains=m.get("time_grains", []),
            owner=m.get("owner", ""),
        ))

    joins = []
    for j in data.get("join_paths", []):
        joins.append(JoinPath(
            source_table=j["source"],
            target_table=j["target"],
            on_column=j["on"],
            join_type=j.get("join_type", "INNER"),
        ))

    logger.info("Loaded %d metrics and %d join paths from %s", len(metrics), len(joins), path)
    return metrics, joins

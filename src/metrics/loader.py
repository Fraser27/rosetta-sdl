"""Metrics YAML loader — parses metric definitions from YAML."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.catalog.models import JoinPath, MetricDefinition, MetricParameter

logger = logging.getLogger(__name__)


def load_metrics(metrics_file: str) -> tuple[list[MetricDefinition], list[JoinPath]]:
    """Load metrics and join paths from a YAML file.

    Each metric entry is validated via the MetricDefinition pydantic model.
    Invalid entries are skipped (with a WARNING) rather than aborting the whole
    file. Duplicate metric_ids keep the first occurrence. Missing files log a
    WARNING and return empty lists; malformed YAML logs an ERROR and returns
    empty lists.
    """
    if not metrics_file:
        logger.info("No metrics file configured — skipping YAML metric loading")
        return [], []
    path = Path(metrics_file)
    if not path.exists():
        logger.warning("Metrics file not found: %s", metrics_file)
        return [], []

    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        # Surface the parse problem (line/column) if PyYAML provides a mark.
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            logger.error(
                "Malformed YAML in metrics file %s at line %d, column %d: %s",
                path,
                mark.line + 1,
                mark.column + 1,
                getattr(exc, "problem", exc),
            )
        else:
            logger.error("Malformed YAML in metrics file %s: %s", path, exc)
        return [], []

    if not isinstance(data, dict):
        logger.error(
            "Malformed metrics file %s: expected a mapping at the top level, got %s",
            path,
            type(data).__name__,
        )
        return [], []

    metrics: list[MetricDefinition] = []
    seen_ids: set[str] = set()
    for idx, m in enumerate(data.get("metrics", []) or []):
        # Identify the entry for logging even before validation succeeds.
        entry_id = None
        if isinstance(m, dict):
            entry_id = m.get("metric_id")
        label = f"metric_id={entry_id!r}" if entry_id else f"index {idx}"

        # Duplicate detection — keep the FIRST occurrence.
        if entry_id and entry_id in seen_ids:
            logger.warning(
                "Duplicate metric_id %r in %s — keeping first occurrence, skipping this one",
                entry_id,
                path,
            )
            continue

        if not isinstance(m, dict):
            logger.warning(
                "Skipping metric entry at %s: expected a mapping, got %s",
                label,
                type(m).__name__,
            )
            continue

        try:
            parameters = [MetricParameter(**p) for p in m.get("parameters", []) or []]
            metric = MetricDefinition(
                metric_id=m["metric_id"],
                name=m["name"],
                synonyms=m.get("synonyms", []),
                definition=m.get("definition", ""),
                type=m.get("type", "simple"),
                expression=m["expression"],
                source_table=m.get("source_table", ""),
                filters=m.get("filters", []),
                grain=m.get("grain", []),
                parameters=parameters,
                time_grains=m.get("time_grains", []),
                time_grain_column=m.get("time_grain_column", ""),
                aggregation=m.get("aggregation", "additive"),
                value_type=m.get("value_type", "number"),
                unit=m.get("unit", ""),
                format=m.get("format", ""),
                owner=m.get("owner", ""),
            )
        except (ValidationError, KeyError, TypeError) as exc:
            logger.warning(
                "Skipping invalid metric entry (%s) in %s: %s",
                label,
                path,
                exc,
            )
            continue

        if metric.metric_id:
            seen_ids.add(metric.metric_id)
        metrics.append(metric)

    joins = []
    for j in data.get("join_paths", []) or []:
        # Note: YAML 1.1 (PyYAML) interprets bare `on` as boolean True
        on_col = j.get("on") or j.get("on_column") or j.get(True, "")
        joins.append(JoinPath(
            source_table=j["source"],
            target_table=j["target"],
            on_column=on_col,
            join_type=j.get("join_type", "INNER"),
        ))

    logger.info("Loaded %d metrics and %d join paths from %s", len(metrics), len(joins), path)
    return metrics, joins

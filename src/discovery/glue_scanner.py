"""Glue catalog scanner — discovers tables/columns from AWS Glue Data Catalog."""

from __future__ import annotations

import logging
from collections import defaultdict

import boto3

from src.catalog.models import ColumnMeta, JoinPath, TableMeta
from src.config import DatabaseConfig

logger = logging.getLogger(__name__)


def discover_all_databases() -> list[TableMeta]:
    """Auto-discover ALL Glue databases and scan every table in each."""
    glue = boto3.client("glue")
    all_tables: list[TableMeta] = []

    logger.info("Auto-discovering all Glue databases...")
    db_paginator = glue.get_paginator("get_databases")
    db_names: list[str] = []

    for page in db_paginator.paginate():
        for db in page.get("DatabaseList", []):
            db_names.append(db["Name"])

    logger.info("Found %d Glue databases: %s", len(db_names), db_names)

    for db_name in db_names:
        logger.info("Scanning Glue database: %s", db_name)
        try:
            paginator = glue.get_paginator("get_tables")
            for page in paginator.paginate(DatabaseName=db_name):
                for glue_table in page.get("TableList", []):
                    table = _parse_glue_table(glue_table, db_name, "glue")
                    all_tables.append(table)
        except Exception as e:
            logger.error("Error scanning database '%s': %s", db_name, e)

    logger.info("Discovered %d tables across %d databases", len(all_tables), len(db_names))
    return all_tables


def scan_databases(databases: list[DatabaseConfig]) -> list[TableMeta]:
    """Scan Glue catalog for all tables in the configured databases."""
    glue = boto3.client("glue")
    all_tables: list[TableMeta] = []

    for db_config in databases:
        db_name = db_config.glue_database
        logger.info("Scanning Glue database: %s", db_name)

        try:
            paginator = glue.get_paginator("get_tables")
            for page in paginator.paginate(DatabaseName=db_name):
                for glue_table in page.get("TableList", []):
                    table = _parse_glue_table(glue_table, db_name, db_config.catalog_type)
                    all_tables.append(table)
        except glue.exceptions.EntityNotFoundException:
            logger.warning("Glue database '%s' not found, skipping", db_name)
        except Exception as e:
            logger.error("Error scanning database '%s': %s", db_name, e)

    logger.info("Discovered %d tables across %d databases", len(all_tables), len(databases))
    return all_tables


def infer_joins(tables: list[TableMeta]) -> list[JoinPath]:
    """Detect potential join paths by matching column names across tables.

    For each pair of tables (including cross-database), if they share a column
    name, create a join path. Skips generic columns that would produce noise.
    """
    SKIP_COLUMNS = {
        "year", "month", "day", "date", "timestamp", "created_at", "updated_at",
        "partition_0", "partition_1", "partition_2", "partition_3",
    }

    # Build index: column_name -> list of table full_names
    col_to_tables: dict[str, list[str]] = defaultdict(list)
    table_cols: dict[str, set[str]] = {}

    for table in tables:
        col_names = {c.name.lower() for c in table.columns} - SKIP_COLUMNS
        table_cols[table.full_name] = col_names
        for col in col_names:
            col_to_tables[col].append(table.full_name)

    joins: list[JoinPath] = []
    seen: set[tuple[str, str, str]] = set()

    for col_name, table_list in col_to_tables.items():
        if len(table_list) < 2:
            continue
        # Create join for each unique pair
        for i, src in enumerate(table_list):
            for tgt in table_list[i + 1:]:
                key = (min(src, tgt), max(src, tgt), col_name)
                if key in seen:
                    continue
                seen.add(key)
                joins.append(JoinPath(
                    source_table=src,
                    target_table=tgt,
                    on_column=col_name,
                    join_type="INNER",
                ))

    logger.info("Inferred %d potential join paths from column name matching", len(joins))
    return joins


def _parse_glue_table(glue_table: dict, database: str, catalog_type: str) -> TableMeta:
    """Parse a Glue table response into our TableMeta model."""
    table_name = glue_table["Name"]
    description = glue_table.get("Description", "")

    # Detect Iceberg tables
    params = glue_table.get("Parameters", {})
    if params.get("table_type") == "ICEBERG" or "iceberg" in params.get("metadata_location", ""):
        catalog_type = "iceberg"

    # Parse columns from StorageDescriptor + PartitionKeys
    columns: list[ColumnMeta] = []
    sd = glue_table.get("StorageDescriptor", {})
    for col in sd.get("Columns", []):
        columns.append(ColumnMeta(
            name=col["Name"],
            data_type=col.get("Type", "string"),
            description=col.get("Comment", ""),
            is_partition=False,
        ))

    for part_col in glue_table.get("PartitionKeys", []):
        columns.append(ColumnMeta(
            name=part_col["Name"],
            data_type=part_col.get("Type", "string"),
            description=part_col.get("Comment", ""),
            is_partition=True,
        ))

    return TableMeta(
        database=database,
        name=table_name,
        columns=columns,
        description=description,
        catalog_type=catalog_type,
    )

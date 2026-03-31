"""Glue catalog scanner — discovers tables/columns from AWS Glue Data Catalog."""

from __future__ import annotations

import logging

import boto3

from src.catalog.models import ColumnMeta, TableMeta
from src.config import DatabaseConfig

logger = logging.getLogger(__name__)


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

"""Pydantic models for the semantic layer catalog."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnMeta(BaseModel):
    name: str
    data_type: str = "string"
    description: str = ""
    is_partition: bool = False
    is_primary_key: bool = False


class TableMeta(BaseModel):
    database: str
    name: str
    full_name: str = ""  # database.name
    columns: list[ColumnMeta] = Field(default_factory=list)
    description: str = ""
    catalog_type: str = "glue"  # glue | iceberg | federated
    row_count_approx: int = 0

    def model_post_init(self, __context: object) -> None:
        if not self.full_name:
            self.full_name = f"{self.database}.{self.name}"


class JoinPath(BaseModel):
    source_table: str  # full_name
    target_table: str  # full_name
    on_column: str
    join_type: str = "INNER"


class MetricDefinition(BaseModel):
    metric_id: str
    name: str
    synonyms: list[str] = Field(default_factory=list)
    definition: str = ""
    type: str = "simple"  # simple | derived
    expression: str  # SQL aggregate expression
    source_table: str  # fully-qualified table name
    filters: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    time_grains: list[str] = Field(default_factory=list)
    owner: str = ""


class DocumentMeta(BaseModel):
    name: str
    s3_key: str = ""
    vector_bucket: str = ""
    vector_index: str = ""
    description: str = ""
    type: str = "document"  # document | policy | manual
    metadata_keys: list[ColumnMeta] = Field(default_factory=list)  # queryable attributes (excl. embeddings)


# -- API response models --

class TableSummary(BaseModel):
    full_name: str
    name: str
    database: str
    description: str = ""
    catalog_type: str = ""
    datasource: str = ""


class MetricSummary(BaseModel):
    metric_id: str
    name: str
    definition: str = ""
    expression: str = ""
    type: str = "simple"
    source_table: str = ""
    synonyms: list[str] | None = Field(default_factory=list)
    grain: list[str] | None = Field(default_factory=list)


class SearchResult(BaseModel):
    type: str  # table | metric | document
    id: str
    name: str
    description: str = ""
    score: float = 0.0


class QueryRoute:
    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"
    BOTH = "both"


class QueryResponse(BaseModel):
    route: str  # structured | unstructured | both
    intent: str = ""  # metric | analytical | schema | document
    metric_name: str | None = None
    sql: str | None = None
    results: dict | None = None
    vector_results: list[dict] | None = None
    error: str | None = None


class QueryPlan(BaseModel):
    """Query plan without execution — returns SQL/search params for external execution."""
    route: str  # structured | unstructured | both
    intent: str = ""  # metric | analytical | document
    metric_name: str | None = None
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    join_paths: list[dict] = Field(default_factory=list)
    vector_searches: list[dict] = Field(default_factory=list)  # [{bucket, index}]
    error: str | None = None

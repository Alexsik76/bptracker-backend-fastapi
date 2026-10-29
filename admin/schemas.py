from typing import Any, Literal

from pydantic import BaseModel, Field

ColumnType = Literal[
    "string",
    "text",
    "integer",
    "number",
    "boolean",
    "date",
    "datetime",
    "uuid",
    "json",
    "enum",
]


class ColumnMetadata(BaseModel):
    name: str
    type: ColumnType
    nullable: bool
    read_only: bool
    foreign_key: str | None = None
    enum_values: list[str] | None = None


class TableMetadata(BaseModel):
    name: str
    module: str
    primary_key: list[str]
    columns: list[ColumnMetadata]
    can_create: bool
    can_update: bool
    can_delete: bool


class RowsResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class CreateRowRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class UpdateRowRequest(BaseModel):
    pk: dict[str, Any]
    values: dict[str, Any] = Field(default_factory=dict)


class DeleteRowRequest(BaseModel):
    pk: dict[str, Any]

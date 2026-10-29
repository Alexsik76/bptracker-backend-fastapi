from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from admin.registry import TABLE_REGISTRY, TableConfig


class AdminServiceError(Exception):
    """Base exception for admin service operations."""


class TableNotFoundError(AdminServiceError):
    """Raised when the requested table is not found in the registry."""


class ForbiddenOperationError(AdminServiceError):
    """Raised when an operation is not permitted on the table."""


class InvalidColumnError(AdminServiceError):
    """Raised when a column is unknown, hidden, or read-only."""


class CoercionError(AdminServiceError):
    """Raised when a column value fails coercion to its expected Python type."""

    def __init__(self, column: str, message: str) -> None:
        self.column = column
        self.message = message
        super().__init__(f"Coercion error on column '{column}': {message}")


class RowNotFoundError(AdminServiceError):
    """Raised when a specified row is not found."""


class ConflictError(AdminServiceError):
    """Raised when a database integrity constraint is violated."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def classify_column_type(col: Column) -> tuple[str, list[str] | None]:
    raw_type = col.type
    if isinstance(raw_type, sa.Enum):
        return "enum", [str(val) for val in raw_type.enums]

    t = raw_type
    while hasattr(t, "impl"):
        if isinstance(t, sa.Enum):
            return "enum", [str(val) for val in t.enums]
        t = t.impl

    if isinstance(t, sa.Enum):
        return "enum", [str(val) for val in t.enums]
    elif isinstance(t, (JSONB, sa.JSON)):
        return "json", None
    elif isinstance(t, sa.Uuid):
        return "uuid", None
    elif isinstance(t, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
        return "integer", None
    elif isinstance(t, (sa.Float, sa.Numeric)):
        return "number", None
    elif isinstance(t, sa.Boolean):
        return "boolean", None
    elif isinstance(t, sa.DateTime):
        return "datetime", None
    elif isinstance(t, sa.Date):
        return "date", None
    elif isinstance(t, sa.Text):
        return "text", None
    else:
        # String, AutoString, LargeBinary, Time, etc.
        return "string", None


def coerce_column_value(col: Column, val: Any) -> Any:
    col_name = col.name
    if val is None:
        if col.nullable or col.server_default is not None or col.default is not None:
            return None
        raise CoercionError(col_name, f"Column '{col_name}' cannot be null")

    col_type_str, enum_values = classify_column_type(col)

    if col_type_str == "uuid":
        if isinstance(val, UUID):
            return val
        try:
            return UUID(str(val))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CoercionError(col_name, f"Invalid UUID: {val}") from exc

    elif col_type_str == "integer":
        if isinstance(val, bool):
            raise CoercionError(col_name, f"Expected integer, got boolean: {val}")
        try:
            return int(val)
        except (ValueError, TypeError) as exc:
            raise CoercionError(col_name, f"Invalid integer: {val}") from exc

    elif col_type_str == "number":
        if isinstance(val, bool):
            raise CoercionError(col_name, f"Expected number, got boolean: {val}")
        try:
            return float(val)
        except (ValueError, TypeError) as exc:
            raise CoercionError(col_name, f"Invalid number: {val}") from exc

    elif col_type_str == "boolean":
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            low = val.strip().lower()
            if low in ("true", "1", "t", "yes"):
                return True
            if low in ("false", "0", "f", "no"):
                return False
            raise CoercionError(col_name, f"Invalid boolean: {val}")
        if val == 1:
            return True
        if val == 0:
            return False
        raise CoercionError(col_name, f"Invalid boolean: {val}")

    elif col_type_str == "date":
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return date.fromisoformat(val)
            except (ValueError, TypeError) as exc:
                raise CoercionError(col_name, f"Invalid date: {val}") from exc
        raise CoercionError(col_name, f"Invalid date: {val}")

    elif col_type_str == "datetime":
        if isinstance(val, datetime):
            if val.tzinfo is None or val.tzinfo.utcoffset(val) is None:
                raise CoercionError(
                    col_name, "Naive datetimes are rejected; must be timezone-aware"
                )
            return val
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val)
            except (ValueError, TypeError) as exc:
                raise CoercionError(col_name, f"Invalid datetime: {val}") from exc
            if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                raise CoercionError(
                    col_name, "Naive datetimes are rejected; must be timezone-aware"
                )
            return dt
        raise CoercionError(col_name, f"Invalid datetime: {val}")

    elif col_type_str == "enum":
        raw_type = col.type
        enum_cls = getattr(raw_type, "enum_class", None)
        if enum_cls is None and hasattr(raw_type, "impl"):
            enum_cls = getattr(raw_type.impl, "enum_class", None)

        if enum_cls is not None:
            if isinstance(val, enum_cls):
                return val
            try:
                return enum_cls(val)
            except (ValueError, KeyError):
                for member in enum_cls:
                    if str(member.value) == str(val) or member.name == str(val):
                        return member
                raise CoercionError(
                    col_name, f"Invalid enum value '{val}' for {col_name}"
                ) from None
        elif enum_values:
            if str(val) not in enum_values:
                raise CoercionError(col_name, f"Invalid enum value '{val}' for {col_name}")
            return str(val)
        return val

    elif col_type_str == "json":
        return val

    elif col_type_str in ("string", "text"):
        raw_type = col.type
        while hasattr(raw_type, "impl"):
            raw_type = raw_type.impl
        if isinstance(raw_type, sa.Time):
            if isinstance(val, time):
                return val
            if isinstance(val, str):
                try:
                    return time.fromisoformat(val)
                except (ValueError, TypeError) as exc:
                    raise CoercionError(col_name, f"Invalid time: {val}") from exc
            raise CoercionError(col_name, f"Invalid time: {val}")

        if isinstance(val, (dict, list)):
            raise CoercionError(col_name, f"Invalid string: got {type(val).__name__}")
        if not isinstance(val, (str, bytes)):
            return str(val)
        return val

    return val


def serialize_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, time):
        return val.isoformat()
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [serialize_value(v) for v in val]
    return val


def serialize_row(instance_or_mapping: Any, visible_cols: list[Column]) -> dict[str, Any]:
    result = {}
    for col in visible_cols:
        if hasattr(instance_or_mapping, col.name):
            val = getattr(instance_or_mapping, col.name)
        elif hasattr(instance_or_mapping, "__getitem__"):
            val = instance_or_mapping[col.name]
        else:
            val = getattr(instance_or_mapping, col.name, None)
        result[col.name] = serialize_value(val)
    return result


def _get_table_config(table_name: str) -> TableConfig:
    cfg = TABLE_REGISTRY.get(table_name)
    if cfg is None:
        raise TableNotFoundError(f"Table '{table_name}' not found")
    return cfg


def get_tables_metadata() -> list[dict[str, Any]]:
    result = []
    for table_name, cfg in TABLE_REGISTRY.items():
        table = cfg.model.__table__
        effective_hidden = cfg.get_effective_hidden_columns()
        effective_read_only = cfg.get_effective_read_only_columns()
        pk_cols = [c.name for c in table.primary_key.columns if c.name not in effective_hidden]

        columns_meta = []
        for col in table.columns:
            if col.name in effective_hidden:
                continue
            col_type, enum_values = classify_column_type(col)
            fk_target = None
            if col.foreign_keys:
                fk_target = next(iter(col.foreign_keys)).target_fullname

            columns_meta.append(
                {
                    "name": col.name,
                    "type": col_type,
                    "nullable": col.nullable,
                    "read_only": col.name in effective_read_only,
                    "foreign_key": fk_target,
                    "enum_values": enum_values,
                }
            )

        module = cfg.model.__module__.split(".")[0]
        result.append(
            {
                "name": table_name,
                "module": module,
                "primary_key": pk_cols,
                "columns": columns_meta,
                "can_create": cfg.can_create,
                "can_update": cfg.can_update,
                "can_delete": cfg.can_delete,
            }
        )
    result.sort(key=lambda t: (t["module"], t["name"]))
    return result


async def list_rows(
    table_name: str,
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    desc: bool = False,
    search: str | None = None,
    filters: list[str] | None = None,
) -> dict[str, Any]:
    cfg = _get_table_config(table_name)
    table = cfg.model.__table__
    effective_hidden = cfg.get_effective_hidden_columns()
    visible_cols = [c for c in table.columns if c.name not in effective_hidden]
    visible_col_names = {c.name for c in visible_cols}

    conditions = []
    if filters:
        for f in filters:
            col_name, sep, filter_val = f.partition(":")
            if not sep:
                raise InvalidColumnError(f"Invalid filter format '{f}', expected 'column:value'")
            if col_name not in visible_col_names:
                raise InvalidColumnError(f"Unknown or hidden column '{col_name}' in filter")
            col = table.c[col_name]
            coerced_val = coerce_column_value(col, filter_val)
            conditions.append(col == coerced_val)

    if search:
        search_text = search.strip()
        if search_text:
            str_cols = [c for c in visible_cols if classify_column_type(c)[0] in ("string", "text")]
            if str_cols:
                conditions.append(
                    sa.or_(*(sa.cast(c, sa.String).ilike(f"%{search_text}%") for c in str_cols))
                )
            else:
                conditions.append(sa.false())

    count_stmt = select(sa.func.count()).select_from(table)
    if conditions:
        count_stmt = count_stmt.where(sa.and_(*conditions))
    total = (await session.exec(count_stmt)).one()

    stmt = select(cfg.model)
    if conditions:
        stmt = stmt.where(sa.and_(*conditions))

    if order_by:
        if order_by not in visible_col_names:
            raise InvalidColumnError(f"Unknown or hidden column '{order_by}' in order_by")
        order_col = table.c[order_by]
        stmt = stmt.order_by(order_col.desc() if desc else order_col.asc())
    else:
        pk_cols = [c for c in table.primary_key.columns if c.name in visible_col_names]
        if pk_cols:
            stmt = stmt.order_by(*(c.asc() for c in pk_cols))

    clamped_limit = min(max(1, limit), 500)
    clamped_offset = max(0, offset)
    stmt = stmt.limit(clamped_limit).offset(clamped_offset)

    result = await session.exec(stmt)
    instances = result.all()
    items = [serialize_row(inst, visible_cols) for inst in instances]

    return {
        "items": items,
        "total": total,
        "limit": clamped_limit,
        "offset": clamped_offset,
    }


def _validate_and_build_pk_conditions(
    table: sa.Table,
    pk: dict[str, Any],
) -> list[sa.BinaryExpression]:
    pk_cols = list(table.primary_key.columns)
    pk_names = {c.name for c in pk_cols}
    if set(pk.keys()) != pk_names:
        raise InvalidColumnError(f"Primary key must specify exact columns: {sorted(pk_names)}")

    conditions = []
    for c in pk_cols:
        val = pk[c.name]
        coerced = coerce_column_value(c, val)
        conditions.append(c == coerced)
    return conditions


async def get_row(
    table_name: str,
    pk: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    cfg = _get_table_config(table_name)
    table = cfg.model.__table__
    effective_hidden = cfg.get_effective_hidden_columns()
    visible_cols = [c for c in table.columns if c.name not in effective_hidden]

    pk_conditions = _validate_and_build_pk_conditions(table, pk)
    stmt = select(cfg.model).where(sa.and_(*pk_conditions))
    res = await session.exec(stmt)
    instance = res.first()
    if instance is None:
        raise RowNotFoundError(f"Row with pk {pk} not found in table '{table_name}'")

    return serialize_row(instance, visible_cols)


async def create_row(
    table_name: str,
    values: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    cfg = _get_table_config(table_name)
    if not cfg.can_create:
        raise ForbiddenOperationError(f"Create operation not allowed on table '{table_name}'")

    table = cfg.model.__table__
    all_cols = {c.name: c for c in table.columns}
    effective_hidden = cfg.get_effective_hidden_columns()
    effective_read_only = cfg.get_effective_read_only_columns()

    for col_name in values:
        if col_name not in all_cols:
            raise InvalidColumnError(f"Unknown column '{col_name}' in values")
        if col_name in effective_hidden:
            raise InvalidColumnError(f"Column '{col_name}' is hidden")
        if col_name in effective_read_only:
            raise InvalidColumnError(f"Column '{col_name}' is read-only")

    coerced_values = {
        col_name: coerce_column_value(all_cols[col_name], val) for col_name, val in values.items()
    }

    instance = cfg.model(**coerced_values)
    session.add(instance)
    try:
        await session.commit()
        await session.refresh(instance)
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(str(exc.orig or exc)) from exc

    visible_cols = [c for c in table.columns if c.name not in effective_hidden]
    return serialize_row(instance, visible_cols)


async def update_row(
    table_name: str,
    pk: dict[str, Any],
    values: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    cfg = _get_table_config(table_name)
    if not cfg.can_update:
        raise ForbiddenOperationError(f"Update operation not allowed on table '{table_name}'")

    table = cfg.model.__table__
    all_cols = {c.name: c for c in table.columns}
    effective_hidden = cfg.get_effective_hidden_columns()
    effective_read_only = cfg.get_effective_read_only_columns()

    pk_conditions = _validate_and_build_pk_conditions(table, pk)

    for col_name in values:
        if col_name not in all_cols:
            raise InvalidColumnError(f"Unknown column '{col_name}' in values")
        if col_name in effective_hidden:
            raise InvalidColumnError(f"Column '{col_name}' is hidden")
        if col_name in effective_read_only:
            raise InvalidColumnError(f"Column '{col_name}' is read-only")

    coerced_values = {
        col_name: coerce_column_value(all_cols[col_name], val) for col_name, val in values.items()
    }

    stmt = select(cfg.model).where(sa.and_(*pk_conditions))
    res = await session.exec(stmt)
    instance = res.first()
    if instance is None:
        raise RowNotFoundError(f"Row with pk {pk} not found in table '{table_name}'")

    for col_name, val in coerced_values.items():
        setattr(instance, col_name, val)

    session.add(instance)
    try:
        await session.commit()
        await session.refresh(instance)
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(str(exc.orig or exc)) from exc

    visible_cols = [c for c in table.columns if c.name not in effective_hidden]
    return serialize_row(instance, visible_cols)


async def delete_row(
    table_name: str,
    pk: dict[str, Any],
    session: AsyncSession,
) -> None:
    cfg = _get_table_config(table_name)
    if not cfg.can_delete:
        raise ForbiddenOperationError(f"Delete operation not allowed on table '{table_name}'")

    table = cfg.model.__table__
    pk_conditions = _validate_and_build_pk_conditions(table, pk)

    stmt = select(cfg.model).where(sa.and_(*pk_conditions))
    res = await session.exec(stmt)
    instance = res.first()
    if instance is None:
        raise RowNotFoundError(f"Row with pk {pk} not found in table '{table_name}'")

    await session.delete(instance)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(str(exc.orig or exc)) from exc

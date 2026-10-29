from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import cache
from app_settings.models import AppSetting
from app_settings.registry import REGISTRY, SettingDef
from app_settings.schemas import SettingRead
from config import Settings


class UnknownSettingError(Exception):
    pass


def _get_definition(key: str) -> SettingDef:
    definition = REGISTRY.get(key)
    if definition is None:
        raise UnknownSettingError(f"Unknown setting: {key}")
    return definition


def _to_read(definition: SettingDef, row: AppSetting | None, settings: Settings) -> SettingRead:
    env_value = getattr(settings, definition.env_attr)
    return SettingRead(
        key=definition.key,
        label=definition.label,
        type=definition.type,
        constraints=definition.constraints,
        value=row.value if row is not None else env_value,
        source="db" if row is not None else "env",
        env_value=env_value,
        updated_at=row.updated_at if row is not None else None,
        updated_by=row.updated_by if row is not None else None,
    )


async def list_settings(session: AsyncSession, settings: Settings) -> list[SettingRead]:
    rows = {row.key: row for row in (await session.exec(select(AppSetting))).all()}
    return [_to_read(d, rows.get(d.key), settings) for d in REGISTRY.values()]


async def set_setting(
    session: AsyncSession,
    settings: Settings,
    key: str,
    value: Any,
    admin_id: UUID,
) -> SettingRead:
    definition = _get_definition(key)
    valid_value = definition.validate(value)

    row = await session.get(AppSetting, key)
    now = datetime.now(UTC)
    if row is None:
        row = AppSetting(key=key, value=valid_value, updated_at=now, updated_by=admin_id)
    else:
        row.value = valid_value
        row.updated_at = now
        row.updated_by = admin_id
    session.add(row)
    await session.commit()

    cache.set_cached(key, valid_value)
    return _to_read(definition, row, settings)


async def reset_setting(session: AsyncSession, key: str) -> None:
    _get_definition(key)
    row = await session.get(AppSetting, key)
    if row is not None:
        await session.delete(row)
        await session.commit()
    cache.remove_cached(key)

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings.models import AppSetting
from app_settings.registry import REGISTRY, SettingValueError
from config import get_settings

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 60

_values: dict[str, Any] = {}


def get_value(key: str) -> Any:
    """Return the effective value: the cached DB value, else the env Settings value."""
    if key in _values:
        return _values[key]
    return getattr(get_settings(), REGISTRY[key].env_attr)


def set_cached(key: str, value: Any) -> None:
    _values[key] = value


def remove_cached(key: str) -> None:
    _values.pop(key, None)


def clear() -> None:
    _values.clear()


async def load(session: AsyncSession) -> None:
    rows = (await session.exec(select(AppSetting))).all()
    fresh: dict[str, Any] = {}
    for row in rows:
        definition = REGISTRY.get(row.key)
        if definition is None:
            continue
        try:
            fresh[row.key] = definition.validate(row.value)
        except SettingValueError:
            logger.warning("Ignoring invalid stored value for setting %s", row.key)
    # Single reassignment of contents so readers never see a half-built cache.
    _values.clear()
    _values.update(fresh)


async def run_refresher(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            async with session_factory() as session:
                await load(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to refresh app settings cache")

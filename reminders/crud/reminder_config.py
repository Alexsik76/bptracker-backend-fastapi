from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from reminders.models import ReminderConfig, ReminderConfigCreate


async def get_reminder_config(
    session: AsyncSession,
    user_id: UUID,
) -> ReminderConfig | None:
    """Return the user's reminder config, or None if not yet set up."""
    statement = select(ReminderConfig).where(ReminderConfig.user_id == user_id)
    result = await session.exec(statement)
    return result.first()


async def upsert_reminder_config(
    session: AsyncSession,
    data: ReminderConfigCreate,
    user_id: UUID,
) -> ReminderConfig:
    """Create the user's reminder config, or fully overwrite it if one already exists."""
    config = await get_reminder_config(session, user_id)
    if config is None:
        config = ReminderConfig.model_validate(data, update={"user_id": user_id})
    else:
        for field, value in data.model_dump().items():
            setattr(config, field, value)
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config

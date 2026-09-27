from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from reminders import crud
from reminders.models import ReminderConfig, ReminderConfigCreate, ReminderConfigRead

router = APIRouter(prefix="/reminders/config")


@router.get("", response_model=ReminderConfigRead)
async def get_reminder_config(
    session: SessionDep,
    user_id: CurrentUserId,
) -> ReminderConfig:
    config = await crud.get_reminder_config(session, user_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reminder config not found"
        )
    return config


@router.put("", response_model=ReminderConfigRead)
async def upsert_reminder_config(
    data: ReminderConfigCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> ReminderConfig:
    return await crud.upsert_reminder_config(session, data, user_id)

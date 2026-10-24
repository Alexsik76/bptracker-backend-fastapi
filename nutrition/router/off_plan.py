from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import OffPlanEntry, OffPlanEntryCreate, OffPlanEntryRead

router = APIRouter(prefix="/off-plan")


@router.get("", response_model=list[OffPlanEntryRead])
async def list_off_plan_entries(
    date: date,
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[OffPlanEntry]:
    return await crud.get_off_plan_entries(session, user_id, date)


@router.post("", response_model=OffPlanEntryRead, status_code=status.HTTP_201_CREATED)
async def create_off_plan_entry(
    data: OffPlanEntryCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> OffPlanEntry:
    if not data.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Off-plan entry text cannot be empty",
        )
    return await crud.create_off_plan_entry(session, user_id, data)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_off_plan_entry(
    entry_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_off_plan_entry(session, entry_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Off-plan entry not found",
        )

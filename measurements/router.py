from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from db import SessionDep
from measurements import crud
from measurements.models import MeasurementCreate, MeasurementRead

# --- Temporary auth stand-in -------------------------------------------------
# Until the auth module lands, "who is calling" is a single hardcoded user
# (matches the dev-seed user). Centralised here so endpoints never see a raw
# UUID: when real auth arrives, only this function changes — routes stay as-is.
# This is the seam where authorization ("is this caller allowed") will attach.
_DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user_id() -> UUID:
    return _DEV_USER_ID


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
# -----------------------------------------------------------------------------


router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.post("", response_model=MeasurementRead, status_code=status.HTTP_201_CREATED)
async def create_measurement(
    data: MeasurementCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MeasurementRead:
    return await crud.create_measurement(session, data, user_id)


@router.get("", response_model=Sequence[MeasurementRead])
async def list_measurements(
    session: SessionDep,
    user_id: CurrentUserId,
) -> Sequence[MeasurementRead]:
    return await crud.get_measurements(session, user_id)

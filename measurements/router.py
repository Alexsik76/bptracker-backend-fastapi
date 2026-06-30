from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from db import SessionDep
from measurements import crud
from measurements.models import Measurement, MeasurementCreate, MeasurementRead, MeasurementUpdate

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
) -> Measurement:
    return await crud.create_measurement(session, data, user_id)


@router.get("", response_model=Sequence[MeasurementRead])
async def list_measurements(
    session: SessionDep,
    user_id: CurrentUserId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Measurement]:
    return await crud.get_measurements(session, user_id, limit, offset)


@router.get("/{measurement_id}", response_model=MeasurementRead)
async def get_measurement(
    measurement_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Measurement:
    measurement = await crud.get_measurement(session, measurement_id, user_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return measurement


@router.patch("/{measurement_id}", response_model=MeasurementRead)
async def update_measurement(
    measurement_id: UUID,
    data: MeasurementUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Measurement:
    measurement = await crud.update_measurement(session, measurement_id, data, user_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return measurement


@router.delete("/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_measurement(
    measurement_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_measurement(session, measurement_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Measurement not found")

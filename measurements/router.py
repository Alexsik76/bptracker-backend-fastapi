from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from auth.deps import CurrentUserId
from db import SessionDep
from measurements import crud
from measurements.models import Measurement, MeasurementCreate, MeasurementRead, MeasurementUpdate

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
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> Sequence[Measurement]:
    return await crud.get_measurements(session, user_id, days)


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

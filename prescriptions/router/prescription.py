from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from db import SessionDep
from prescriptions import crud
from prescriptions.models import (
    Prescription,
    PrescriptionCreate,
    PrescriptionRead,
    PrescriptionUpdate,
)
from prescriptions.router.deps import CurrentUserId

router = APIRouter(prefix="/prescriptions")


@router.post("", response_model=PrescriptionRead, status_code=status.HTTP_201_CREATED)
async def create_prescription(
    data: PrescriptionCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Prescription:
    return await crud.create_prescription(session, data, user_id)


@router.get("", response_model=Sequence[PrescriptionRead])
async def list_prescriptions(
    session: SessionDep,
    user_id: CurrentUserId,
) -> Sequence[Prescription]:
    return await crud.get_prescriptions(session, user_id)


@router.get("/{prescription_id}", response_model=PrescriptionRead)
async def get_prescription(
    prescription_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Prescription:
    prescription = await crud.get_prescription(session, prescription_id, user_id)
    if prescription is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


@router.patch("/{prescription_id}", response_model=PrescriptionRead)
async def update_prescription(
    prescription_id: UUID,
    data: PrescriptionUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Prescription:
    prescription = await crud.update_prescription(session, prescription_id, data, user_id)
    if prescription is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


@router.delete("/{prescription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prescription(
    prescription_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_prescription(session, prescription_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prescription not found")

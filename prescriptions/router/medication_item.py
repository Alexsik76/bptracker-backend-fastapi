from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from db import SessionDep
from prescriptions import crud
from prescriptions.models import (
    MedicationItem,
    MedicationItemCreate,
    MedicationItemRead,
    MedicationItemUpdate,
)
from prescriptions.router.deps import CurrentUserId

# Nested under /prescriptions/{prescription_id}/items — a medication item
# has no independent existence outside its prescription.
router = APIRouter(prefix="/prescriptions/{prescription_id}/items")


@router.post("", response_model=MedicationItemRead, status_code=status.HTTP_201_CREATED)
async def create_medication_item(
    prescription_id: UUID,
    data: MedicationItemCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MedicationItem:
    item = await crud.create_medication_item(session, prescription_id, data, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return item


@router.get("", response_model=Sequence[MedicationItemRead])
async def list_medication_items(
    prescription_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Sequence[MedicationItem]:
    items = await crud.get_medication_items(session, prescription_id, user_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return items


@router.get("/{item_id}", response_model=MedicationItemRead)
async def get_medication_item(
    prescription_id: UUID,
    item_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MedicationItem:
    item = await crud.get_medication_item(session, prescription_id, item_id, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Medication item not found")
    return item


@router.patch("/{item_id}", response_model=MedicationItemRead)
async def update_medication_item(
    prescription_id: UUID,
    item_id: UUID,
    data: MedicationItemUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MedicationItem:
    item = await crud.update_medication_item(session, prescription_id, item_id, data, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Medication item not found")
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_medication_item(
    prescription_id: UUID,
    item_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_medication_item(session, prescription_id, item_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Medication item not found")

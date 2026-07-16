from collections.abc import Sequence
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from prescriptions.crud.prescription import get_prescription
from prescriptions.models import (
    CourseType,
    MedicationItem,
    MedicationItemCreate,
    MedicationItemUpdate,
)

# Ownership is checked via the parent prescription (medication_items has no user_id).


async def create_medication_item(
    session: AsyncSession,
    prescription_id: UUID,
    data: MedicationItemCreate,
    user_id: UUID,
) -> MedicationItem | None:
    """Add a medication item to a user's prescription. None if prescription not found."""
    prescription = await get_prescription(session, prescription_id, user_id)
    if prescription is None:
        return None
    item = MedicationItem.model_validate(data, update={"prescription_id": prescription_id})
    if item.course_type == CourseType.ONGOING:
        item.course_start = None
        item.course_intakes = None
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_medication_items(
    session: AsyncSession,
    prescription_id: UUID,
    user_id: UUID,
) -> Sequence[MedicationItem] | None:
    """Return all items of a user's prescription. None if prescription not found."""
    prescription = await get_prescription(session, prescription_id, user_id)
    if prescription is None:
        return None
    statement = select(MedicationItem).where(MedicationItem.prescription_id == prescription_id)
    result = await session.exec(statement)
    return result.all()


async def get_medication_item(
    session: AsyncSession,
    prescription_id: UUID,
    item_id: UUID,
    user_id: UUID,
) -> MedicationItem | None:
    """Return one medication item, scoped to the user's prescription."""
    prescription = await get_prescription(session, prescription_id, user_id)
    if prescription is None:
        return None
    statement = select(MedicationItem).where(
        MedicationItem.id == item_id,
        MedicationItem.prescription_id == prescription_id,
    )
    result = await session.exec(statement)
    return result.first()


async def update_medication_item(
    session: AsyncSession,
    prescription_id: UUID,
    item_id: UUID,
    data: MedicationItemUpdate,
    user_id: UUID,
) -> MedicationItem | None:
    """Apply partial changes to a medication item."""
    item = await get_medication_item(session, prescription_id, item_id, user_id)
    if item is None:
        return None
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(item, field, value)
    if item.course_type == CourseType.ONGOING:
        item.course_start = None
        item.course_intakes = None
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def delete_medication_item(
    session: AsyncSession,
    prescription_id: UUID,
    item_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a medication item. False if not found."""
    item = await get_medication_item(session, prescription_id, item_id, user_id)
    if item is None:
        return False
    await session.delete(item)
    await session.commit()
    return True

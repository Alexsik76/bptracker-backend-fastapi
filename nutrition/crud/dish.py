from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.menu import evaluate_active_menus_completeness
from nutrition.models import (
    Dish,
    DishComponent,
    DishComponentRead,
    DishCreate,
    DishKind,
    DishRead,
    DishUpdate,
)


def _to_dish_read(dish: Dish, components: Sequence[DishComponent]) -> DishRead:
    comp_reads = [DishComponentRead.model_validate(c) for c in components]
    return DishRead(
        id=dish.id,
        user_id=dish.user_id,
        name=dish.name,
        kind=dish.kind,
        cooked_weight_g=dish.cooked_weight_g,
        remaining_g=dish.remaining_g,
        cooked_on=dish.cooked_on,
        fixed_slot=dish.fixed_slot,
        notes=dish.notes,
        created_at=dish.created_at,
        updated_at=dish.updated_at,
        components=comp_reads,
    )


async def create_dish(
    session: AsyncSession,
    user_id: UUID,
    data: DishCreate,
) -> DishRead:
    """Create a new dish along with its components."""
    dish_data = data.model_dump(exclude={"components"})
    dish = Dish.model_validate(dish_data, update={"user_id": user_id})
    session.add(dish)
    await session.flush()
    await session.refresh(dish)

    components: list[DishComponent] = []
    for comp_in in data.components:
        comp = DishComponent.model_validate(comp_in, update={"dish_id": dish.id})
        session.add(comp)
        components.append(comp)

    await session.commit()
    await session.refresh(dish)
    for comp in components:
        await session.refresh(comp)

    return _to_dish_read(dish, components)


async def get_dishes(
    session: AsyncSession,
    user_id: UUID,
    kind: DishKind | None = None,
    available: bool | None = None,
) -> list[DishRead]:
    """Return dishes for a user, optionally filtered by kind and availability."""
    statement = select(Dish).where(Dish.user_id == user_id)
    if kind is not None:
        statement = statement.where(Dish.kind == kind)
    if available:
        statement = statement.where(
            (Dish.kind == DishKind.CONSTANT)
            | (
                (Dish.kind == DishKind.READY)
                & ((Dish.remaining_g.is_(None)) | (Dish.remaining_g > 0))
            )
        )
    statement = statement.order_by(col(Dish.name).asc())
    dishes = (await session.exec(statement)).all()

    if not dishes:
        return []

    dish_ids = [d.id for d in dishes]
    comp_stmt = select(DishComponent).where(col(DishComponent.dish_id).in_(dish_ids))
    comps = (await session.exec(comp_stmt)).all()

    comps_by_dish: dict[UUID | None, list[DishComponent]] = {d.id: [] for d in dishes}
    for comp in comps:
        comps_by_dish[comp.dish_id].append(comp)

    return [_to_dish_read(dish, comps_by_dish.get(dish.id, [])) for dish in dishes]


async def get_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> DishRead | None:
    """Return a single dish by id with components, scoped to the user."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    comps = (await session.exec(comp_stmt)).all()
    return _to_dish_read(dish, comps)


async def update_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
    data: DishUpdate,
) -> DishRead | None:
    """Update a dish and re-evaluate active menus if remaining_g changed."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    old_remaining = dish.remaining_g
    updates = data.model_dump(exclude_unset=True, exclude={"components"})
    for field, value in updates.items():
        setattr(dish, field, value)
    dish.updated_at = datetime.now(UTC)
    session.add(dish)

    if data.components is not None:
        del_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
        old_comps = (await session.exec(del_stmt)).all()
        for old in old_comps:
            await session.delete(old)

        for comp_in in data.components:
            comp = DishComponent.model_validate(comp_in, update={"dish_id": dish.id})
            session.add(comp)

    if "remaining_g" in updates and updates["remaining_g"] != old_remaining:
        await evaluate_active_menus_completeness(session, user_id)

    await session.commit()
    await session.refresh(dish)

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    current_comps = (await session.exec(comp_stmt)).all()
    return _to_dish_read(dish, current_comps)


async def delete_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's dish. Cascades to components; triggers completeness check on active menus."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return False
    await session.delete(dish)
    await session.flush()
    await evaluate_active_menus_completeness(session, user_id)
    await session.commit()
    return True

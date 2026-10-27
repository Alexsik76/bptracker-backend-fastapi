from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.menu import get_menu_read
from nutrition.crud.profile import get_nutrition_profile
from nutrition.models import (
    AvailablePinRead,
    Dish,
    KcalSource,
    Menu,
    MenuItem,
    MenuItemOrigin,
    MenuRead,
    PinnedMeal,
    PinnedMealItem,
    PinnedMealItemRead,
    PinnedMealRead,
    PinnedMealUpsert,
    Product,
)


async def get_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
) -> list[PinnedMealRead]:
    """Fetch all pinned meals for a user with their items ordered by position."""
    stmt = (
        select(PinnedMeal)
        .where(PinnedMeal.user_id == user_id)
        .order_by(col(PinnedMeal.created_at).asc(), col(PinnedMeal.slot).asc())
    )
    pins = list((await session.exec(stmt)).all())
    if not pins:
        return []

    pin_ids = [p.id for p in pins]
    items_stmt = (
        select(PinnedMealItem)
        .where(col(PinnedMealItem.pinned_meal_id).in_(pin_ids))
        .order_by(col(PinnedMealItem.position).asc())
    )
    all_items = list((await session.exec(items_stmt)).all())
    items_by_pin: dict[UUID, list[PinnedMealItemRead]] = {p.id: [] for p in pins}
    for item in all_items:
        items_by_pin[item.pinned_meal_id].append(
            PinnedMealItemRead(
                id=item.id,
                pinned_meal_id=item.pinned_meal_id,
                dish_id=item.dish_id,
                product_id=item.product_id,
                title=item.title,
                portion_g=item.portion_g,
                portion_text=item.portion_text,
                kcal_est=item.kcal_est,
                position=item.position,
            )
        )

    return [
        PinnedMealRead(
            id=p.id,
            user_id=p.user_id,
            slot=p.slot,
            active=p.active,
            created_at=p.created_at,
            updated_at=p.updated_at,
            items=items_by_pin.get(p.id, []),
        )
        for p in pins
    ]


async def get_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    slot: str,
) -> PinnedMeal | None:
    """Fetch PinnedMeal row by user_id and slot."""
    stmt = select(PinnedMeal).where(
        PinnedMeal.user_id == user_id,
        PinnedMeal.slot == slot,
    )
    return (await session.exec(stmt)).first()


async def get_pinned_meal_read(
    session: AsyncSession,
    user_id: UUID,
    slot: str,
) -> PinnedMealRead | None:
    """Fetch PinnedMealRead by user_id and slot."""
    pin = await get_pinned_meal(session, user_id, slot)
    if pin is None:
        return None

    items = await get_pinned_meal_items(session, pin.id)
    return PinnedMealRead(
        id=pin.id,
        user_id=pin.user_id,
        slot=pin.slot,
        active=pin.active,
        created_at=pin.created_at,
        updated_at=pin.updated_at,
        items=[
            PinnedMealItemRead(
                id=item.id,
                pinned_meal_id=item.pinned_meal_id,
                dish_id=item.dish_id,
                product_id=item.product_id,
                title=item.title,
                portion_g=item.portion_g,
                portion_text=item.portion_text,
                kcal_est=item.kcal_est,
                position=item.position,
            )
            for item in items
        ],
    )


async def get_pinned_meal_items(
    session: AsyncSession,
    pinned_meal_id: UUID,
) -> list[PinnedMealItem]:
    """Fetch items of a pinned meal ordered by position."""
    stmt = (
        select(PinnedMealItem)
        .where(PinnedMealItem.pinned_meal_id == pinned_meal_id)
        .order_by(col(PinnedMealItem.position).asc())
    )
    return list((await session.exec(stmt)).all())


async def get_active_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
) -> list[PinnedMeal]:
    """Fetch all active PinnedMeal rows for a user."""
    stmt = (
        select(PinnedMeal)
        .where(PinnedMeal.user_id == user_id, PinnedMeal.active.is_(True))
        .order_by(col(PinnedMeal.slot).asc())
    )
    return list((await session.exec(stmt)).all())


async def cleanup_unmarked_materialized_pinned_menus(
    session: AsyncSession,
    user_id: UUID,
    slot: str,
) -> None:
    """Delete materialized pinned rows for dates >= today that have no marked items.

    This ensures that when a pin is updated, deactivated, or deleted,
    the next read re-materializes from the current pin state.
    """
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.slot == slot,
        Menu.date >= dt_date.today(),
        Menu.pinned_meal_id.is_not(None),
    )
    menus = list((await session.exec(stmt)).all())
    for m in menus:
        items_stmt = select(MenuItem).where(MenuItem.menu_id == m.id)
        items = list((await session.exec(items_stmt)).all())
        has_marked = any(it.status is not None for it in items)
        if not has_marked:
            await session.delete(m)


async def upsert_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    slot: str,
    data: PinnedMealUpsert,
) -> PinnedMealRead:
    """Upsert pinned meal: update active flag, replace full items list, resync future rows."""
    pin = await get_pinned_meal(session, user_id, slot)
    if pin is not None:
        pin.active = data.active
        pin.updated_at = datetime.now(UTC)
        session.add(pin)
        await session.flush()

        # Delete existing items
        del_stmt = select(PinnedMealItem).where(
            PinnedMealItem.pinned_meal_id == pin.id
        )
        existing_items = list((await session.exec(del_stmt)).all())
        for it in existing_items:
            await session.delete(it)
    else:
        pin = PinnedMeal(
            user_id=user_id,
            slot=slot,
            active=data.active,
        )
        session.add(pin)
        await session.flush()
        await session.refresh(pin)

    # Insert new items
    for item in data.items:
        resolved_title = item.title
        if item.dish_id is not None and not resolved_title:
            dish = (
                await session.exec(
                    select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                )
            ).first()
            if dish is None:
                raise ValueError(f"Dish '{item.dish_id}' not found")
            resolved_title = dish.name
        elif item.product_id is not None and not resolved_title:
            product = (
                await session.exec(
                    select(Product).where(
                        Product.id == item.product_id, Product.user_id == user_id
                    )
                )
            ).first()
            if product is None:
                raise ValueError(f"Product '{item.product_id}' not found")
            resolved_title = product.name

        new_item = PinnedMealItem(
            pinned_meal_id=pin.id,
            dish_id=item.dish_id,
            product_id=item.product_id,
            title=resolved_title or "",
            portion_g=item.portion_g,
            portion_text=item.portion_text,
            kcal_est=item.kcal_est,
            position=item.position,
        )
        session.add(new_item)

    # Resync untouched future rows
    await cleanup_unmarked_materialized_pinned_menus(session, user_id, slot)

    await session.commit()
    await session.refresh(pin)

    return await get_pinned_meal_read(session, user_id, slot)  # type: ignore[return-value]


async def delete_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    slot: str,
) -> bool:
    """Delete a pinned meal by slot and cleanup untouched future materialized rows."""
    pin = await get_pinned_meal(session, user_id, slot)
    if pin is None:
        return False

    await session.delete(pin)
    await cleanup_unmarked_materialized_pinned_menus(session, user_id, slot)
    await session.commit()
    return True


async def get_available_pins(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> list[AvailablePinRead]:
    """Return active pins whose slot on that date is not currently pinned."""
    active_pins = await get_active_pinned_meals(session, user_id)
    if not active_pins:
        return []

    m_stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
    )
    date_menus = list((await session.exec(m_stmt)).all())
    currently_pinned_slots = {
        m.slot for m in date_menus if not m.removed and m.pinned_meal_id is not None
    }

    return [
        AvailablePinRead(slot=pin.slot, pinned_meal_id=pin.id)
        for pin in active_pins
        if pin.slot not in currently_pinned_slots
    ]


async def restore_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    slot: str,
) -> MenuRead:
    """Restore active pin into the day meal slot.

    Revives row if removed, deletes unmarked items, copies pin items,
    sets pinned_meal_id, pin_dismissed=False; eaten/skipped items stay.
    """
    pin = await get_pinned_meal(session, user_id, slot)
    if pin is None or not pin.active:
        raise KeyError(f"Active pin for slot '{slot}' not found")

    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.slot == slot,
    )
    menu = (await session.exec(stmt)).first()

    existing_items: list[MenuItem] = []
    if menu is None:
        profile = await get_nutrition_profile(session, user_id)
        meal_time: str | None = None
        if profile and profile.meals:
            for m in profile.meals:
                if m.name == slot:
                    meal_time = m.time
                    break

        menu = Menu(
            user_id=user_id,
            date=menu_date,
            slot=slot,
            time=meal_time,
            pinned_meal_id=pin.id,
            pin_dismissed=False,
            removed=False,
        )
        session.add(menu)
        await session.flush()
        await session.refresh(menu)
    else:
        menu.removed = False
        menu.pinned_meal_id = pin.id
        menu.pin_dismissed = False
        if menu.time is None:
            profile = await get_nutrition_profile(session, user_id)
            if profile and profile.meals:
                for m in profile.meals:
                    if m.name == slot and m.time:
                        menu.time = m.time
                        break
        menu.updated_at = datetime.now(UTC)
        session.add(menu)

        items_stmt = (
            select(MenuItem)
            .where(MenuItem.menu_id == menu.id)
            .order_by(col(MenuItem.position).asc())
        )
        existing_items = list((await session.exec(items_stmt)).all())
        for it in existing_items:
            if it.status is None:
                await session.delete(it)

    pin_items = await get_pinned_meal_items(session, pin.id)
    marked_items = [it for it in existing_items if it.status is not None]
    max_marked_pos = max((it.position for it in marked_items), default=0)

    for idx, it in enumerate(pin_items, start=1):
        menu_item = MenuItem(
            menu_id=menu.id,
            dish_id=it.dish_id,
            product_id=it.product_id,
            title=it.title,
            portion_g=it.portion_g,
            portion_text=it.portion_text,
            kcal_est=it.kcal_est,
            kcal_source=KcalSource.USER if it.kcal_est is not None else None,
            origin=MenuItemOrigin.MANUAL,
            position=max_marked_pos + idx if marked_items else it.position,
            status=None,
            eaten_at=None,
        )
        session.add(menu_item)

    await session.commit()
    return await get_menu_read(session, menu.id, user_id)  # type: ignore[return-value]

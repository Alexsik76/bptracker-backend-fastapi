from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.menu import (
    EatenMealImmutableError,
    get_menu_read,
)
from nutrition.crud.profile import get_nutrition_profile
from nutrition.models import (
    AvailablePinRead,
    Dish,
    MealType,
    Menu,
    MenuItem,
    MenuItemOrigin,
    MenuRead,
    NutritionSource,
    PinnedMeal,
    PinnedMealItem,
    PinnedMealItemRead,
    PinnedMealRead,
    PinnedMealUpsert,
)


async def get_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
) -> list[PinnedMealRead]:
    """Fetch all pinned meals for a user with their items ordered by position."""
    stmt = (
        select(PinnedMeal)
        .where(PinnedMeal.user_id == user_id)
        .order_by(col(PinnedMeal.created_at).asc(), col(PinnedMeal.meal_type).asc())
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
    all_items = (await session.exec(items_stmt)).all()

    items_by_pin: dict[UUID, list[PinnedMealItemRead]] = {p.id: [] for p in pins}
    for item in all_items:
        items_by_pin[item.pinned_meal_id].append(PinnedMealItemRead.model_validate(item))

    return [
        PinnedMealRead(
            id=p.id,
            user_id=p.user_id,
            meal_type=p.meal_type,
            active=p.active,
            created_at=p.created_at,
            updated_at=p.updated_at,
            items=items_by_pin.get(p.id, []),
        )
        for p in pins
    ]


async def get_pinned_meal_read(
    session: AsyncSession,
    user_id: UUID,
    meal_type: MealType,
) -> PinnedMealRead | None:
    """Fetch a pinned meal by meal type for a user."""
    stmt = select(PinnedMeal).where(
        PinnedMeal.user_id == user_id,
        PinnedMeal.meal_type == meal_type,
    )
    pin = (await session.exec(stmt)).first()
    if pin is None:
        return None

    items_stmt = (
        select(PinnedMealItem)
        .where(PinnedMealItem.pinned_meal_id == pin.id)
        .order_by(col(PinnedMealItem.position).asc())
    )
    items = list((await session.exec(items_stmt)).all())

    return PinnedMealRead(
        id=pin.id,
        user_id=pin.user_id,
        meal_type=pin.meal_type,
        active=pin.active,
        created_at=pin.created_at,
        updated_at=pin.updated_at,
        items=[PinnedMealItemRead.model_validate(item) for item in items],
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


async def get_pinned_meal_entity(
    session: AsyncSession,
    user_id: UUID,
    meal_type: MealType,
) -> PinnedMeal | None:
    stmt = select(PinnedMeal).where(
        PinnedMeal.user_id == user_id,
        PinnedMeal.meal_type == meal_type,
    )
    return (await session.exec(stmt)).first()


async def cleanup_unmarked_materialized_pinned_menus(
    session: AsyncSession,
    user_id: UUID,
    meal_type: MealType,
) -> None:
    """Delete materialized pinned rows for dates >= today that are not eaten.

    A row is eligible for cleanup if:
      - date >= today
      - meal_type == meal_type
      - eaten_at IS NULL
      - pinned_meal_id is set
      - user has not modified items (origin is MANUAL for all and all items match pin)
    """
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date >= dt_date.today(),
        Menu.meal_type == meal_type,
        Menu.eaten_at.is_(None),
        Menu.pinned_meal_id.is_not(None),
        Menu.removed.is_(False),
    )
    menus = list((await session.exec(stmt)).all())
    for menu in menus:
        items_stmt = select(MenuItem).where(MenuItem.menu_id == menu.id)
        items = list((await session.exec(items_stmt)).all())
        for it in items:
            await session.delete(it)
        await session.delete(menu)


async def upsert_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    meal_type: MealType,
    data: PinnedMealUpsert,
) -> PinnedMealRead:
    """Create or update a pinned meal for a specific meal type."""
    stmt = select(PinnedMeal).where(
        PinnedMeal.user_id == user_id,
        PinnedMeal.meal_type == meal_type,
    )
    pin = (await session.exec(stmt)).first()

    if pin is None:
        pin = PinnedMeal(
            user_id=user_id,
            meal_type=meal_type,
            active=data.active,
        )
        session.add(pin)
        await session.flush()
        await session.refresh(pin)
    else:
        pin.active = data.active
        pin.updated_at = datetime.now(UTC)
        session.add(pin)

        del_stmt = select(PinnedMealItem).where(PinnedMealItem.pinned_meal_id == pin.id)
        for old_item in (await session.exec(del_stmt)).all():
            await session.delete(old_item)

    for item in data.items:
        resolved_title = item.title
        if item.dish_id is not None:
            dish = (
                await session.exec(
                    select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                )
            ).first()
            if dish is None:
                raise ValueError(f"Dish '{item.dish_id}' not found")
            resolved_title = dish.name

        new_item = PinnedMealItem(
            pinned_meal_id=pin.id,
            dish_id=item.dish_id,
            title=resolved_title or "",
            portion_g=item.portion_g,
            portion_text=item.portion_text,
            position=item.position,
            kcal=item.kcal,
            protein_g=item.protein_g,
            fat_g=item.fat_g,
            carbs_g=item.carbs_g,
            fiber_g=item.fiber_g,
            sugar_g=item.sugar_g,
            sodium_mg=item.sodium_mg,
        )
        session.add(new_item)

    # Resync untouched future rows
    await cleanup_unmarked_materialized_pinned_menus(session, user_id, meal_type)

    await session.commit()
    await session.refresh(pin)

    return await get_pinned_meal_read(session, user_id, meal_type)  # type: ignore[return-value]


async def delete_pinned_meal(
    session: AsyncSession,
    user_id: UUID,
    meal_type: MealType,
) -> bool:
    """Delete a pinned meal configuration and its materialized rows."""
    stmt = select(PinnedMeal).where(
        PinnedMeal.user_id == user_id,
        PinnedMeal.meal_type == meal_type,
    )
    pin = (await session.exec(stmt)).first()
    if pin is None:
        return False

    await cleanup_unmarked_materialized_pinned_menus(session, user_id, meal_type)
    await session.delete(pin)
    await session.commit()
    return True


async def get_available_pins(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> list[AvailablePinRead]:
    """Return active pins whose meal_type on that date is not currently pinned."""
    active_pins = await get_active_pinned_meals(session, user_id)
    if not active_pins:
        return []

    m_stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
    )
    date_menus = list((await session.exec(m_stmt)).all())
    currently_pinned_types = {
        m.meal_type for m in date_menus if not m.removed and m.pinned_meal_id is not None
    }

    return [
        AvailablePinRead(meal_type=pin.meal_type, pinned_meal_id=pin.id)
        for pin in active_pins
        if pin.meal_type not in currently_pinned_types
    ]


async def restore_pinned_meal_to_day(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
) -> MenuRead:
    """Explicitly restore a pinned meal to a meal slot on the canvas."""
    pin = await get_pinned_meal_entity(session, user_id, meal_type)
    if pin is None or not pin.active:
        raise ValueError(f"No active pinned meal found for '{meal_type.value}'")

    profile = await get_nutrition_profile(session, user_id)
    profile_time: str | None = None
    if profile and profile.meals:
        for m in profile.meals:
            if m.type == meal_type:
                profile_time = m.time
                break

    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.meal_type == meal_type,
    )
    menu = (await session.exec(stmt)).first()

    if menu is not None:
        if menu.eaten_at is not None:
            raise EatenMealImmutableError(
                f"Cannot restore pin to meal '{meal_type.value}' because it is marked as eaten"
            )
        if menu.removed:
            menu.removed = False
        menu.pinned_meal_id = pin.id
        menu.pin_dismissed = False
        if menu.time is None and profile_time:
            menu.time = profile_time
        menu.updated_at = datetime.now(UTC)
        session.add(menu)

        items_stmt = select(MenuItem).where(MenuItem.menu_id == menu.id)
        existing_items = list((await session.exec(items_stmt)).all())
        for it in existing_items:
            await session.delete(it)
    else:
        menu = Menu(
            user_id=user_id,
            date=menu_date,
            meal_type=meal_type,
            time=profile_time,
            pinned_meal_id=pin.id,
            pin_dismissed=False,
            removed=False,
        )
        session.add(menu)
        await session.flush()
        await session.refresh(menu)

    pin_items = await get_pinned_meal_items(session, pin.id)
    for it in pin_items:
        has_nutr = it.kcal is not None
        menu_item = MenuItem(
            menu_id=menu.id,
            dish_id=it.dish_id,
            title=it.title,
            portion_g=it.portion_g,
            portion_text=it.portion_text,
            kcal=it.kcal,
            protein_g=it.protein_g,
            fat_g=it.fat_g,
            carbs_g=it.carbs_g,
            fiber_g=it.fiber_g,
            sugar_g=it.sugar_g,
            sodium_mg=it.sodium_mg,
            nutrition_source=NutritionSource.USER if has_nutr else None,
            origin=MenuItemOrigin.MANUAL,
            position=it.position,
        )
        session.add(menu_item)

    await session.commit()
    return await get_menu_read(session, menu.id, user_id)  # type: ignore[return-value]


# Backward compatibility aliases
get_pinned_meal = get_pinned_meal_read
restore_pinned_meal = restore_pinned_meal_to_day


async def get_active_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
) -> list[PinnedMealRead]:
    pins = await get_pinned_meals(session, user_id)
    return [p for p in pins if p.active]


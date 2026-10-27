from datetime import date as dt_date
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition import crud
from nutrition.models import KcalSource, Menu, MenuItem, MenuItemOrigin


async def materialize_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> bool:
    """Materialize active pinned meals on day menu read for date >= today.

    For every active pin whose slot is on the canvas and has no Menu row for that date
    (neither active nor removed), creates the Menu row (time from profile) with copies
    of the pin items (origin manual, kcal_source user when kcal given) and pinned_meal_id set,
    then commits.
    """
    if menu_date < dt_date.today():
        return False

    profile = await crud.get_nutrition_profile(session, user_id)
    if not profile or not profile.meals:
        return False

    active_pins = await crud.get_active_pinned_meals(session, user_id)
    if not active_pins:
        return False

    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    existing_menus = list((await session.exec(stmt)).all())
    existing_slots = {m.slot for m in existing_menus}

    profile_meals_map = {m.name: m for m in profile.meals}

    materialized_any = False
    for pin in active_pins:
        if pin.slot in profile_meals_map and pin.slot not in existing_slots:
            profile_meal = profile_meals_map[pin.slot]
            menu = Menu(
                user_id=user_id,
                date=menu_date,
                slot=pin.slot,
                time=profile_meal.time,
                pinned_meal_id=pin.id,
                pin_dismissed=False,
                removed=False,
            )
            session.add(menu)
            await session.flush()
            await session.refresh(menu)

            pin_items = await crud.get_pinned_meal_items(session, pin.id)
            for it in pin_items:
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
                    position=it.position,
                    status=None,
                    eaten_at=None,
                )
                session.add(menu_item)
            materialized_any = True
            existing_slots.add(pin.slot)

    if materialized_any:
        await session.commit()

    return materialized_any

from datetime import date as dt_date
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition import crud
from nutrition.models import Menu, MenuItem, MenuItemOrigin, NutritionSource


async def materialize_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    *,
    commit: bool = True,
) -> bool:
    """Materialize active pinned meals into the day's menus.

    For every active pin whose slot is in the profile schedule:
    - If a menu row already exists (and is not eaten, removed, or pin_dismissed),
      attaches pinned_meal_id.
    - If no menu row exists, creates the menu row.
    - Populates the menu with copies of pin items.
    """
    profile = await crud.get_nutrition_profile(session, user_id)
    if not profile or not profile.meals:
        return False

    active_pins = await crud.get_active_pinned_meals(session, user_id)
    if not active_pins:
        return False

    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    existing_menus = list((await session.exec(stmt)).all())
    existing_by_type = {m.meal_type: m for m in existing_menus}
    profile_meals_map = {m.type: m for m in profile.meals}

    materialized_any = False
    for pin in active_pins:
        if pin.meal_type in profile_meals_map:
            menu = existing_by_type.get(pin.meal_type)
            if menu is None:
                profile_meal = profile_meals_map[pin.meal_type]
                menu = Menu(
                    user_id=user_id,
                    date=menu_date,
                    meal_type=pin.meal_type,
                    time=profile_meal.time,
                    pinned_meal_id=pin.id,
                    pin_dismissed=False,
                    removed=False,
                )
                session.add(menu)
                await session.flush()
                await session.refresh(menu)
                existing_by_type[pin.meal_type] = menu
            elif (
                menu.pinned_meal_id is None
                and not menu.removed
                and menu.eaten_at is None
                and not menu.pin_dismissed
            ):
                menu.pinned_meal_id = pin.id
                session.add(menu)
                await session.flush()
            else:
                continue

            pin_items = await crud.get_pinned_meal_items(session, pin.id)
            for it in pin_items:
                has_user_nutr = it.kcal is not None
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
                    nutrition_source=NutritionSource.USER if has_user_nutr else None,
                    origin=MenuItemOrigin.MANUAL,
                    position=it.position,
                )
                session.add(menu_item)
            materialized_any = True

    if materialized_any:
        await session.flush()
        if commit:
            await session.commit()

    return materialized_any

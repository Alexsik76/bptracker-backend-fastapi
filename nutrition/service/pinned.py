from datetime import date as dt_date
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition import crud
from nutrition.models import Menu, MenuItem, MenuItemOrigin, NutritionSource, StockWarning


async def materialize_pinned_meals(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    *,
    commit: bool = True,
) -> tuple[bool, list[StockWarning]]:
    """Materialize active pinned meals into the day's menus.

    For each item, cook the frequent dish with cooked_on = menu date (stock rule)
    and add a menu item referencing the new dish with portion_g = cooked_weight_g.
    Returns (materialized_any, collected_stock_warnings).
    """
    profile = await crud.get_nutrition_profile(session, user_id)
    if not profile or not profile.meals:
        return False, []

    active_pins = await crud.get_active_pinned_meals(session, user_id)
    if not active_pins:
        return False, []

    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    existing_menus = list((await session.exec(stmt)).all())
    existing_by_type = {m.meal_type: m for m in existing_menus}
    profile_meals_map = {m.type: m for m in profile.meals}

    materialized_any = False
    all_warnings: list[StockWarning] = []

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
                dish_read = await crud.cook_frequent_dish(
                    session=session,
                    frequent_dish_id=it.frequent_dish_id,
                    user_id=user_id,
                    cooked_on=menu_date,
                )
                all_warnings.extend(dish_read.stock_warnings)

                portion = dish_read.cooked_weight_g
                ratio = portion / 100.0
                menu_item = MenuItem(
                    menu_id=menu.id,
                    dish_id=dish_read.id,
                    title=dish_read.name,
                    portion_g=portion,
                    portion_text=f"{portion} г",
                    kcal=round(dish_read.kcal * ratio, 1) if dish_read.kcal is not None else None,
                    protein_g=(
                        round(dish_read.protein_g * ratio, 1)
                        if dish_read.protein_g is not None
                        else None
                    ),
                    fat_g=(
                        round(dish_read.fat_g * ratio, 1)
                        if dish_read.fat_g is not None
                        else None
                    ),
                    carbs_g=(
                        round(dish_read.carbs_g * ratio, 1)
                        if dish_read.carbs_g is not None
                        else None
                    ),
                    fiber_g=(
                        round(dish_read.fiber_g * ratio, 1)
                        if dish_read.fiber_g is not None
                        else None
                    ),
                    sugar_g=(
                        round(dish_read.sugar_g * ratio, 1)
                        if dish_read.sugar_g is not None
                        else None
                    ),
                    sodium_mg=(
                        round(dish_read.sodium_mg * ratio, 1)
                        if dish_read.sodium_mg is not None
                        else None
                    ),
                    nutrition_source=(
                        NutritionSource.COMPUTED if dish_read.kcal is not None else None
                    ),
                    origin=MenuItemOrigin.MANUAL,
                    position=it.position,
                )
                session.add(menu_item)
            materialized_any = True

    if materialized_any:
        await session.flush()
        if commit:
            await session.commit()

    return materialized_any, all_warnings

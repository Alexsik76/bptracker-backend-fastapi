from datetime import UTC, date, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.dish import get_dish, get_dish_components
from nutrition.crud.evaluation import get_meal_evaluations_for_menus
from nutrition.models import (
    Dish,
    GenerationLog,
    MealEvaluationRead,
    Menu,
    MenuCreate,
    MenuItem,
    MenuItemCreate,
    MenuItemOrigin,
    MenuItemRead,
    MenuItemUpdate,
    MenuOrigin,
    MenuRead,
    MenuState,
    MenuUpdate,
    NutritionSource,
    PinnedDishDay,
)


def to_menu_item_read(item: MenuItem) -> MenuItemRead:
    return MenuItemRead(
        id=item.id,
        menu_id=item.menu_id,
        dish_id=item.dish_id,
        title=item.title,
        portion_g=item.portion_g,
        portion_text=item.portion_text,
        nutrition_source=item.nutrition_source,
        origin=item.origin,
        position=item.position,
        kcal=item.kcal,
        protein_g=item.protein_g,
        fat_g=item.fat_g,
        carbs_g=item.carbs_g,
        fiber_g=item.fiber_g,
        sugar_g=item.sugar_g,
        sodium_mg=item.sodium_mg,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def to_menu_read(
    menu: Menu,
    items: list[MenuItemRead] | None = None,
    evaluations: list[MealEvaluationRead] | None = None,
) -> MenuRead:
    return MenuRead(
        id=menu.id,
        user_id=menu.user_id,
        date=menu.date,
        meal_type=menu.meal_type,
        origin=menu.origin,
        state=menu.state,
        eaten_at=menu.eaten_at,
        pinned_dish_id=menu.pinned_dish_id,
        created_at=menu.created_at,
        updated_at=menu.updated_at,
        items=items or [],
        evaluations=evaluations or [],
    )


async def get_menu_entity(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
) -> Menu | None:
    stmt = select(Menu).where(Menu.id == menu_id, Menu.user_id == user_id)
    return (await session.exec(stmt)).first()


async def get_menu_items_for_menu(
    session: AsyncSession,
    menu_id: UUID,
) -> list[MenuItemRead]:
    stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu_id)
        .order_by(MenuItem.position, MenuItem.created_at)
    )
    items = (await session.exec(stmt)).all()
    return [to_menu_item_read(it) for it in items]


async def get_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
) -> MenuRead | None:
    menu = await get_menu_entity(session, menu_id, user_id)
    if menu is None:
        return None
    items = await get_menu_items_for_menu(session, menu.id)
    evals_by_menu = await get_meal_evaluations_for_menus(session, user_id, [menu.id])
    return to_menu_read(menu, items, evaluations=evals_by_menu.get(menu.id, []))


async def list_meals(
    session: AsyncSession,
    user_id: UUID,
    target_date: date,
) -> list[MenuRead]:
    """List meals for a date: by Menu.date for eaten meals, plus the current suggestion."""
    stmt = (
        select(Menu)
        .where(
            Menu.user_id == user_id,
            sa.or_(
                sa.and_(
                    Menu.state == MenuState.EATEN,
                    Menu.date == target_date,
                ),
                Menu.state == MenuState.SUGGESTION,
            ),
        )
        .order_by(Menu.eaten_at.asc().nulls_last(), Menu.created_at.asc())
    )
    menus = list((await session.exec(stmt)).all())
    meal_ids = [m.id for m in menus if m.id is not None]
    evals_by_menu = await get_meal_evaluations_for_menus(session, user_id, meal_ids)

    result: list[MenuRead] = []
    for m in menus:
        items = await get_menu_items_for_menu(session, m.id)
        result.append(to_menu_read(m, items, evaluations=evals_by_menu.get(m.id, [])))
    return result


async def create_menu(
    session: AsyncSession,
    user_id: UUID,
    data: MenuCreate,
) -> MenuRead:
    eaten_at = data.eaten_at
    if data.state == MenuState.EATEN and eaten_at is None:
        eaten_at = datetime.now(UTC)

    menu = Menu(
        user_id=user_id,
        date=data.date,
        meal_type=data.meal_type,
        origin=data.origin,
        state=data.state,
        eaten_at=eaten_at,
        pinned_dish_id=data.pinned_dish_id,
    )
    session.add(menu)
    await session.commit()
    await session.refresh(menu)

    created_items: list[MenuItemRead] = []
    for it_data in data.items:
        it = await add_menu_item(
            session=session,
            user_id=user_id,
            menu_id=menu.id,
            data=it_data,
        )
        created_items.append(it)

    return to_menu_read(menu, created_items)


async def eat_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
    target_date: date,
    eaten_at: datetime,
) -> MenuRead | None:
    menu = await get_menu_entity(session, menu_id, user_id)
    if menu is None:
        return None
    menu.state = MenuState.EATEN
    menu.date = target_date
    menu.eaten_at = eaten_at
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()
    await session.refresh(menu)
    items = await get_menu_items_for_menu(session, menu.id)
    return to_menu_read(menu, items)


async def materialize_pinned_dishes(
    session: AsyncSession,
    user_id: UUID,
    target_date: date,
) -> list[MenuRead]:
    stmt_dishes = (
        select(Dish)
        .where(
            Dish.user_id == user_id,
            Dish.pinned_meal_type.is_not(None),
        )
        .order_by(Dish.name.asc())
    )
    pinned_dishes = list((await session.exec(stmt_dishes)).all())
    if not pinned_dishes:
        return []

    stmt_markers = select(PinnedDishDay.dish_id).where(
        PinnedDishDay.user_id == user_id,
        PinnedDishDay.date == target_date,
    )
    marked_dish_ids = set((await session.exec(stmt_markers)).all())

    unmaterialized = [d for d in pinned_dishes if d.id not in marked_dish_ids]
    if not unmaterialized:
        return []

    materialized_menus: list[MenuRead] = []
    for dish in unmaterialized:
        comps = await get_dish_components(session, dish.id)
        comp_weight = sum(c.weight_g for c in comps)
        if dish.cooked_weight_g is not None:
            portion_g = dish.cooked_weight_g
        elif comp_weight > 0:
            portion_g = comp_weight
        else:
            portion_g = 100

        if dish.kcal is not None:
            ratio = portion_g / 100.0
            kcal = round(dish.kcal * ratio, 1)
            protein_g = round(dish.protein_g * ratio, 1) if dish.protein_g is not None else None
            fat_g = round(dish.fat_g * ratio, 1) if dish.fat_g is not None else None
            carbs_g = round(dish.carbs_g * ratio, 1) if dish.carbs_g is not None else None
            fiber_g = round(dish.fiber_g * ratio, 1) if dish.fiber_g is not None else None
            sugar_g = round(dish.sugar_g * ratio, 1) if dish.sugar_g is not None else None
            sodium_mg = round(dish.sodium_mg * ratio, 1) if dish.sodium_mg is not None else None
            source = NutritionSource.COMPUTED
        else:
            kcal, protein_g, fat_g, carbs_g, fiber_g, sugar_g, sodium_mg = (
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            source = None

        assert dish.pinned_meal_type is not None
        menu = Menu(
            user_id=user_id,
            date=target_date,
            meal_type=dish.pinned_meal_type,
            origin=MenuOrigin.PINNED,
            state=MenuState.EATEN,
            eaten_at=None,
            pinned_dish_id=dish.id,
        )
        session.add(menu)
        await session.flush()

        item = MenuItem(
            menu_id=menu.id,
            dish_id=dish.id,
            title=dish.name,
            portion_g=portion_g,
            portion_text=f"{portion_g} г",
            nutrition_source=source,
            origin=MenuItemOrigin.MANUAL,
            position=0,
            kcal=kcal,
            protein_g=protein_g,
            fat_g=fat_g,
            carbs_g=carbs_g,
            fiber_g=fiber_g,
            sugar_g=sugar_g,
            sodium_mg=sodium_mg,
        )
        session.add(item)

        marker = PinnedDishDay(
            user_id=user_id,
            dish_id=dish.id,
            date=target_date,
        )
        session.add(marker)
        await session.flush()

        materialized_menus.append(to_menu_read(menu, [to_menu_item_read(item)]))

    await session.commit()
    return materialized_menus


async def get_journal_data(
    session: AsyncSession,
    user_id: UUID,
    target_date: date,
) -> tuple[list[MenuRead], MenuRead | None, float, float]:
    stmt_eaten = (
        select(Menu)
        .where(
            Menu.user_id == user_id,
            Menu.date == target_date,
            Menu.state == MenuState.EATEN,
        )
        .order_by(Menu.eaten_at.asc().nulls_last(), Menu.created_at.asc())
    )
    eaten_menus = list((await session.exec(stmt_eaten)).all())
    meal_ids = [m.id for m in eaten_menus if m.id is not None]
    evals_by_menu = await get_meal_evaluations_for_menus(session, user_id, meal_ids)

    meals_read: list[MenuRead] = []
    total_kcal = 0.0
    total_protein_g = 0.0
    for m in eaten_menus:
        items = await get_menu_items_for_menu(session, m.id)
        evals = evals_by_menu.get(m.id, [])
        meals_read.append(to_menu_read(m, items, evaluations=evals))
        for it in items:
            if it.kcal is not None:
                total_kcal += it.kcal
            if it.protein_g is not None:
                total_protein_g += it.protein_g

    stmt_sugg = (
        select(Menu)
        .where(
            Menu.user_id == user_id,
            Menu.state == MenuState.SUGGESTION,
        )
        .order_by(Menu.created_at.desc())
    )
    sugg_menu = (await session.exec(stmt_sugg)).first()
    sugg_read = None
    if sugg_menu:
        sugg_items = await get_menu_items_for_menu(session, sugg_menu.id)
        sugg_read = to_menu_read(sugg_menu, sugg_items)

    return meals_read, sugg_read, round(total_kcal, 1), round(total_protein_g, 1)


async def update_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
    data: MenuUpdate,
) -> MenuRead | None:
    menu = await get_menu_entity(session, menu_id, user_id)
    if menu is None:
        return None

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(menu, field, value)

    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()
    await session.refresh(menu)

    items = await get_menu_items_for_menu(session, menu.id)
    return to_menu_read(menu, items)


async def delete_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
) -> bool:
    menu = await get_menu_entity(session, menu_id, user_id)
    if menu is None:
        return False
    await session.delete(menu)
    await session.commit()
    return True


async def get_menu_item_with_menu(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
) -> tuple[MenuItem, Menu] | None:
    stmt = (
        select(MenuItem, Menu)
        .join(Menu, MenuItem.menu_id == Menu.id)
        .where(MenuItem.id == item_id, Menu.user_id == user_id)
    )
    return (await session.exec(stmt)).first()


async def add_menu_item(
    session: AsyncSession,
    user_id: UUID,
    menu_id: UUID,
    data: MenuItemCreate,
) -> MenuItemRead:
    dish = None
    if data.dish_id is not None:
        dish = await get_dish(session, data.dish_id, user_id)

    title = data.title or (dish.name if dish else "")
    portion_g = data.portion_g
    portion_text = data.portion_text or (f"{portion_g} г" if portion_g else None)

    # Compute snapshot if dish is linked and no explicit nutrition provided
    user_provided = any(
        getattr(data, f) is not None
        for f in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")
    )

    kcal = data.kcal
    protein_g = data.protein_g
    fat_g = data.fat_g
    carbs_g = data.carbs_g
    fiber_g = data.fiber_g
    sugar_g = data.sugar_g
    sodium_mg = data.sodium_mg
    nutrition_source = data.nutrition_source

    if not user_provided and dish is not None and dish.kcal is not None:
        ratio = (portion_g / 100.0) if portion_g is not None else 1.0
        kcal = round(dish.kcal * ratio, 1)
        protein_g = round(dish.protein_g * ratio, 1) if dish.protein_g is not None else None
        fat_g = round(dish.fat_g * ratio, 1) if dish.fat_g is not None else None
        carbs_g = round(dish.carbs_g * ratio, 1) if dish.carbs_g is not None else None
        fiber_g = round(dish.fiber_g * ratio, 1) if dish.fiber_g is not None else None
        sugar_g = round(dish.sugar_g * ratio, 1) if dish.sugar_g is not None else None
        sodium_mg = round(dish.sodium_mg * ratio, 1) if dish.sodium_mg is not None else None
        nutrition_source = NutritionSource.COMPUTED
    elif user_provided and nutrition_source is None:
        nutrition_source = NutritionSource.USER

    item = MenuItem(
        menu_id=menu_id,
        dish_id=data.dish_id,
        title=title,
        portion_g=portion_g,
        portion_text=portion_text,
        nutrition_source=nutrition_source,
        origin=data.origin,
        position=data.position,
        kcal=kcal,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        fiber_g=fiber_g,
        sugar_g=sugar_g,
        sodium_mg=sodium_mg,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return to_menu_item_read(item)


async def update_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
    data: MenuItemUpdate,
) -> MenuItemRead | None:
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return None
    item, menu = row

    old_portion_g = item.portion_g
    updates = data.model_dump(exclude_unset=True)

    portion_changed = "portion_g" in updates and updates["portion_g"] != old_portion_g

    for field, value in updates.items():
        setattr(item, field, value)

    # Recompute snapshot when portion_g changes and nutrition wasn't explicitly set
    nutr_keys = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")
    user_passed_nutrition = any(f in updates for f in nutr_keys)

    if portion_changed and not user_passed_nutrition and item.portion_g is not None:
        item.portion_text = f"{item.portion_g} г"
        if item.dish_id is not None:
            dish = await get_dish(session, item.dish_id, user_id)
            if dish and dish.kcal is not None:
                ratio = item.portion_g / 100.0
                item.kcal = round(dish.kcal * ratio, 1)
                item.protein_g = (
                    round(dish.protein_g * ratio, 1) if dish.protein_g is not None else None
                )
                item.fat_g = round(dish.fat_g * ratio, 1) if dish.fat_g is not None else None
                item.carbs_g = (
                    round(dish.carbs_g * ratio, 1) if dish.carbs_g is not None else None
                )
                item.fiber_g = (
                    round(dish.fiber_g * ratio, 1) if dish.fiber_g is not None else None
                )
                item.sugar_g = (
                    round(dish.sugar_g * ratio, 1) if dish.sugar_g is not None else None
                )
                item.sodium_mg = (
                    round(dish.sodium_mg * ratio, 1) if dish.sodium_mg is not None else None
                )
                item.nutrition_source = NutritionSource.COMPUTED
        elif old_portion_g and old_portion_g > 0 and item.kcal is not None:
            ratio = item.portion_g / old_portion_g
            item.kcal = round(item.kcal * ratio, 1)
            item.protein_g = (
                round(item.protein_g * ratio, 1) if item.protein_g is not None else None
            )
            item.fat_g = round(item.fat_g * ratio, 1) if item.fat_g is not None else None
            item.carbs_g = round(item.carbs_g * ratio, 1) if item.carbs_g is not None else None
            item.fiber_g = round(item.fiber_g * ratio, 1) if item.fiber_g is not None else None
            item.sugar_g = round(item.sugar_g * ratio, 1) if item.sugar_g is not None else None
            item.sodium_mg = (
                round(item.sodium_mg * ratio, 1) if item.sodium_mg is not None else None
            )
            item.nutrition_source = NutritionSource.COMPUTED

    item.updated_at = datetime.now(UTC)
    menu.updated_at = datetime.now(UTC)
    session.add(item)
    session.add(menu)
    await session.commit()
    await session.refresh(item)
    return to_menu_item_read(item)


async def delete_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
) -> bool:
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return False
    item, menu = row
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.delete(item)
    await session.commit()
    return True


async def add_generation_log(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    scope: str,
    model: str,
    prompt_version: str,
    prompt: str | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    raw_response: str | None = None,
    menu_id: UUID | None = None,
) -> GenerationLog:
    log_entry = GenerationLog(
        user_id=user_id,
        menu_id=menu_id,
        date=menu_date,
        scope=scope,
        model=model,
        prompt_version=prompt_version,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=raw_response,
    )
    session.add(log_entry)
    await session.commit()
    await session.refresh(log_entry)
    return log_entry

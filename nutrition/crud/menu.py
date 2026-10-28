from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.dish import get_dish, get_dishes
from nutrition.crud.product import get_product_entity, get_product_reserves_map
from nutrition.crud.profile import get_nutrition_profile
from nutrition.models import (
    Dish,
    DishKind,
    DishRead,
    GenerationLog,
    MealType,
    Menu,
    MenuDay,
    MenuItem,
    MenuItemCreate,
    MenuItemOrigin,
    MenuItemRead,
    MenuItemUpdate,
    MenuRead,
    MenuStatus,
    MenuUpdate,
    NutritionSource,
    Product,
)


class MealSlotRemovedError(Exception):
    """Raised when an operation targets a meal slot that has been removed."""


class MealSlotConflictError(Exception):
    """Raised when creating a meal slot that already actively exists on the canvas."""


class EatenMealImmutableError(Exception):
    """Raised when an operation attempts to modify an eaten meal or its items."""


# Backward compatibility aliases
EatenItemImmutableError = EatenMealImmutableError
MealHasEatenItemsError = EatenMealImmutableError


class DayAlreadyCreatedError(Exception):
    """Raised when trying to explicitly create a day that already exists."""


class DayNotCreatedError(Exception):
    """Raised when trying to perform operations on an uncreated day."""


async def is_day_created(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> bool:
    """Check if a MenuDay row exists for user on menu_date."""
    stmt = (
        select(MenuDay.date)
        .where(
            MenuDay.user_id == user_id,
            MenuDay.date == menu_date,
        )
        .limit(1)
    )
    return (await session.exec(stmt)).first() is not None


async def create_menu_day(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> MenuDay:
    """Create a menu day, inserting one menu row per profile meal,
    and materializing pinned meals.
    """
    if await is_day_created(session, user_id, menu_date):
        raise DayAlreadyCreatedError(f"Menu day for {menu_date} already exists")

    menu_day = MenuDay(user_id=user_id, date=menu_date)
    session.add(menu_day)

    profile = await get_nutrition_profile(session, user_id)
    if profile and profile.meals:
        existing_stmt = select(Menu.meal_type).where(
            Menu.user_id == user_id,
            Menu.date == menu_date,
        )
        existing_types = set((await session.exec(existing_stmt)).all())

        for p_meal in profile.meals:
            if p_meal.type not in existing_types:
                menu = Menu(
                    user_id=user_id,
                    date=menu_date,
                    meal_type=p_meal.type,
                    time=p_meal.time,
                    removed=False,
                    pin_dismissed=False,
                )
                session.add(menu)
        await session.flush()

        from nutrition.service.pinned import materialize_pinned_meals

        await materialize_pinned_meals(session, user_id, menu_date, commit=False)

    await session.commit()
    return menu_day


def sort_menu_reads(menus: list[MenuRead]) -> list[MenuRead]:
    return sorted(
        menus,
        key=lambda m: (
            1 if m.time is None else 0,
            m.time or "",
            m.meal_type.value if hasattr(m.meal_type, "value") else str(m.meal_type),
        ),
    )


def compute_menu_item_read(
    item: MenuItem,
    *,
    dish: DishRead | None = None,
    is_eaten: bool = False,
    incomplete: bool = False,
) -> MenuItemRead:
    if not is_eaten and item.dish_id is not None and dish is not None:
        if dish.kind == DishKind.READY:
            ratio = (item.portion_g / 100.0) if item.portion_g is not None else 1.0
        else:
            tot_w = sum(c.weight_g for c in dish.components) if dish.components else 0
            ratio = (item.portion_g / tot_w) if (item.portion_g is not None and tot_w > 0) else 1.0

        kcal = round(dish.kcal * ratio, 1) if dish.kcal is not None else None
        protein_g = round(dish.protein_g * ratio, 1) if dish.protein_g is not None else None
        fat_g = round(dish.fat_g * ratio, 1) if dish.fat_g is not None else None
        carbs_g = round(dish.carbs_g * ratio, 1) if dish.carbs_g is not None else None
        fiber_g = round(dish.fiber_g * ratio, 1) if dish.fiber_g is not None else None
        sugar_g = round(dish.sugar_g * ratio, 1) if dish.sugar_g is not None else None
        sodium_mg = round(dish.sodium_mg * ratio, 1) if dish.sodium_mg is not None else None
        nutrition_source = NutritionSource.COMPUTED if dish.kcal is not None else None
    else:
        kcal = item.kcal
        protein_g = item.protein_g
        fat_g = item.fat_g
        carbs_g = item.carbs_g
        fiber_g = item.fiber_g
        sugar_g = item.sugar_g
        sodium_mg = item.sodium_mg
        nutrition_source = item.nutrition_source

    return MenuItemRead(
        id=item.id,
        menu_id=item.menu_id,
        dish_id=item.dish_id,
        title=item.title,
        portion_g=item.portion_g,
        portion_text=item.portion_text,
        origin=item.origin,
        position=item.position,
        kcal=kcal,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        fiber_g=fiber_g,
        sugar_g=sugar_g,
        sodium_mg=sodium_mg,
        nutrition_source=nutrition_source,
        updated_at=item.updated_at if item.updated_at is not None else datetime.now(UTC),
        incomplete=incomplete,
    )


async def adjust_meal_ready_dish_stock(
    session: AsyncSession,
    user_id: UUID,
    menu_id: UUID,
    *,
    mark_eaten: bool,
) -> None:
    """Adjust ready dish stock when marking a meal eaten (subtract) or unmarking (return)."""
    items_stmt = select(MenuItem).where(MenuItem.menu_id == menu_id)
    items = list((await session.exec(items_stmt)).all())

    dish_portions: dict[UUID, int] = {}
    for item in items:
        if item.dish_id is not None and item.portion_g:
            dish_portions[item.dish_id] = dish_portions.get(item.dish_id, 0) + item.portion_g

    if not dish_portions:
        return

    d_stmt = select(Dish).where(
        col(Dish.id).in_(list(dish_portions.keys())),
        Dish.user_id == user_id,
    )
    dishes = list((await session.exec(d_stmt)).all())
    for dish in dishes:
        if dish.kind == DishKind.READY and dish.remaining_g is not None:
            portion = dish_portions.get(dish.id, 0)
            if portion <= 0:
                continue
            max_bound = dish.cooked_weight_g if dish.cooked_weight_g is not None else 999999
            if mark_eaten:
                dish.remaining_g = max(0, min(max_bound, dish.remaining_g - portion))
            else:
                dish.remaining_g = max(0, min(max_bound, dish.remaining_g + portion))
            session.add(dish)


async def adjust_meal_constant_dish_stock(
    session: AsyncSession,
    user_id: UUID,
    menu_id: UUID,
    *,
    mark_eaten: bool,
) -> None:
    """Adjust product stock when marking constant dish meal eaten or unmarking."""
    items_stmt = select(MenuItem).where(MenuItem.menu_id == menu_id)
    items = list((await session.exec(items_stmt)).all())

    constant_dish_ids = [it.dish_id for it in items if it.dish_id is not None]
    if not constant_dish_ids:
        return

    d_stmt = select(Dish).where(
        col(Dish.id).in_(constant_dish_ids),
        Dish.user_id == user_id,
        Dish.kind == DishKind.CONSTANT,
    )
    constant_dishes = list((await session.exec(d_stmt)).all())
    if not constant_dishes:
        return

    dish_counts: dict[UUID, int] = {}
    for it in items:
        if it.dish_id in {d.id for d in constant_dishes}:
            dish_counts[it.dish_id] = dish_counts.get(it.dish_id, 0) + 1

    from nutrition.models import DishComponent

    c_stmt = select(DishComponent).where(
        col(DishComponent.dish_id).in_([d.id for d in constant_dishes])
    )
    components = list((await session.exec(c_stmt)).all())

    prod_weights: dict[UUID, int] = {}
    for comp in components:
        count = dish_counts.get(comp.dish_id, 1)
        prod_weights[comp.product_id] = (
            prod_weights.get(comp.product_id, 0) + comp.weight_g * count
        )

    if not prod_weights:
        return

    p_stmt = select(Product).where(
        col(Product.id).in_(list(prod_weights.keys())), Product.user_id == user_id
    )
    products = list((await session.exec(p_stmt)).all())

    for p in products:
        if p.stock_g is not None:
            w = prod_weights[p.id]
            if mark_eaten:
                p.stock_g -= w
            else:
                p.stock_g += w
            session.add(p)


async def snapshot_meal_nutrition(
    session: AsyncSession,
    user_id: UUID,
    menu_id: UUID,
    *,
    mark_eaten: bool,
) -> None:
    """Snapshot computed nutrition values for dish items on eaten, or clear them on unmark."""
    items_stmt = select(MenuItem).where(MenuItem.menu_id == menu_id)
    items = list((await session.exec(items_stmt)).all())

    dish_ids = [it.dish_id for it in items if it.dish_id is not None]
    dishes_map: dict[UUID, DishRead] = {}
    if dish_ids:
        for d_id in set(dish_ids):
            d = await get_dish(session, d_id, user_id)
            if d:
                dishes_map[d_id] = d

    for it in items:
        if mark_eaten:
            if it.dish_id is not None:
                dish = dishes_map.get(it.dish_id)
                if dish:
                    if dish.kind == DishKind.READY:
                        ratio = (it.portion_g / 100.0) if it.portion_g is not None else 1.0
                    else:
                        tot_w = sum(c.weight_g for c in dish.components) if dish.components else 0
                        ratio = (
                            (it.portion_g / tot_w)
                            if (it.portion_g is not None and tot_w > 0)
                            else 1.0
                        )

                    it.kcal = round(dish.kcal * ratio, 1) if dish.kcal is not None else None
                    it.protein_g = (
                        round(dish.protein_g * ratio, 1) if dish.protein_g is not None else None
                    )
                    it.fat_g = round(dish.fat_g * ratio, 1) if dish.fat_g is not None else None
                    it.carbs_g = (
                        round(dish.carbs_g * ratio, 1) if dish.carbs_g is not None else None
                    )
                    it.fiber_g = (
                        round(dish.fiber_g * ratio, 1) if dish.fiber_g is not None else None
                    )
                    it.sugar_g = (
                        round(dish.sugar_g * ratio, 1) if dish.sugar_g is not None else None
                    )
                    it.sodium_mg = (
                        round(dish.sodium_mg * ratio, 1) if dish.sodium_mg is not None else None
                    )
                    it.nutrition_source = NutritionSource.COMPUTED
                    it.updated_at = datetime.now(UTC)
                    session.add(it)
        else:
            if it.nutrition_source == NutritionSource.COMPUTED:
                it.kcal = None
                it.protein_g = None
                it.fat_g = None
                it.carbs_g = None
                it.fiber_g = None
                it.sugar_g = None
                it.sodium_mg = None
                it.nutrition_source = None
                it.updated_at = datetime.now(UTC)
                session.add(it)


async def evaluate_day_menu_reads(
    session: AsyncSession,
    user_id: UUID,
    menus: list[Menu],
    now: datetime | None = None,
) -> list[MenuRead]:
    """Derive MenuRead instances with read-time status, eaten_at and item incompleteness."""
    if not menus:
        return []

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    now_date = now.date()
    now_time = now.strftime("%H:%M")

    menus = sorted(
        menus,
        key=lambda m: (
            m.created_at if m.created_at is not None else datetime.min.replace(tzinfo=UTC),
            m.meal_type.value if hasattr(m.meal_type, "value") else str(m.meal_type),
        ),
    )

    menu_ids = [m.id for m in menus]
    items_stmt = (
        select(MenuItem)
        .where(col(MenuItem.menu_id).in_(menu_ids))
        .order_by(col(MenuItem.position).asc())
    )
    all_items = list((await session.exec(items_stmt)).all())

    items_by_menu: dict[UUID, list[MenuItem]] = {m.id: [] for m in menus}
    dish_ids: set[UUID] = set()
    for item in all_items:
        items_by_menu[item.menu_id].append(item)
        if item.dish_id:
            dish_ids.add(item.dish_id)

    dishes_map: dict[UUID, DishRead] = {}
    if dish_ids:
        all_dishes = await get_dishes(session, user_id)
        for d in all_dishes:
            if d.id in dish_ids:
                dishes_map[d.id] = d

    reserves_map = await get_product_reserves_map(session, user_id, now=now)

    # Later-time meal eaten check for today or other dates
    eaten_times = [
        m.time for m in menus
        if m.eaten_at is not None and m.time is not None
    ]
    max_eaten_time = max(eaten_times) if eaten_times else None

    reserved_portions: dict[UUID, int] = {}
    menu_reads: list[MenuRead] = []

    for menu in menus:
        menu_items = items_by_menu.get(menu.id, [])
        item_reads: list[MenuItemRead] = []
        is_eaten = menu.eaten_at is not None

        is_missed = False
        if not is_eaten:
            if menu.date < now_date:
                is_missed = True
            elif menu.date == now_date and menu.time is not None and menu.time < now_time:
                is_missed = True
            elif (
                menu.time is not None
                and max_eaten_time is not None
                and menu.time < max_eaten_time
            ):
                is_missed = True

        for item in menu_items:
            incomplete = False
            dish = dishes_map.get(item.dish_id) if item.dish_id is not None else None

            if not is_eaten and not is_missed:
                if item.dish_id is not None:
                    if dish is None:
                        incomplete = True
                    elif dish.kind == DishKind.READY:
                        portion = item.portion_g or 0
                        if dish.remaining_g is not None:
                            current_reserved = reserved_portions.get(dish.id, 0)
                            if current_reserved + portion > dish.remaining_g:
                                incomplete = True
                            reserved_portions[dish.id] = current_reserved + portion
                    elif dish.kind == DishKind.CONSTANT:
                        for comp in dish.components:
                            prod = await get_product_entity(session, comp.product_id, user_id)
                            if prod and prod.stock_g is not None:
                                reserved = reserves_map.get(prod.id, 0)
                                if comp.weight_g > (prod.stock_g - reserved):
                                    incomplete = True

            item_reads.append(
                compute_menu_item_read(
                    item,
                    dish=dish,
                    is_eaten=is_eaten,
                    incomplete=incomplete,
                )
            )

        if is_eaten:
            status_val = MenuStatus.EATEN
        elif is_missed:
            status_val = MenuStatus.MISSED
        elif any(it.incomplete for it in item_reads):
            status_val = MenuStatus.INCOMPLETE
        else:
            status_val = MenuStatus.PLANNED

        menu_reads.append(
            MenuRead(
                id=menu.id,
                user_id=menu.user_id,
                date=menu.date,
                meal_type=menu.meal_type,
                time=menu.time,
                status=status_val,
                eaten_at=menu.eaten_at,
                regenerations=menu.regenerations,
                created_at=menu.created_at,
                updated_at=menu.updated_at,
                items=item_reads,
                pinned=menu.pinned_meal_id is not None,
            )
        )

    return menu_reads


async def get_day_menus(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    now: datetime | None = None,
) -> list[MenuRead]:
    """Fetch day canvas for a user on a given date, ordered by time then meal_type."""
    if now is None:
        eff_now = datetime.now(UTC)
    elif now.tzinfo is None:
        eff_now = now.replace(tzinfo=UTC)
    else:
        eff_now = now

    now_date = eff_now.date()
    now_time = eff_now.strftime("%H:%M")

    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    all_rows = list((await session.exec(stmt)).all())

    active_rows = [m for m in all_rows if not m.removed]
    persisted_types = {m.meal_type for m in all_rows}

    active_reads = await evaluate_day_menu_reads(session, user_id, active_rows, now=eff_now)

    eaten_times = [
        m.time for m in active_rows
        if m.eaten_at is not None and m.time is not None
    ]
    max_eaten_time = max(eaten_times) if eaten_times else None

    profile = await get_nutrition_profile(session, user_id)
    projected_reads: list[MenuRead] = []
    if profile and profile.meals:
        for p_meal in profile.meals:
            if p_meal.type not in persisted_types:
                is_missed = (
                    (menu_date < now_date)
                    or (
                        menu_date == now_date
                        and p_meal.time is not None
                        and p_meal.time < now_time
                    )
                    or (
                        p_meal.time is not None
                        and max_eaten_time is not None
                        and p_meal.time < max_eaten_time
                    )
                )
                status_val = MenuStatus.MISSED if is_missed else MenuStatus.PLANNED
                projected_reads.append(
                    MenuRead(
                        id=None,
                        user_id=user_id,
                        date=menu_date,
                        meal_type=p_meal.type,
                        time=p_meal.time,
                        status=status_val,
                        eaten_at=None,
                        regenerations=0,
                        created_at=None,
                        updated_at=None,
                        items=[],
                    )
                )

    canvas = active_reads + projected_reads
    return sort_menu_reads(canvas)


async def has_menu_rows(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> bool:
    """Check if any Menu rows (active or removed) exist for user on menu_date."""
    stmt = select(Menu.id).where(Menu.user_id == user_id, Menu.date == menu_date).limit(1)
    return (await session.exec(stmt)).first() is not None


async def get_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
    now: datetime | None = None,
) -> MenuRead | None:
    """Fetch a specific meal slot menu by date and meal type."""
    day_reads = await get_day_menus(session, user_id, menu_date, now=now)
    for m in day_reads:
        if m.meal_type == meal_type:
            return m
    return None


async def get_menu_by_id(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
) -> Menu | None:
    """Fetch Menu entity by ID scoped to user."""
    stmt = select(Menu).where(Menu.id == menu_id, Menu.user_id == user_id)
    return (await session.exec(stmt)).first()


async def get_menu_read(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> MenuRead | None:
    """Fetch MenuRead by ID with derived status and items."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None or menu.removed:
        return None
    day_reads = await get_day_menus(session, user_id, menu.date, now=now)
    for m in day_reads:
        if m.id == menu.id:
            return m
    return None


async def get_or_create_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
) -> Menu:
    """Get existing meal slot menu for user, date and meal_type, or create a new one."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.meal_type == meal_type,
    )
    menu = (await session.exec(stmt)).first()
    if menu is not None:
        if menu.removed:
            raise MealSlotRemovedError(
                f"Meal '{meal_type.value}' is removed for date {menu_date}"
            )
        if menu.time is None:
            profile = await get_nutrition_profile(session, user_id)
            if profile and profile.meals:
                for m in profile.meals:
                    if m.type == meal_type and m.time:
                        menu.time = m.time
                        session.add(menu)
                        break
        return menu

    profile = await get_nutrition_profile(session, user_id)
    meal_time: str | None = None
    if profile and profile.meals:
        for m in profile.meals:
            if m.type == meal_type:
                meal_time = m.time
                break

    menu = Menu(
        user_id=user_id,
        date=menu_date,
        meal_type=meal_type,
        time=meal_time,
        removed=False,
    )
    session.add(menu)
    await session.flush()
    await session.refresh(menu)
    return menu


async def update_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
    data: MenuUpdate,
) -> MenuRead | None:
    """Update meal eaten mark, adjust ready and constant dish stock, and snapshot nutrition."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None or menu.removed:
        return None

    if "eaten_at" in data.model_fields_set:
        was_eaten = menu.eaten_at is not None
        will_be_eaten = data.eaten_at is not None

        if not was_eaten and will_be_eaten:
            await adjust_meal_ready_dish_stock(session, user_id, menu.id, mark_eaten=True)
            await adjust_meal_constant_dish_stock(session, user_id, menu.id, mark_eaten=True)
            await snapshot_meal_nutrition(session, user_id, menu.id, mark_eaten=True)
        elif was_eaten and not will_be_eaten:
            await adjust_meal_ready_dish_stock(session, user_id, menu.id, mark_eaten=False)
            await adjust_meal_constant_dish_stock(session, user_id, menu.id, mark_eaten=False)
            await snapshot_meal_nutrition(session, user_id, menu.id, mark_eaten=False)

        menu.eaten_at = data.eaten_at
        menu.updated_at = datetime.now(UTC)
        session.add(menu)
        await session.commit()

    return await get_menu_read(session, menu.id, user_id)


async def delete_meal_slot(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
) -> bool:
    """Delete a meal slot: remove all items and tombstone the menu row."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.meal_type == meal_type,
    )
    menu = (await session.exec(stmt)).first()

    if menu is not None:
        if menu.removed:
            return False

        if menu.eaten_at is not None:
            raise EatenMealImmutableError(
                f"Cannot delete meal '{meal_type.value}' because it is marked as eaten"
            )

        items_stmt = select(MenuItem).where(MenuItem.menu_id == menu.id)
        items = list((await session.exec(items_stmt)).all())

        for item in items:
            await session.delete(item)

        if menu.pinned_meal_id is not None:
            menu.pinned_meal_id = None
            menu.pin_dismissed = True
            menu.updated_at = datetime.now(UTC)
            session.add(menu)
            await session.commit()
            return True

        menu.removed = True
        menu.updated_at = datetime.now(UTC)
        session.add(menu)
        await session.commit()
        return True

    profile = await get_nutrition_profile(session, user_id)
    if profile and profile.meals and any(m.type == meal_type for m in profile.meals):
        tombstone = Menu(
            user_id=user_id,
            date=menu_date,
            meal_type=meal_type,
            time=None,
            removed=True,
        )
        session.add(tombstone)
        await session.commit()
        return True

    return False


async def create_meal(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
    time: str | None = None,
) -> MenuRead:
    """Add a meal slot to the day canvas."""
    existing_reads = await get_day_menus(session, user_id, menu_date)
    for m in existing_reads:
        if m.meal_type == meal_type:
            raise MealSlotConflictError(
                f"Meal '{meal_type.value}' already exists on canvas for date {menu_date}"
            )

    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.meal_type == meal_type,
    )
    menu = (await session.exec(stmt)).first()
    if menu is not None:
        if menu.removed:
            menu.removed = False
            menu.time = time
            menu.updated_at = datetime.now(UTC)
            session.add(menu)
            await session.commit()
            return (await get_menu(session, user_id, menu_date, meal_type))  # type: ignore[return-value]
        raise MealSlotConflictError(
            f"Meal '{meal_type.value}' already exists on canvas for date {menu_date}"
        )

    menu = Menu(
        user_id=user_id,
        date=menu_date,
        meal_type=meal_type,
        time=time,
        removed=False,
    )
    session.add(menu)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise MealSlotConflictError(
            f"Meal '{meal_type.value}' already exists on canvas for date {menu_date}"
        ) from exc

    return (await get_menu(session, user_id, menu_date, meal_type))  # type: ignore[return-value]


create_or_revive_meal_slot = create_meal


async def add_menu_item(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    meal_type: MealType,
    data: MenuItemCreate,
) -> MenuItemRead:
    """Add a new item to a meal slot, validating available stock for constant dishes."""
    title = data.title
    if data.dish_id is not None:
        dish = await get_dish(session, data.dish_id, user_id)
        if dish is None:
            raise KeyError("Dish not found")
        if not title:
            title = dish.name

        if dish.kind == DishKind.CONSTANT and dish.components:
            reserves_map = await get_product_reserves_map(session, user_id)
            for comp in dish.components:
                p = await get_product_entity(session, comp.product_id, user_id)
                if p and p.stock_g is not None:
                    reserved = reserves_map.get(p.id, 0)
                    available = p.stock_g - reserved
                    if comp.weight_g > available:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Not enough available stock for product '{p.name}'",
                        )

    menu = await get_or_create_menu(session, user_id, menu_date, meal_type)
    if menu.eaten_at is not None:
        raise EatenMealImmutableError(f"Cannot add items to eaten meal '{meal_type.value}'")

    has_user_nutrition = any(
        getattr(data, f) is not None
        for f in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")
    )
    nutrition_source = NutritionSource.USER if has_user_nutrition else None

    item = MenuItem(
        menu_id=menu.id,
        dish_id=data.dish_id,
        title=title or "",
        portion_g=data.portion_g,
        portion_text=data.portion_text,
        origin=MenuItemOrigin.MANUAL,
        position=data.position,
        kcal=data.kcal,
        protein_g=data.protein_g,
        fat_g=data.fat_g,
        carbs_g=data.carbs_g,
        fiber_g=data.fiber_g,
        sugar_g=data.sugar_g,
        sodium_mg=data.sodium_mg,
        nutrition_source=nutrition_source,
    )
    session.add(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()
    await session.refresh(item)

    day_reads = await get_day_menus(session, user_id, menu_date)
    for m in day_reads:
        for it in m.items:
            if it.id == item.id:
                return it
    return compute_menu_item_read(item, incomplete=False)


async def get_menu_item_with_menu(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
) -> tuple[MenuItem, Menu] | None:
    """Fetch item and verify it belongs to user's menu."""
    stmt = (
        select(MenuItem, Menu)
        .join(Menu, col(MenuItem.menu_id) == col(Menu.id))
        .where(col(MenuItem.id) == item_id, col(Menu.user_id) == user_id)
    )
    return (await session.exec(stmt)).first()


async def update_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
    data: MenuItemUpdate,
) -> MenuItemRead | None:
    """Update a menu item, checking available stock when replacing with a constant dish."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return None
    item, menu = row

    if menu.eaten_at is not None:
        raise EatenMealImmutableError("Cannot modify items of an eaten meal")

    # Handle constant dish stock check on replacement
    if data.dish_id is not None:
        new_dish = await get_dish(session, data.dish_id, user_id)
        if new_dish is None:
            raise KeyError("Dish not found")

        if new_dish.kind == DishKind.CONSTANT and new_dish.components:
            reserves_map = await get_product_reserves_map(session, user_id)
            old_reserved_by_item: dict[UUID, int] = {}
            if menu.eaten_at is None and not menu.removed and menu.date >= dt_date.today():
                if item.dish_id is not None:
                    old_dish = await get_dish(session, item.dish_id, user_id)
                    if old_dish and old_dish.kind == DishKind.CONSTANT:
                        for c in old_dish.components:
                            old_reserved_by_item[c.product_id] = (
                                old_reserved_by_item.get(c.product_id, 0) + c.weight_g
                            )

            for comp in new_dish.components:
                p = await get_product_entity(session, comp.product_id, user_id)
                if p and p.stock_g is not None:
                    curr_reserved = reserves_map.get(p.id, 0) - old_reserved_by_item.get(p.id, 0)
                    available = p.stock_g - curr_reserved
                    if comp.weight_g > available:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Not enough available stock for product '{p.name}'",
                        )

        item.dish_id = data.dish_id
        if data.title is None:
            item.title = new_dish.name

    nutrition_fields = (
        "kcal",
        "protein_g",
        "fat_g",
        "carbs_g",
        "fiber_g",
        "sugar_g",
        "sodium_mg",
    )
    user_passed_nutrition = any(f in data.model_fields_set for f in nutrition_fields)

    # Free text items: reset model estimate if title/portion changed without user nutrition
    if (
        item.dish_id is None
        and item.nutrition_source == NutritionSource.MODEL
        and not user_passed_nutrition
        and (
            (data.title is not None and data.title != item.title)
            or (data.portion_g is not None and data.portion_g != item.portion_g)
        )
    ):
        for f in nutrition_fields:
            setattr(item, f, None)
        item.nutrition_source = None

    if data.title is not None:
        item.title = data.title
    if data.portion_g is not None:
        item.portion_g = data.portion_g
    if data.portion_text is not None:
        item.portion_text = data.portion_text
    if data.position is not None:
        item.position = data.position

    if user_passed_nutrition:
        for f in nutrition_fields:
            if f in data.model_fields_set:
                setattr(item, f, getattr(data, f))
        has_any = any(getattr(item, f) is not None for f in nutrition_fields)
        item.nutrition_source = NutritionSource.USER if has_any else None

    item.updated_at = datetime.now(UTC)
    session.add(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()
    await session.refresh(item)

    day_reads = await get_day_menus(session, user_id, menu.date)
    for m in day_reads:
        for it in m.items:
            if it.id == item.id:
                return it
    return compute_menu_item_read(item, incomplete=False)


async def delete_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a menu item."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return False
    item, menu = row

    if menu.eaten_at is not None:
        raise EatenMealImmutableError("Cannot delete items of an eaten meal")

    await session.delete(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()
    return True


async def replace_unmarked_slot_items(
    session: AsyncSession,
    menu: Menu,
    new_items_data: list[MenuItemCreate],
) -> None:
    """Replace all items in a non-eaten meal slot."""
    if menu.eaten_at is not None:
        raise EatenMealImmutableError(
            f"Cannot replace items of eaten meal '{menu.meal_type.value}'"
        )

    stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu.id)
        .order_by(col(MenuItem.position).asc())
    )
    existing_items = list((await session.exec(stmt)).all())
    for it in existing_items:
        await session.delete(it)

    for idx, it_data in enumerate(new_items_data, start=1):
        item = MenuItem(
            menu_id=menu.id,
            dish_id=it_data.dish_id,
            title=it_data.title or "",
            portion_g=it_data.portion_g,
            portion_text=it_data.portion_text,
            origin=MenuItemOrigin.GENERATED,
            position=it_data.position if it_data.position else idx,
            kcal=it_data.kcal,
            protein_g=it_data.protein_g,
            fat_g=it_data.fat_g,
            carbs_g=it_data.carbs_g,
            fiber_g=it_data.fiber_g,
            sugar_g=it_data.sugar_g,
            sodium_mg=it_data.sodium_mg,
            nutrition_source=None,
        )
        session.add(item)

    menu.regenerations += 1
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.flush()


async def add_generation_log(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    scope: str,
    model: str,
    prompt_version: str,
    prompt: str | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    raw_response: str | None = None,
) -> None:
    """Record an entry in generation_log via the CRUD layer."""
    log_entry = GenerationLog(
        user_id=user_id,
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
    await session.flush()

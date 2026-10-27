from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.profile import get_nutrition_profile
from nutrition.models import (
    Dish,
    DishKind,
    GenerationLog,
    KcalSource,
    MealType,
    Menu,
    MenuItem,
    MenuItemCreate,
    MenuItemOrigin,
    MenuItemRead,
    MenuItemUpdate,
    MenuRead,
    MenuStatus,
    MenuUpdate,
    Product,
    ProductAvailability,
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
    incomplete: bool = False,
) -> MenuItemRead:
    return MenuItemRead(
        id=item.id,
        menu_id=item.menu_id,
        dish_id=item.dish_id,
        product_id=item.product_id,
        title=item.title,
        portion_g=item.portion_g,
        portion_text=item.portion_text,
        kcal_est=item.kcal_est,
        kcal_source=item.kcal_source,
        origin=item.origin,
        position=item.position,
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


async def evaluate_day_menu_reads(
    session: AsyncSession,
    user_id: UUID,
    menus: list[Menu],
) -> list[MenuRead]:
    """Derive MenuRead instances with read-time status, eaten_at and item incompleteness."""
    if not menus:
        return []

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
    all_items = (await session.exec(items_stmt)).all()

    items_by_menu: dict[UUID, list[MenuItem]] = {m.id: [] for m in menus}
    dish_ids: set[UUID] = set()
    product_ids: set[UUID] = set()
    for item in all_items:
        items_by_menu[item.menu_id].append(item)
        if item.dish_id:
            dish_ids.add(item.dish_id)
        if item.product_id:
            product_ids.add(item.product_id)

    dishes_map: dict[UUID, Dish] = {}
    if dish_ids:
        d_stmt = select(Dish).where(col(Dish.id).in_(list(dish_ids)), Dish.user_id == user_id)
        for d in (await session.exec(d_stmt)).all():
            dishes_map[d.id] = d

    products_map: dict[UUID, Product] = {}
    if product_ids:
        p_stmt = select(Product).where(
            col(Product.id).in_(list(product_ids)), Product.user_id == user_id
        )
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    # Running cumulative consumption reservation for ready dishes
    # across items of non-eaten meals of the day
    reserved_portions: dict[UUID, int] = {}
    menu_reads: list[MenuRead] = []

    for menu in menus:
        menu_items = items_by_menu.get(menu.id, [])
        item_reads: list[MenuItemRead] = []
        is_eaten = menu.eaten_at is not None

        for item in menu_items:
            incomplete = False
            # The derived incomplete of an item is computed only for items of non-eaten meals
            if not is_eaten:
                if item.dish_id is not None:
                    dish = dishes_map.get(item.dish_id)
                    if dish is None:
                        incomplete = True
                    elif dish.kind == DishKind.READY:
                        portion = item.portion_g or 0
                        if dish.remaining_g is not None:
                            current_reserved = reserved_portions.get(dish.id, 0)
                            if current_reserved + portion > dish.remaining_g:
                                incomplete = True
                            reserved_portions[dish.id] = current_reserved + portion
                elif item.product_id is not None:
                    product = products_map.get(item.product_id)
                    if product is None or product.availability == ProductAvailability.OUT:
                        incomplete = True

            item_reads.append(compute_menu_item_read(item, incomplete=incomplete))

        if is_eaten:
            status = MenuStatus.EATEN
        elif any(it.incomplete for it in item_reads):
            status = MenuStatus.INCOMPLETE
        else:
            status = MenuStatus.PLANNED

        menu_reads.append(
            MenuRead(
                id=menu.id,
                user_id=menu.user_id,
                date=menu.date,
                meal_type=menu.meal_type,
                time=menu.time,
                status=status,
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
    menu_date: date,
) -> list[MenuRead]:
    """Fetch day canvas for a user on a given date, ordered by time then meal_type."""
    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    all_rows = list((await session.exec(stmt)).all())

    active_rows = [m for m in all_rows if not m.removed]
    persisted_types = {m.meal_type for m in all_rows}

    active_reads = await evaluate_day_menu_reads(session, user_id, active_rows)

    profile = await get_nutrition_profile(session, user_id)
    projected_reads: list[MenuRead] = []
    if profile and profile.meals:
        for p_meal in profile.meals:
            if p_meal.type not in persisted_types:
                projected_reads.append(
                    MenuRead(
                        id=None,
                        user_id=user_id,
                        date=menu_date,
                        meal_type=p_meal.type,
                        time=p_meal.time,
                        status=MenuStatus.PLANNED,
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
    menu_date: date,
) -> bool:
    """Check if any Menu rows (active or removed) exist for user on menu_date."""
    stmt = select(Menu.id).where(Menu.user_id == user_id, Menu.date == menu_date).limit(1)
    return (await session.exec(stmt)).first() is not None


async def get_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    meal_type: MealType,
) -> MenuRead | None:
    """Fetch a specific meal slot menu by date and meal type."""
    day_reads = await get_day_menus(session, user_id, menu_date)
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
) -> MenuRead | None:
    """Fetch MenuRead by ID with derived status and items."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None or menu.removed:
        return None
    day_reads = await get_day_menus(session, user_id, menu.date)
    for m in day_reads:
        if m.id == menu.id:
            return m
    return None


async def get_or_create_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
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
    """Update meal eaten mark and adjust ready-dish stock."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None or menu.removed:
        return None

    if "eaten_at" in data.model_fields_set:
        was_eaten = menu.eaten_at is not None
        will_be_eaten = data.eaten_at is not None

        if not was_eaten and will_be_eaten:
            await adjust_meal_ready_dish_stock(session, user_id, menu.id, mark_eaten=True)
        elif was_eaten and not will_be_eaten:
            await adjust_meal_ready_dish_stock(session, user_id, menu.id, mark_eaten=False)

        menu.eaten_at = data.eaten_at
        menu.updated_at = datetime.now(UTC)
        session.add(menu)
        await session.commit()

    return await get_menu_read(session, menu.id, user_id)


async def delete_meal_slot(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
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
    profile_meal = None
    if profile and profile.meals:
        for m in profile.meals:
            if m.type == meal_type:
                profile_meal = m
                break

    if profile_meal is None:
        return False

    tombstone = Menu(
        user_id=user_id,
        date=menu_date,
        meal_type=meal_type,
        time=profile_meal.time,
        removed=True,
    )
    session.add(tombstone)
    await session.commit()
    return True


async def create_or_revive_meal_slot(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    meal_type: MealType,
    time: str,
) -> MenuRead:
    """Create a new meal slot or revive a removed tombstone row."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.meal_type == meal_type,
    )
    existing = (await session.exec(stmt)).first()

    if existing is not None and not existing.removed:
        raise MealSlotConflictError(
            f"Active meal '{meal_type.value}' already exists for date {menu_date}"
        )

    if existing is None:
        profile = await get_nutrition_profile(session, user_id)
        if profile and profile.meals:
            if any(m.type == meal_type for m in profile.meals):
                raise MealSlotConflictError(
                    f"Active meal '{meal_type.value}' "
                    "already exists on canvas from profile schedule"
                )

    if existing is not None and existing.removed:
        existing.removed = False
        existing.time = time
        existing.updated_at = datetime.now(UTC)
        session.add(existing)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise MealSlotConflictError(
                f"Active meal '{meal_type.value}' already exists for date {menu_date}"
            ) from exc
        await session.refresh(existing)
        menu = existing
    else:
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
                f"Active meal '{meal_type.value}' already exists for date {menu_date}"
            ) from exc
        await session.refresh(menu)

    menu_read = await get_menu_read(session, menu.id, user_id)
    if menu_read is None:
        menu_read = MenuRead(
            id=menu.id,
            user_id=user_id,
            date=menu_date,
            meal_type=menu.meal_type,
            time=menu.time,
            status=MenuStatus.PLANNED,
            pinned=menu.pinned_meal_id is not None,
            items=[],
            created_at=menu.created_at,
            updated_at=menu.updated_at,
        )
    return menu_read


async def add_menu_item(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    meal_type: MealType,
    data: MenuItemCreate,
) -> MenuItemRead:
    """Add a manual item to the meal slot menu."""
    title = data.title
    if data.dish_id is not None:
        dish = (
            await session.exec(
                select(Dish).where(Dish.id == data.dish_id, Dish.user_id == user_id)
            )
        ).first()
        if dish is None:
            raise KeyError("Dish not found")
        if not title:
            title = dish.name
    elif data.product_id is not None:
        product = (
            await session.exec(
                select(Product).where(Product.id == data.product_id, Product.user_id == user_id)
            )
        ).first()
        if product is None:
            raise KeyError("Product not found")
        if not title:
            title = product.name

    menu = await get_or_create_menu(session, user_id, menu_date, meal_type)
    if menu.eaten_at is not None:
        raise EatenMealImmutableError(f"Cannot add items to eaten meal '{meal_type.value}'")

    item = MenuItem(
        menu_id=menu.id,
        dish_id=data.dish_id,
        product_id=data.product_id,
        title=title or "",
        portion_g=data.portion_g,
        portion_text=data.portion_text,
        kcal_est=data.kcal_est,
        kcal_source=KcalSource.USER if data.kcal_est is not None else None,
        origin=MenuItemOrigin.MANUAL,
        position=data.position,
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
    result = (await session.exec(stmt)).first()
    return result


async def update_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
    data: MenuItemUpdate,
) -> MenuItemRead | None:
    """Update a menu item."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return None
    item, menu = row

    if menu.eaten_at is not None:
        raise EatenMealImmutableError("Cannot modify items of an eaten meal")

    reset_fields = ("title", "portion_g", "portion_text", "dish_id", "product_id")
    has_relevant_change = any(
        f in data.model_fields_set and getattr(data, f) != getattr(item, f)
        for f in reset_fields
    )

    # Handle dish / product replacement
    if data.dish_id is not None and data.dish_id != item.dish_id:
        dish = (
            await session.exec(
                select(Dish).where(Dish.id == data.dish_id, Dish.user_id == user_id)
            )
        ).first()
        if dish is None:
            raise KeyError("Dish not found")
        item.dish_id = data.dish_id
        item.product_id = None
        if data.title is None:
            item.title = dish.name
    elif data.product_id is not None and data.product_id != item.product_id:
        product = (
            await session.exec(
                select(Product).where(Product.id == data.product_id, Product.user_id == user_id)
            )
        ).first()
        if product is None:
            raise KeyError("Product not found")
        item.product_id = data.product_id
        item.dish_id = None
        if data.title is None:
            item.title = product.name

    if (
        has_relevant_change
        and "kcal_est" not in data.model_fields_set
        and item.kcal_source == KcalSource.MODEL
    ):
        item.kcal_est = None
        item.kcal_source = None

    if data.title is not None:
        item.title = data.title
    if data.portion_g is not None:
        item.portion_g = data.portion_g
    if data.portion_text is not None:
        item.portion_text = data.portion_text
    if "kcal_est" in data.model_fields_set:
        item.kcal_est = data.kcal_est
        item.kcal_source = KcalSource.USER if data.kcal_est is not None else None
    if data.position is not None:
        item.position = data.position

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
            product_id=it_data.product_id,
            title=it_data.title or "",
            portion_g=it_data.portion_g,
            portion_text=it_data.portion_text,
            kcal_est=it_data.kcal_est,
            kcal_source=KcalSource.MODEL if it_data.kcal_est is not None else None,
            origin=MenuItemOrigin.GENERATED,
            position=it_data.position if it_data.position else idx,
        )
        session.add(item)

    menu.regenerations += 1
    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.flush()


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

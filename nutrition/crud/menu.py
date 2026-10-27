from datetime import UTC, date, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.crud.profile import get_nutrition_profile
from nutrition.models import (
    Dish,
    DishKind,
    GenerationLog,
    KcalSource,
    Menu,
    MenuItem,
    MenuItemCreate,
    MenuItemOrigin,
    MenuItemRead,
    MenuItemStatus,
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


class EatenItemImmutableError(Exception):
    """Raised when an operation attempts to modify content or delete an eaten item."""


class MealHasEatenItemsError(Exception):
    """Raised when attempting to delete a meal slot that contains eaten items."""


def sort_menu_reads(menus: list[MenuRead]) -> list[MenuRead]:
    return sorted(
        menus,
        key=lambda m: (
            1 if m.time is None else 0,
            m.time or "",
            m.slot,
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
        status=item.status,
        eaten_at=item.eaten_at,
        incomplete=incomplete,
    )


async def adjust_item_ready_dish_stock(
    session: AsyncSession,
    user_id: UUID,
    *,
    old_dish_id: UUID | None = None,
    old_status: MenuItemStatus | None = None,
    old_portion_g: int | None = None,
    new_dish_id: UUID | None = None,
    new_status: MenuItemStatus | None = None,
    new_portion_g: int | None = None,
) -> None:
    """Adjust ready dish stock by reverting old item effect and applying new item effect.

    Effect = portion_g subtracted from remaining_g while the item is eaten.
    Reverting adds back the portion; applying subtracts the new portion.
    Ensures remaining_g >= 0 and not exceeding cooked_weight_g.
    """
    old_eaten = (old_status == MenuItemStatus.EATEN) and (old_dish_id is not None)
    new_eaten = (new_status == MenuItemStatus.EATEN) and (new_dish_id is not None)

    if not old_eaten and not new_eaten:
        return

    old_portion = (old_portion_g or 0) if old_eaten else 0
    new_portion = (new_portion_g or 0) if new_eaten else 0

    if old_dish_id == new_dish_id:
        diff = new_portion - old_portion
        if diff != 0 and old_dish_id is not None:
            dish = (
                await session.exec(
                    select(Dish).where(Dish.id == old_dish_id, Dish.user_id == user_id)
                )
            ).first()
            if dish and dish.kind == DishKind.READY and dish.remaining_g is not None:
                max_bound = dish.cooked_weight_g if dish.cooked_weight_g is not None else 999999
                dish.remaining_g = max(0, min(max_bound, dish.remaining_g - diff))
                session.add(dish)
    else:
        if old_eaten and old_dish_id is not None and old_portion > 0:
            old_dish = (
                await session.exec(
                    select(Dish).where(Dish.id == old_dish_id, Dish.user_id == user_id)
                )
            ).first()
            if old_dish and old_dish.kind == DishKind.READY and old_dish.remaining_g is not None:
                max_bound = (
                    old_dish.cooked_weight_g if old_dish.cooked_weight_g is not None else 999999
                )
                old_dish.remaining_g = max(0, min(max_bound, old_dish.remaining_g + old_portion))
                session.add(old_dish)

        if new_eaten and new_dish_id is not None and new_portion > 0:
            new_dish = (
                await session.exec(
                    select(Dish).where(Dish.id == new_dish_id, Dish.user_id == user_id)
                )
            ).first()
            if new_dish and new_dish.kind == DishKind.READY and new_dish.remaining_g is not None:
                max_bound = (
                    new_dish.cooked_weight_g if new_dish.cooked_weight_g is not None else 999999
                )
                new_dish.remaining_g = max(0, min(max_bound, new_dish.remaining_g - new_portion))
                session.add(new_dish)


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
            m.slot or "",
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

    # Running cumulative consumption reservation for ready dishes across unmarked items of the day
    reserved_portions: dict[UUID, int] = {}
    menu_reads: list[MenuRead] = []

    for menu in menus:
        menu_items = items_by_menu.get(menu.id, [])
        item_reads: list[MenuItemRead] = []

        for item in menu_items:
            incomplete = False
            # Only unmarked items can be incomplete
            if item.status is None:
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

        if not item_reads:
            status = MenuStatus.PLANNED
            latest_eaten_at = None
        else:
            total = len(item_reads)
            eaten_count = sum(1 for it in item_reads if it.status == MenuItemStatus.EATEN)
            skipped_count = sum(1 for it in item_reads if it.status == MenuItemStatus.SKIPPED)
            marked_count = eaten_count + skipped_count

            if eaten_count == total:
                status = MenuStatus.EATEN
            elif skipped_count == total:
                status = MenuStatus.SKIPPED
            elif marked_count > 0:
                status = MenuStatus.PARTIAL
            else:
                if any(it.incomplete for it in item_reads):
                    status = MenuStatus.INCOMPLETE
                else:
                    status = MenuStatus.PLANNED

            eaten_ats = [it.eaten_at for it in item_reads if it.eaten_at is not None]
            latest_eaten_at = max(eaten_ats) if eaten_ats else None

        menu_reads.append(
            MenuRead(
                id=menu.id,
                user_id=menu.user_id,
                date=menu.date,
                slot=menu.slot,
                time=menu.time,
                status=status,
                eaten_at=latest_eaten_at,
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
    """Fetch day canvas for a user on a given date, ordered by time then slot."""
    stmt = select(Menu).where(Menu.user_id == user_id, Menu.date == menu_date)
    all_rows = list((await session.exec(stmt)).all())

    active_rows = [m for m in all_rows if not m.removed]
    persisted_slots = {m.slot for m in all_rows}

    active_reads = await evaluate_day_menu_reads(session, user_id, active_rows)

    profile = await get_nutrition_profile(session, user_id)
    projected_reads: list[MenuRead] = []
    if profile and profile.meals:
        for p_meal in profile.meals:
            if p_meal.name not in persisted_slots:
                projected_reads.append(
                    MenuRead(
                        id=None,
                        user_id=user_id,
                        date=menu_date,
                        slot=p_meal.name,
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
    slot: str,
) -> MenuRead | None:
    """Fetch a specific meal slot menu by date and slot name."""
    day_reads = await get_day_menus(session, user_id, menu_date)
    for m in day_reads:
        if m.slot == slot:
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
    slot: str,
) -> Menu:
    """Get existing meal slot menu for user, date and slot, or create a new one."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.slot == slot,
    )
    menu = (await session.exec(stmt)).first()
    if menu is not None:
        if menu.removed:
            raise MealSlotRemovedError(
                f"Meal slot '{slot}' is removed for date {menu_date}"
            )
        if menu.time is None:
            profile = await get_nutrition_profile(session, user_id)
            if profile and profile.meals:
                for m in profile.meals:
                    if m.name == slot and m.time:
                        menu.time = m.time
                        session.add(menu)
                        break
        return menu

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
    """Bulk update meal status (eaten, planned) across all items of the meal."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None or menu.removed:
        return None

    items_stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu.id)
        .order_by(col(MenuItem.position).asc())
    )
    items = list((await session.exec(items_stmt)).all())

    if data.status is not None:
        if data.status == "eaten":
            target_status = MenuItemStatus.EATEN
            target_eaten_at = data.eaten_at or datetime.now(UTC)
        elif data.status == "planned":
            target_status = None
            target_eaten_at = None
        else:
            raise ValueError(f"Invalid menu update status: {data.status}")

        for item in items:
            old_dish_id = item.dish_id
            old_status = item.status
            old_portion_g = item.portion_g

            item.status = target_status
            item.eaten_at = target_eaten_at

            await adjust_item_ready_dish_stock(
                session,
                user_id,
                old_dish_id=old_dish_id,
                old_status=old_status,
                old_portion_g=old_portion_g,
                new_dish_id=item.dish_id,
                new_status=item.status,
                new_portion_g=item.portion_g,
            )
            session.add(item)
    elif data.eaten_at is not None:
        for item in items:
            if item.status == MenuItemStatus.EATEN:
                item.eaten_at = data.eaten_at
                session.add(item)

    menu.updated_at = datetime.now(UTC)
    session.add(menu)
    await session.commit()

    return await get_menu_read(session, menu.id, user_id)


async def delete_meal_slot(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    slot: str,
) -> bool:
    """Delete a meal slot: remove all items (restoring stock) and tombstone the menu row."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.slot == slot,
    )
    menu = (await session.exec(stmt)).first()

    if menu is not None:
        if menu.removed:
            return False

        items_stmt = select(MenuItem).where(MenuItem.menu_id == menu.id)
        items = list((await session.exec(items_stmt)).all())

        if any(item.status == MenuItemStatus.EATEN for item in items):
            raise MealHasEatenItemsError(
                f"Cannot delete meal slot '{slot}' because it contains eaten items"
            )

        for item in items:
            await adjust_item_ready_dish_stock(
                session,
                user_id,
                old_dish_id=item.dish_id,
                old_status=item.status,
                old_portion_g=item.portion_g,
            )
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
            if m.name == slot:
                profile_meal = m
                break

    if profile_meal is None:
        return False

    tombstone = Menu(
        user_id=user_id,
        date=menu_date,
        slot=slot,
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
    slot: str,
    time: str,
) -> MenuRead:
    """Create a new meal slot or revive a removed tombstone row."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.slot == slot,
    )
    existing = (await session.exec(stmt)).first()

    if existing is not None and not existing.removed:
        raise MealSlotConflictError(
            f"Active meal slot '{slot}' already exists for date {menu_date}"
        )

    if existing is None:
        profile = await get_nutrition_profile(session, user_id)
        if profile and profile.meals:
            if any(m.name == slot for m in profile.meals):
                raise MealSlotConflictError(
                    f"Active meal slot '{slot}' already exists on canvas from profile schedule"
                )

    if existing is not None and existing.removed:
        existing.removed = False
        existing.time = time
        existing.updated_at = datetime.now(UTC)
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        menu = existing
    else:
        menu = Menu(
            user_id=user_id,
            date=menu_date,
            slot=slot,
            time=time,
            removed=False,
        )
        session.add(menu)
        await session.commit()
        await session.refresh(menu)

    menu_read = await get_menu_read(session, menu.id, user_id)
    if menu_read is None:
        menu_read = MenuRead(
            id=menu.id,
            user_id=user_id,
            date=menu_date,
            slot=menu.slot,
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
    slot: str,
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

    menu = await get_or_create_menu(session, user_id, menu_date, slot)

    status = data.status
    eaten_at = data.eaten_at
    if status == MenuItemStatus.EATEN and eaten_at is None:
        eaten_at = datetime.now(UTC)
    elif status != MenuItemStatus.EATEN:
        eaten_at = None

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
        status=status,
        eaten_at=eaten_at,
    )
    session.add(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)

    await adjust_item_ready_dish_stock(
        session,
        user_id,
        new_dish_id=item.dish_id,
        new_status=item.status,
        new_portion_g=item.portion_g,
    )

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
    """Update a menu item and adjust ready dish stock."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return None
    item, menu = row

    old_dish_id = item.dish_id
    old_status = item.status
    old_portion_g = item.portion_g

    content_fields = ("dish_id", "product_id", "title", "portion_g", "portion_text", "kcal_est")
    has_content_change = any(
        f in data.model_fields_set and getattr(data, f) != getattr(item, f)
        for f in content_fields
    )

    if item.status == MenuItemStatus.EATEN and has_content_change:
        raise EatenItemImmutableError("Cannot modify content of an eaten menu item")

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

    # Handle status & eaten_at
    if "status" in data.model_fields_set:
        if data.status is None:
            item.status = None
            item.eaten_at = None
        elif data.status == MenuItemStatus.EATEN:
            item.status = MenuItemStatus.EATEN
            if "eaten_at" in data.model_fields_set and data.eaten_at is not None:
                item.eaten_at = data.eaten_at
            elif item.eaten_at is None:
                item.eaten_at = datetime.now(UTC)
        elif data.status == MenuItemStatus.SKIPPED:
            item.status = MenuItemStatus.SKIPPED
            item.eaten_at = None
    elif "eaten_at" in data.model_fields_set:
        item.eaten_at = data.eaten_at

    await adjust_item_ready_dish_stock(
        session,
        user_id,
        old_dish_id=old_dish_id,
        old_status=old_status,
        old_portion_g=old_portion_g,
        new_dish_id=item.dish_id,
        new_status=item.status,
        new_portion_g=item.portion_g,
    )

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
    """Delete a menu item, restoring dish stock if item was eaten."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return False
    item, menu = row

    if item.status == MenuItemStatus.EATEN:
        raise EatenItemImmutableError("Cannot delete an eaten menu item")

    await adjust_item_ready_dish_stock(
        session,
        user_id,
        old_dish_id=item.dish_id,
        old_status=item.status,
        old_portion_g=item.portion_g,
    )

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
    """Replace only unmarked items in a meal slot, preserving marked items."""
    stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu.id)
        .order_by(col(MenuItem.position).asc())
    )
    existing_items = list((await session.exec(stmt)).all())
    marked_items = [it for it in existing_items if it.status is not None]
    unmarked_items = [it for it in existing_items if it.status is None]

    if existing_items and len(unmarked_items) == 0:
        return

    for it in unmarked_items:
        await session.delete(it)

    max_marked_pos = max((it.position for it in marked_items), default=0)
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
            position=max_marked_pos + idx,
            status=None,
            eaten_at=None,
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

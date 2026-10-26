from datetime import UTC, date, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import (
    Dish,
    DishKind,
    GenerationLog,
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


def _to_menu_item_read(item: MenuItem) -> MenuItemRead:
    return MenuItemRead.model_validate(item)


def _to_menu_read(menu: Menu, items: list[MenuItem]) -> MenuRead:
    return MenuRead(
        id=menu.id,
        user_id=menu.user_id,
        date=menu.date,
        slot=menu.slot,
        status=menu.status,
        eaten_at=menu.eaten_at,
        regenerations=menu.regenerations,
        created_at=menu.created_at,
        updated_at=menu.updated_at,
        items=[_to_menu_item_read(i) for i in items],
    )


async def get_day_menus(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
) -> list[MenuRead]:
    """Fetch all meals (menus) for a user on a given date, ordered by slot."""
    stmt = (
        select(Menu)
        .where(Menu.user_id == user_id, Menu.date == menu_date)
        .order_by(col(Menu.created_at).asc(), col(Menu.slot).asc())
    )
    menus = (await session.exec(stmt)).all()
    if not menus:
        return []

    menu_ids = [m.id for m in menus]
    items_stmt = (
        select(MenuItem)
        .where(col(MenuItem.menu_id).in_(menu_ids))
        .order_by(col(MenuItem.position).asc())
    )
    all_items = (await session.exec(items_stmt)).all()
    items_by_menu: dict[UUID, list[MenuItem]] = {m.id: [] for m in menus}
    for item in all_items:
        items_by_menu[item.menu_id].append(item)

    return [_to_menu_read(m, items_by_menu.get(m.id, [])) for m in menus]


async def get_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    slot: str,
) -> MenuRead | None:
    """Fetch a specific meal slot menu by date and slot name."""
    stmt = select(Menu).where(
        Menu.user_id == user_id,
        Menu.date == menu_date,
        Menu.slot == slot,
    )
    menu = (await session.exec(stmt)).first()
    if menu is None:
        return None

    items_stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu.id)
        .order_by(col(MenuItem.position).asc())
    )
    items = (await session.exec(items_stmt)).all()
    return _to_menu_read(menu, list(items))


async def get_menu_by_id(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
) -> Menu | None:
    """Fetch Menu entity by ID scoped to user."""
    stmt = select(Menu).where(Menu.id == menu_id, Menu.user_id == user_id)
    return (await session.exec(stmt)).first()


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
    if menu is None:
        menu = Menu(
            user_id=user_id,
            date=menu_date,
            slot=slot,
            status=MenuStatus.PLANNED,
        )
        session.add(menu)
        await session.flush()
        await session.refresh(menu)
    return menu


async def evaluate_active_menus_completeness(
    session: AsyncSession,
    user_id: UUID,
) -> None:
    """Check active menus for missing references or insufficient ready dish stock."""
    # Find all planned or incomplete menus
    menus_stmt = (
        select(Menu)
        .where(
            Menu.user_id == user_id,
            col(Menu.status).in_([MenuStatus.PLANNED, MenuStatus.INCOMPLETE]),
        )
        .order_by(col(Menu.date).asc())
    )
    active_menus = (await session.exec(menus_stmt)).all()
    if not active_menus:
        return

    menu_ids = [m.id for m in active_menus]
    items_stmt = (
        select(MenuItem)
        .where(col(MenuItem.menu_id).in_(menu_ids))
        .order_by(col(MenuItem.position).asc())
    )
    all_items = (await session.exec(items_stmt)).all()

    items_by_menu: dict[UUID, list[MenuItem]] = {m.id: [] for m in active_menus}
    dish_ids: set[UUID] = set()
    product_ids: set[UUID] = set()
    for item in all_items:
        items_by_menu[item.menu_id].append(item)
        if item.dish_id:
            dish_ids.add(item.dish_id)
        if item.product_id:
            product_ids.add(item.product_id)

    # Load referenced dishes and products
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

    # Running cumulative consumption reservation for ready dishes across planned meals
    reserved_portions: dict[UUID, int] = {}

    for menu in active_menus:
        is_incomplete = False
        menu_items = items_by_menu.get(menu.id, [])

        for item in menu_items:
            # 1. Null reference -> foreign key was deleted
            if item.dish_id is None and item.product_id is None:
                is_incomplete = True
                break

            # 2. Dish checks
            if item.dish_id is not None:
                dish = dishes_map.get(item.dish_id)
                if dish is None:
                    is_incomplete = True
                    break
                if dish.kind == DishKind.READY:
                    portion = item.portion_g or 0
                    if dish.remaining_g is not None:
                        current_reserved = reserved_portions.get(dish.id, 0)
                        if current_reserved + portion > dish.remaining_g:
                            is_incomplete = True
                            break
                        reserved_portions[dish.id] = current_reserved + portion

            # 3. Product checks
            elif item.product_id is not None:
                product = products_map.get(item.product_id)
                if product is None or product.availability == ProductAvailability.OUT:
                    is_incomplete = True
                    break

        new_status = MenuStatus.INCOMPLETE if is_incomplete else MenuStatus.PLANNED
        if menu.status != new_status:
            menu.status = new_status
            menu.updated_at = datetime.now(UTC)
            session.add(menu)


def apply_ready_dish_stock_change(
    dish: Dish,
    portion_diff: int,
) -> None:
    """Apply consumption change to a ready dish.

    portion_diff > 0 means more was eaten (decrease remaining_g).
    portion_diff < 0 means eating was undone/decreased (increase remaining_g).
    """
    if dish.kind != DishKind.READY or dish.remaining_g is None:
        return
    max_bound = dish.cooked_weight_g if dish.cooked_weight_g is not None else 999999
    dish.remaining_g = max(0, min(max_bound, dish.remaining_g - portion_diff))


async def update_menu(
    session: AsyncSession,
    menu_id: UUID,
    user_id: UUID,
    data: MenuUpdate,
) -> MenuRead | None:
    """Update meal status (e.g. mark eaten/skipped) and adjust dish stocks."""
    menu = await get_menu_by_id(session, menu_id, user_id)
    if menu is None:
        return None

    old_status = menu.status
    new_status = data.status if data.status is not None else old_status

    # Load items
    items_stmt = (
        select(MenuItem)
        .where(MenuItem.menu_id == menu.id)
        .order_by(col(MenuItem.position).asc())
    )
    items = list((await session.exec(items_stmt)).all())

    # If transitioning to/from EATEN, adjust stock of ready dishes
    if old_status != MenuStatus.EATEN and new_status == MenuStatus.EATEN:
        for item in items:
            if item.dish_id and item.portion_g:
                dish = (
                    await session.exec(
                        select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                    )
                ).first()
                if dish:
                    apply_ready_dish_stock_change(dish, item.portion_g)
                    session.add(dish)
        menu.eaten_at = data.eaten_at or datetime.now(UTC)

    elif old_status == MenuStatus.EATEN and new_status != MenuStatus.EATEN:
        for item in items:
            if item.dish_id and item.portion_g:
                dish = (
                    await session.exec(
                        select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                    )
                ).first()
                if dish:
                    apply_ready_dish_stock_change(dish, -item.portion_g)
                    session.add(dish)
        menu.eaten_at = None

    menu.status = new_status
    menu.updated_at = datetime.now(UTC)
    session.add(menu)

    # Re-evaluate any planned meals
    await evaluate_active_menus_completeness(session, user_id)

    await session.commit()
    await session.refresh(menu)
    for i in items:
        await session.refresh(i)

    return _to_menu_read(menu, items)


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

    item = MenuItem(
        menu_id=menu.id,
        dish_id=data.dish_id,
        product_id=data.product_id,
        title=title,
        portion_g=data.portion_g,
        portion_text=data.portion_text,
        kcal_est=data.kcal_est,
        origin=MenuItemOrigin.MANUAL,
        position=data.position,
    )
    session.add(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)

    # If menu is already eaten, deduct from stock immediately
    if menu.status == MenuStatus.EATEN and item.dish_id and item.portion_g:
        dish = (
            await session.exec(
                select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
            )
        ).first()
        if dish:
            apply_ready_dish_stock_change(dish, item.portion_g)
            session.add(dish)

    await evaluate_active_menus_completeness(session, user_id)
    await session.commit()
    await session.refresh(item)
    return _to_menu_item_read(item)


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
    """Update a menu item and adjust stock if the parent meal is eaten."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return None
    item, menu = row

    old_portion = item.portion_g or 0
    old_dish_id = item.dish_id

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

    if data.title is not None:
        item.title = data.title
    if data.portion_g is not None:
        item.portion_g = data.portion_g
    if data.portion_text is not None:
        item.portion_text = data.portion_text
    if data.kcal_est is not None:
        item.kcal_est = data.kcal_est
    if data.position is not None:
        item.position = data.position

    new_portion = item.portion_g or 0

    # If menu is eaten, adjust stock of changed dish/portion
    if menu.status == MenuStatus.EATEN:
        if old_dish_id != item.dish_id:
            # Revert old dish
            if old_dish_id:
                old_d = (
                    await session.exec(
                        select(Dish).where(Dish.id == old_dish_id, Dish.user_id == user_id)
                    )
                ).first()
                if old_d:
                    apply_ready_dish_stock_change(old_d, -old_portion)
                    session.add(old_d)
            # Apply new dish
            if item.dish_id:
                new_d = (
                    await session.exec(
                        select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                    )
                ).first()
                if new_d:
                    apply_ready_dish_stock_change(new_d, new_portion)
                    session.add(new_d)
        elif item.dish_id:
            # Same dish, portion diff
            dish = (
                await session.exec(
                    select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
                )
            ).first()
            if dish:
                apply_ready_dish_stock_change(dish, new_portion - old_portion)
                session.add(dish)

    session.add(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)

    await evaluate_active_menus_completeness(session, user_id)
    await session.commit()
    await session.refresh(item)
    return _to_menu_item_read(item)


async def delete_menu_item(
    session: AsyncSession,
    item_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a menu item, restoring dish stock if parent menu is eaten."""
    row = await get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        return False
    item, menu = row

    if menu.status == MenuStatus.EATEN and item.dish_id is not None and item.portion_g:
        dish = (
            await session.exec(
                select(Dish).where(Dish.id == item.dish_id, Dish.user_id == user_id)
            )
        ).first()
        if dish:
            apply_ready_dish_stock_change(dish, -item.portion_g)
            session.add(dish)

    await session.delete(item)
    menu.updated_at = datetime.now(UTC)
    session.add(menu)

    await evaluate_active_menus_completeness(session, user_id)
    await session.commit()
    return True


async def replace_unmarked_slot_items(
    session: AsyncSession,
    menu: Menu,
    new_items_data: list[MenuItemCreate],
) -> None:
    """Replace all items in a non-completed meal slot."""
    del_stmt = select(MenuItem).where(MenuItem.menu_id == menu.id)
    existing_items = (await session.exec(del_stmt)).all()
    for it in existing_items:
        await session.delete(it)

    for it_data in new_items_data:
        item = MenuItem(
            menu_id=menu.id,
            dish_id=it_data.dish_id,
            product_id=it_data.product_id,
            title=it_data.title or "",
            portion_g=it_data.portion_g,
            portion_text=it_data.portion_text,
            kcal_est=it_data.kcal_est,
            origin=MenuItemOrigin.GENERATED,
            position=it_data.position,
        )
        session.add(item)

    menu.regenerations += 1
    menu.status = MenuStatus.PLANNED
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

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from nutrition.models import (
    Dish,
    DishComponent,
    DishKind,
    Menu,
    MenuItem,
    Product,
    ProductAvailability,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)

logger = logging.getLogger(__name__)


async def get_product_reserves_map(
    session: AsyncSession,
    user_id: UUID,
    now: datetime | None = None,
) -> dict[UUID, int]:
    """Calculate reserved weights per product from constant dishes in
    future/today uneaten, non-missed meals.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    now_date = now.date()
    now_time = now.strftime("%H:%M")

    eaten_stmt = (
        select(Menu.date, Menu.time)
        .where(
            Menu.user_id == user_id,
            Menu.removed.is_(False),
            Menu.eaten_at.is_not(None),
            Menu.date >= now_date,
            Menu.time.is_not(None),
        )
    )
    eaten_rows = (await session.exec(eaten_stmt)).all()
    max_eaten_time_by_date: dict[dt_date, str] = {}
    for d, t in eaten_rows:
        if d not in max_eaten_time_by_date or t > max_eaten_time_by_date[d]:
            max_eaten_time_by_date[d] = t

    stmt = (
        select(Menu, DishComponent.product_id, DishComponent.weight_g)
        .join(MenuItem, col(MenuItem.menu_id) == col(Menu.id))
        .join(Dish, col(MenuItem.dish_id) == col(Dish.id))
        .join(DishComponent, col(DishComponent.dish_id) == col(Dish.id))
        .where(
            Menu.user_id == user_id,
            Menu.removed.is_(False),
            Menu.eaten_at.is_(None),
            Menu.date >= now_date,
            Dish.kind == DishKind.CONSTANT,
        )
    )
    results = (await session.exec(stmt)).all()

    reserves: dict[UUID, int] = {}
    for menu, product_id, weight_g in results:
        max_eaten = max_eaten_time_by_date.get(menu.date)
        is_missed = (
            (menu.date < now_date)
            or (menu.date == now_date and menu.time is not None and menu.time < now_time)
            or (menu.time is not None and max_eaten is not None and menu.time < max_eaten)
        )
        if not is_missed:
            reserves[product_id] = reserves.get(product_id, 0) + int(weight_g)

    return reserves


def to_product_read(product: Product, reserve_g: int = 0) -> ProductRead:
    if product.stock_g is None:
        reserved_g = None
        available_g = None
    else:
        reserved_g = reserve_g
        available_g = product.stock_g - reserved_g

    return ProductRead(
        id=product.id,
        user_id=product.user_id,
        name=product.name,
        availability=product.availability,
        preference=product.preference,
        eat_raw=product.eat_raw,
        count=product.count,
        stock_g=product.stock_g,
        reserved_g=reserved_g,
        available_g=available_g,
        kcal=product.kcal,
        protein_g=product.protein_g,
        fat_g=product.fat_g,
        carbs_g=product.carbs_g,
        fiber_g=product.fiber_g,
        sugar_g=product.sugar_g,
        sodium_mg=product.sodium_mg,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


async def get_product_entity(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> Product | None:
    """Return raw Product entity for the given user."""
    statement = select(Product).where(
        Product.id == product_id,
        Product.user_id == user_id,
    )
    return (await session.exec(statement)).first()


async def create_product(
    session: AsyncSession,
    user_id: UUID,
    data: ProductCreate,
    settings: Settings | None = None,
) -> ProductRead:
    """Create a new product for the given user and estimate nutrition with Gemini."""
    product = Product.model_validate(data, update={"user_id": user_id})
    session.add(product)
    await session.commit()
    await session.refresh(product)

    if settings is None:
        settings = get_settings()

    try:
        from nutrition.service.estimator import estimate_product_nutrition

        est = await estimate_product_nutrition(
            session=session,
            user_id=user_id,
            product_name=product.name,
            settings=settings,
            raise_on_error=False,
        )
        if est:
            product.kcal = est.kcal
            product.protein_g = est.protein_g
            product.fat_g = est.fat_g
            product.carbs_g = est.carbs_g
            product.fiber_g = est.fiber_g
            product.sugar_g = est.sugar_g
            product.sodium_mg = est.sodium_mg
            session.add(product)
            await session.commit()
            await session.refresh(product)
    except Exception as exc:
        logger.warning("Product estimation failed on create for '%s': %s", product.name, exc)

    reserves = await get_product_reserves_map(session, user_id)
    return to_product_read(product, reserves.get(product.id, 0))


async def get_products(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[ProductRead]:
    """Return all products belonging to the given user, ordered by name."""
    statement = (
        select(Product)
        .where(Product.user_id == user_id)
        .order_by(col(Product.name).asc())
    )
    products = list((await session.exec(statement)).all())
    if not products:
        return []

    reserves = await get_product_reserves_map(session, user_id)
    return [to_product_read(p, reserves.get(p.id, 0)) for p in products]


async def get_available_products(
    session: AsyncSession,
    user_id: UUID,
) -> list[ProductRead]:
    """Return products available for consumption (in_stock or low)."""
    stmt = (
        select(Product)
        .where(
            Product.user_id == user_id,
            col(Product.availability).in_([ProductAvailability.IN_STOCK, ProductAvailability.LOW]),
        )
        .order_by(col(Product.name).asc())
    )
    products = list((await session.exec(stmt)).all())
    if not products:
        return []

    reserves = await get_product_reserves_map(session, user_id)
    return [to_product_read(p, reserves.get(p.id, 0)) for p in products]


async def get_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> ProductRead | None:
    """Return a single product by id for the given user with reserve values."""
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return None
    reserves = await get_product_reserves_map(session, user_id)
    return to_product_read(product, reserves.get(product.id, 0))


async def update_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
    data: ProductUpdate,
    settings: Settings | None = None,
) -> ProductRead | None:
    """Apply partial updates to a user's product and re-estimate on name change."""
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return None

    name_changed = data.name is not None and data.name.strip() != product.name
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)
    product.updated_at = datetime.now(UTC)
    session.add(product)
    await session.commit()
    await session.refresh(product)

    if name_changed:
        product.kcal = None
        product.protein_g = None
        product.fat_g = None
        product.carbs_g = None
        product.fiber_g = None
        product.sugar_g = None
        product.sodium_mg = None
        session.add(product)
        await session.commit()
        await session.refresh(product)

        if settings is None:
            settings = get_settings()
        try:
            from nutrition.service.estimator import estimate_product_nutrition

            est = await estimate_product_nutrition(
                session=session,
                user_id=user_id,
                product_name=product.name,
                settings=settings,
                raise_on_error=False,
            )
            if est:
                product.kcal = est.kcal
                product.protein_g = est.protein_g
                product.fat_g = est.fat_g
                product.carbs_g = est.carbs_g
                product.fiber_g = est.fiber_g
                product.sugar_g = est.sugar_g
                product.sodium_mg = est.sodium_mg
                session.add(product)
                await session.commit()
                await session.refresh(product)
        except Exception as exc:
            logger.warning(
                "Product estimation failed on name change for '%s': %s", product.name, exc
            )

    reserves = await get_product_reserves_map(session, user_id)
    return to_product_read(product, reserves.get(product.id, 0))


async def estimate_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
    settings: Settings,
) -> ProductRead | None:
    """Explicitly retry estimation of a product's nutrition set."""
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return None

    from nutrition.service.estimator import estimate_product_nutrition

    est = await estimate_product_nutrition(
        session=session,
        user_id=user_id,
        product_name=product.name,
        settings=settings,
        raise_on_error=True,
    )
    if est:
        product.kcal = est.kcal
        product.protein_g = est.protein_g
        product.fat_g = est.fat_g
        product.carbs_g = est.carbs_g
        product.fiber_g = est.fiber_g
        product.sugar_g = est.sugar_g
        product.sodium_mg = est.sodium_mg
        product.updated_at = datetime.now(UTC)
        session.add(product)
        await session.commit()
        await session.refresh(product)

    reserves = await get_product_reserves_map(session, user_id)
    return to_product_read(product, reserves.get(product.id, 0))


async def delete_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's product."""
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return False
    await session.delete(product)
    await session.commit()
    return True

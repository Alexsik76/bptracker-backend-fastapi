from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import Product, ProductAvailability, ProductCreate, ProductUpdate


async def create_product(
    session: AsyncSession,
    user_id: UUID,
    data: ProductCreate,
) -> Product:
    """Create a new product for the given user."""
    product = Product.model_validate(data, update={"user_id": user_id})
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def get_products(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[Product]:
    """Return all products belonging to the given user, ordered by name."""
    statement = (
        select(Product)
        .where(Product.user_id == user_id)
        .order_by(col(Product.name).asc())
    )
    result = await session.exec(statement)
    return result.all()


async def get_available_products(
    session: AsyncSession,
    user_id: UUID,
) -> list[Product]:
    """Return products available for consumption (in_stock or low)."""
    stmt = (
        select(Product)
        .where(
            Product.user_id == user_id,
            col(Product.availability).in_([ProductAvailability.IN_STOCK, ProductAvailability.LOW]),
        )
        .order_by(col(Product.name).asc())
    )
    return list((await session.exec(stmt)).all())


async def get_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> Product | None:
    """Return a single product by id for the given user."""
    statement = select(Product).where(
        Product.id == product_id,
        Product.user_id == user_id,
    )
    result = await session.exec(statement)
    return result.first()


async def update_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
    data: ProductUpdate,
) -> Product | None:
    """Apply partial updates to a user's product."""
    product = await get_product(session, product_id, user_id)
    if product is None:
        return None
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)
    product.updated_at = datetime.now(UTC)
    session.add(product)

    await session.commit()
    await session.refresh(product)
    return product


async def delete_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's product."""
    product = await get_product(session, product_id, user_id)
    if product is None:
        return False
    await session.delete(product)
    await session.commit()
    return True

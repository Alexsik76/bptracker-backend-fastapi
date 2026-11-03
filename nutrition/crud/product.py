from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import (
    Product,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)


def to_product_read(product: Product) -> ProductRead:
    return ProductRead(
        id=product.id,
        user_id=product.user_id,
        name=product.name,
        available=product.available,
        note=product.note,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


async def get_product_entity(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> Product | None:
    statement = select(Product).where(
        Product.id == product_id,
        Product.user_id == user_id,
    )
    return (await session.exec(statement)).first()


async def create_product(
    session: AsyncSession,
    user_id: UUID,
    data: ProductCreate,
) -> ProductRead:
    product = Product.model_validate(data, update={"user_id": user_id})
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return to_product_read(product)


async def get_products(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[ProductRead]:
    statement = (
        select(Product)
        .where(Product.user_id == user_id)
        .order_by(col(Product.name).asc())
    )
    products = list((await session.exec(statement)).all())
    return [to_product_read(p) for p in products]


async def get_available_products(
    session: AsyncSession,
    user_id: UUID,
) -> list[ProductRead]:
    stmt = (
        select(Product)
        .where(
            Product.user_id == user_id,
            Product.available.is_(True),
        )
        .order_by(col(Product.name).asc())
    )
    products = list((await session.exec(stmt)).all())
    return [to_product_read(p) for p in products]


async def get_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> ProductRead | None:
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return None
    return to_product_read(product)


async def update_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
    data: ProductUpdate,
) -> ProductRead | None:
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return None

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)
    product.updated_at = datetime.now(UTC)
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return to_product_read(product)


async def delete_product(
    session: AsyncSession,
    product_id: UUID,
    user_id: UUID,
) -> bool:
    product = await get_product_entity(session, product_id, user_id)
    if product is None:
        return False
    await session.delete(product)
    await session.commit()
    return True

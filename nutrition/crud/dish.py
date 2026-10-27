from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from nutrition.crud.product import create_product, get_product_reserves_map
from nutrition.models import (
    Dish,
    DishComponent,
    DishComponentRead,
    DishCreate,
    DishKind,
    DishRead,
    DishUpdate,
    Product,
    ProductCreate,
)


def compute_dish_nutrition(
    dish: Dish,
    components: Sequence[DishComponent],
    products_map: dict[UUID, Product],
) -> dict[str, float | None]:
    """Compute the nutrition set for a dish.

    Ready dish: per 100g of cooked_weight_g.
    Constant dish: for the whole composition.
    Missing component values -> NULL, never 0.
    """
    if not components:
        return {
            "kcal": None,
            "protein_g": None,
            "fat_g": None,
            "carbs_g": None,
            "fiber_g": None,
            "sugar_g": None,
            "sodium_mg": None,
        }

    total_kcal = 0.0
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    total_fiber = 0.0
    total_sugar = 0.0
    total_sodium = 0.0

    has_null_kcal = False
    has_null_protein = False
    has_null_fat = False
    has_null_carbs = False
    has_null_fiber = False
    has_null_sugar = False
    has_null_sodium = False

    for comp in components:
        p = products_map.get(comp.product_id)
        if p is None:
            has_null_kcal = True
            has_null_protein = True
            has_null_fat = True
            has_null_carbs = True
            has_null_fiber = True
            has_null_sugar = True
            has_null_sodium = True
            continue

        ratio = comp.weight_g / 100.0

        if p.kcal is None:
            has_null_kcal = True
        else:
            total_kcal += p.kcal * ratio

        if p.protein_g is None:
            has_null_protein = True
        else:
            total_protein += p.protein_g * ratio

        if p.fat_g is None:
            has_null_fat = True
        else:
            total_fat += p.fat_g * ratio

        if p.carbs_g is None:
            has_null_carbs = True
        else:
            total_carbs += p.carbs_g * ratio

        if p.fiber_g is None:
            has_null_fiber = True
        else:
            total_fiber += p.fiber_g * ratio

        if p.sugar_g is None:
            has_null_sugar = True
        else:
            total_sugar += p.sugar_g * ratio

        if p.sodium_mg is None:
            has_null_sodium = True
        else:
            total_sodium += p.sodium_mg * ratio

    if dish.kind == DishKind.READY:
        if dish.cooked_weight_g is None or dish.cooked_weight_g <= 0:
            scale = 0.0
            has_null_kcal = True
            has_null_protein = True
            has_null_fat = True
            has_null_carbs = True
            has_null_fiber = True
            has_null_sugar = True
            has_null_sodium = True
        else:
            scale = 100.0 / dish.cooked_weight_g
    else:
        # Constant dish is for the whole composition
        scale = 1.0

    return {
        "kcal": round(total_kcal * scale, 1) if not has_null_kcal else None,
        "protein_g": round(total_protein * scale, 1) if not has_null_protein else None,
        "fat_g": round(total_fat * scale, 1) if not has_null_fat else None,
        "carbs_g": round(total_carbs * scale, 1) if not has_null_carbs else None,
        "fiber_g": round(total_fiber * scale, 1) if not has_null_fiber else None,
        "sugar_g": round(total_sugar * scale, 1) if not has_null_sugar else None,
        "sodium_mg": round(total_sodium * scale, 1) if not has_null_sodium else None,
    }


def _to_dish_read(
    dish: Dish,
    components: Sequence[DishComponent],
    products_map: dict[UUID, Product],
) -> DishRead:
    comp_reads = [DishComponentRead.model_validate(c) for c in components]
    nutrition = compute_dish_nutrition(dish, components, products_map)
    return DishRead(
        id=dish.id,
        user_id=dish.user_id,
        name=dish.name,
        kind=dish.kind,
        cooked_weight_g=dish.cooked_weight_g,
        remaining_g=dish.remaining_g,
        cooked_on=dish.cooked_on,
        fixed_meal_type=dish.fixed_meal_type,
        notes=dish.notes,
        created_at=dish.created_at,
        updated_at=dish.updated_at,
        components=comp_reads,
        kcal=nutrition["kcal"],
        protein_g=nutrition["protein_g"],
        fat_g=nutrition["fat_g"],
        carbs_g=nutrition["carbs_g"],
        fiber_g=nutrition["fiber_g"],
        sugar_g=nutrition["sugar_g"],
        sodium_mg=nutrition["sodium_mg"],
    )


async def create_dish(
    session: AsyncSession,
    user_id: UUID,
    data: DishCreate,
    settings: Settings | None = None,
) -> DishRead:
    """Create a new dish along with its components, updating ready-dish stock."""
    if settings is None:
        settings = get_settings()

    # 1. Resolve new products if any
    resolved_components: list[tuple[UUID, int]] = []
    for comp_in in data.components:
        if comp_in.new_product:
            new_prod = await create_product(
                session=session,
                user_id=user_id,
                data=ProductCreate(
                    name=comp_in.new_product.name,
                    stock_g=comp_in.new_product.stock_g,
                ),
                settings=settings,
            )
            resolved_components.append((new_prod.id, comp_in.weight_g))
        else:
            assert comp_in.product_id is not None
            resolved_components.append((comp_in.product_id, comp_in.weight_g))

    # 2. Check and subtract stock for ready dish
    prod_ids = list({pid for pid, _ in resolved_components})
    products_map: dict[UUID, Product] = {}
    if prod_ids:
        p_stmt = select(Product).where(col(Product.id).in_(prod_ids), Product.user_id == user_id)
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    for pid in prod_ids:
        if pid not in products_map:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {pid} not found",
            )

    if data.kind == DishKind.READY:
        weights_needed: dict[UUID, int] = {}
        for pid, w in resolved_components:
            weights_needed[pid] = weights_needed.get(pid, 0) + w

        reserves_map = await get_product_reserves_map(session, user_id)
        for pid, needed in weights_needed.items():
            prod = products_map[pid]
            if prod.stock_g is not None:
                reserved = reserves_map.get(pid, 0)
                available = prod.stock_g - reserved
                if needed > available:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Not enough available stock for product '{prod.name}'",
                    )
                prod.stock_g -= needed
                session.add(prod)

    # 3. Create dish entity
    dish_data = data.model_dump(exclude={"components"})
    dish = Dish.model_validate(dish_data, update={"user_id": user_id})
    session.add(dish)
    await session.flush()
    await session.refresh(dish)

    components: list[DishComponent] = []
    for pid, weight in resolved_components:
        comp = DishComponent(dish_id=dish.id, product_id=pid, weight_g=weight)
        session.add(comp)
        components.append(comp)

    await session.commit()
    await session.refresh(dish)
    for comp in components:
        await session.refresh(comp)

    return _to_dish_read(dish, components, products_map)


async def get_dishes(
    session: AsyncSession,
    user_id: UUID,
    kind: DishKind | None = None,
    available: bool | None = None,
) -> list[DishRead]:
    """Return dishes for a user, optionally filtered by kind and availability."""
    statement = select(Dish).where(Dish.user_id == user_id)
    if kind is not None:
        statement = statement.where(Dish.kind == kind)
    if available:
        statement = statement.where(
            (Dish.kind == DishKind.CONSTANT)
            | (
                (Dish.kind == DishKind.READY)
                & ((Dish.remaining_g.is_(None)) | (Dish.remaining_g > 0))
            )
        )
    statement = statement.order_by(col(Dish.name).asc())
    dishes = list((await session.exec(statement)).all())

    if not dishes:
        return []

    dish_ids = [d.id for d in dishes]
    comp_stmt = select(DishComponent).where(col(DishComponent.dish_id).in_(dish_ids))
    comps = list((await session.exec(comp_stmt)).all())

    comps_by_dish: dict[UUID | None, list[DishComponent]] = {d.id: [] for d in dishes}
    prod_ids: set[UUID] = set()
    for comp in comps:
        comps_by_dish[comp.dish_id].append(comp)
        prod_ids.add(comp.product_id)

    products_map: dict[UUID, Product] = {}
    if prod_ids:
        p_stmt = select(Product).where(
            col(Product.id).in_(list(prod_ids)), Product.user_id == user_id
        )
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    return [_to_dish_read(dish, comps_by_dish.get(dish.id, []), products_map) for dish in dishes]


async def get_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> DishRead | None:
    """Return a single dish by id with components and computed nutrition."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    comps = list((await session.exec(comp_stmt)).all())

    prod_ids = [c.product_id for c in comps]
    products_map: dict[UUID, Product] = {}
    if prod_ids:
        p_stmt = select(Product).where(col(Product.id).in_(prod_ids), Product.user_id == user_id)
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    return _to_dish_read(dish, comps, products_map)


async def update_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
    data: DishUpdate,
    settings: Settings | None = None,
) -> DishRead | None:
    """Update a dish and its components, adjusting stock for ready dishes."""
    if settings is None:
        settings = get_settings()

    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    updates = data.model_dump(exclude_unset=True, exclude={"components"})
    for field, value in updates.items():
        setattr(dish, field, value)
    dish.updated_at = datetime.now(UTC)
    session.add(dish)

    if data.components is not None:
        old_comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
        old_comps = list((await session.exec(old_comp_stmt)).all())

        # If ready dish, return old components
        if dish.kind == DishKind.READY:
            old_weights: dict[UUID, int] = {}
            for c in old_comps:
                old_weights[c.product_id] = old_weights.get(c.product_id, 0) + c.weight_g

            if old_weights:
                p_stmt = select(Product).where(
                    col(Product.id).in_(list(old_weights.keys())),
                    Product.user_id == user_id,
                )
                for p in (await session.exec(p_stmt)).all():
                    if p.stock_g is not None:
                        p.stock_g += old_weights[p.id]
                        session.add(p)

        for old in old_comps:
            await session.delete(old)

        resolved_components: list[tuple[UUID, int]] = []
        for comp_in in data.components:
            if comp_in.new_product:
                new_prod = await create_product(
                    session=session,
                    user_id=user_id,
                    data=ProductCreate(
                        name=comp_in.new_product.name,
                        stock_g=comp_in.new_product.stock_g,
                    ),
                    settings=settings,
                )
                resolved_components.append((new_prod.id, comp_in.weight_g))
            else:
                assert comp_in.product_id is not None
                resolved_components.append((comp_in.product_id, comp_in.weight_g))

        # If ready dish, check available stock and subtract new components
        if dish.kind == DishKind.READY:
            new_weights: dict[UUID, int] = {}
            for pid, w in resolved_components:
                new_weights[pid] = new_weights.get(pid, 0) + w

            new_prod_ids = list(new_weights.keys())
            prods_map: dict[UUID, Product] = {}
            if new_prod_ids:
                p_stmt = select(Product).where(
                    col(Product.id).in_(new_prod_ids), Product.user_id == user_id
                )
                for p in (await session.exec(p_stmt)).all():
                    prods_map[p.id] = p

            reserves_map = await get_product_reserves_map(session, user_id)
            for pid, needed in new_weights.items():
                prod = prods_map.get(pid)
                if prod is not None and prod.stock_g is not None:
                    reserved = reserves_map.get(pid, 0)
                    available = prod.stock_g - reserved
                    if needed > available:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Not enough available stock for product '{prod.name}'",
                        )
                    prod.stock_g -= needed
                    session.add(prod)

        for pid, weight in resolved_components:
            comp = DishComponent(dish_id=dish.id, product_id=pid, weight_g=weight)
            session.add(comp)

    await session.commit()
    await session.refresh(dish)

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    current_comps = list((await session.exec(comp_stmt)).all())

    prod_ids = [c.product_id for c in current_comps]
    products_map: dict[UUID, Product] = {}
    if prod_ids:
        p_stmt = select(Product).where(col(Product.id).in_(prod_ids), Product.user_id == user_id)
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    return _to_dish_read(dish, current_comps, products_map)


async def delete_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's dish. Returning components weights to stock for ready dishes."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return False

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    comps = list((await session.exec(comp_stmt)).all())

    if dish.kind == DishKind.READY and comps:
        weights: dict[UUID, int] = {}
        for c in comps:
            weights[c.product_id] = weights.get(c.product_id, 0) + c.weight_g

        p_stmt = select(Product).where(
            col(Product.id).in_(list(weights.keys())), Product.user_id == user_id
        )
        for p in (await session.exec(p_stmt)).all():
            if p.stock_g is not None:
                p.stock_g += weights[p.id]
                session.add(p)

    await session.delete(dish)
    await session.commit()
    return True

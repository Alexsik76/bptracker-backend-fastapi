from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from nutrition.crud.dish import (
    apply_stock_rule,
    compute_nutrition_per_100g,
    compute_nutrition_per_100g_tuples,
)
from nutrition.crud.product import create_product
from nutrition.crud.recipe import get_recipe_entity
from nutrition.models import (
    Dish,
    DishComponent,
    DishComponentRead,
    DishRead,
    FrequentDish,
    FrequentDishComponent,
    FrequentDishComponentRead,
    FrequentDishCreate,
    FrequentDishRead,
    FrequentDishUpdate,
    Product,
    ProductCreate,
    Recipe,
)

NUTR_FIELDS = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")


def _to_frequent_dish_read(
    fd: FrequentDish,
    recipe_name: str,
    components: list[FrequentDishComponent],
) -> FrequentDishRead:
    comp_reads = [FrequentDishComponentRead.model_validate(c) for c in components]
    return FrequentDishRead(
        id=fd.id,
        user_id=fd.user_id,
        recipe_id=fd.recipe_id,
        recipe_name=recipe_name,
        cooked_weight_g=fd.cooked_weight_g,
        nutrition_overridden=fd.nutrition_overridden,
        created_at=fd.created_at,
        updated_at=fd.updated_at,
        components=comp_reads,
        kcal=fd.kcal,
        protein_g=fd.protein_g,
        fat_g=fd.fat_g,
        carbs_g=fd.carbs_g,
        fiber_g=fd.fiber_g,
        sugar_g=fd.sugar_g,
        sodium_mg=fd.sodium_mg,
    )


async def create_frequent_dish(
    session: AsyncSession,
    user_id: UUID,
    data: FrequentDishCreate,
    settings: Settings | None = None,
) -> FrequentDishRead:
    """Create a new frequent dish and compute its nutrition per 100g cooked weight."""
    if settings is None:
        settings = get_settings()

    recipe = await get_recipe_entity(session, data.recipe_id, user_id)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recipe '{data.recipe_id}' not found",
        )

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
                detail=f"Product '{pid}' not found",
            )

    computed = compute_nutrition_per_100g_tuples(
        resolved_components, products_map, data.cooked_weight_g
    )

    explicit_passed = any(f in data.model_fields_set for f in NUTR_FIELDS)
    overridden = explicit_passed
    final_nutr: dict[str, float | None] = {}
    for f in NUTR_FIELDS:
        if f in data.model_fields_set and getattr(data, f) is not None:
            final_nutr[f] = getattr(data, f)
        else:
            final_nutr[f] = computed[f]

    fd = FrequentDish(
        user_id=user_id,
        recipe_id=recipe.id,
        cooked_weight_g=data.cooked_weight_g,
        nutrition_overridden=overridden,
        kcal=final_nutr["kcal"],
        protein_g=final_nutr["protein_g"],
        fat_g=final_nutr["fat_g"],
        carbs_g=final_nutr["carbs_g"],
        fiber_g=final_nutr["fiber_g"],
        sugar_g=final_nutr["sugar_g"],
        sodium_mg=final_nutr["sodium_mg"],
    )
    session.add(fd)
    await session.flush()
    await session.refresh(fd)

    components: list[FrequentDishComponent] = []
    for pid, weight in resolved_components:
        comp = FrequentDishComponent(frequent_dish_id=fd.id, product_id=pid, weight_g=weight)
        session.add(comp)
        components.append(comp)

    await session.commit()
    await session.refresh(fd)
    for comp in components:
        await session.refresh(comp)

    return _to_frequent_dish_read(fd, recipe.name, components)


async def get_frequent_dishes(
    session: AsyncSession,
    user_id: UUID,
) -> list[FrequentDishRead]:
    """Return all frequent dishes for user with recipe name and components."""
    stmt = (
        select(FrequentDish, Recipe.name)
        .join(Recipe, col(FrequentDish.recipe_id) == col(Recipe.id))
        .where(FrequentDish.user_id == user_id)
        .order_by(col(Recipe.name).asc())
    )
    rows = list((await session.exec(stmt)).all())
    if not rows:
        return []

    fd_ids = [fd.id for fd, _ in rows]
    comp_stmt = select(FrequentDishComponent).where(
        col(FrequentDishComponent.frequent_dish_id).in_(fd_ids)
    )
    comps = list((await session.exec(comp_stmt)).all())

    comps_by_fd: dict[UUID | None, list[FrequentDishComponent]] = {fd.id: [] for fd, _ in rows}
    for comp in comps:
        comps_by_fd[comp.frequent_dish_id].append(comp)

    return [
        _to_frequent_dish_read(fd, rec_name, comps_by_fd.get(fd.id, []))
        for fd, rec_name in rows
    ]


async def get_frequent_dish(
    session: AsyncSession,
    frequent_dish_id: UUID,
    user_id: UUID,
) -> FrequentDishRead | None:
    """Return single frequent dish by id."""
    stmt = (
        select(FrequentDish, Recipe.name)
        .join(Recipe, col(FrequentDish.recipe_id) == col(Recipe.id))
        .where(FrequentDish.id == frequent_dish_id, FrequentDish.user_id == user_id)
    )
    row = (await session.exec(stmt)).first()
    if row is None:
        return None
    fd, recipe_name = row

    comp_stmt = select(FrequentDishComponent).where(
        FrequentDishComponent.frequent_dish_id == frequent_dish_id
    )
    comps = list((await session.exec(comp_stmt)).all())

    return _to_frequent_dish_read(fd, recipe_name, comps)


async def update_frequent_dish(
    session: AsyncSession,
    frequent_dish_id: UUID,
    user_id: UUID,
    data: FrequentDishUpdate,
    settings: Settings | None = None,
) -> FrequentDishRead | None:
    """Update frequent dish.

    - Explicit nutrition values store them and set nutrition_overridden=True.
    - reset_nutrition=True recomputes nutrition and clears the flag.
    - Component/cooked_weight changes recompute nutrition if not overridden.
    """
    if settings is None:
        settings = get_settings()

    stmt = select(FrequentDish).where(
        FrequentDish.id == frequent_dish_id, FrequentDish.user_id == user_id
    )
    fd = (await session.exec(stmt)).first()
    if fd is None:
        return None

    if data.recipe_id is not None:
        recipe = await get_recipe_entity(session, data.recipe_id, user_id)
        if recipe is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Recipe '{data.recipe_id}' not found",
            )
        fd.recipe_id = data.recipe_id

    if data.cooked_weight_g is not None:
        fd.cooked_weight_g = data.cooked_weight_g

    comps_changed = False
    if data.components is not None:
        comps_changed = True
        del_stmt = select(FrequentDishComponent).where(
            FrequentDishComponent.frequent_dish_id == frequent_dish_id
        )
        for old in (await session.exec(del_stmt)).all():
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

        for pid, weight in resolved_components:
            session.add(
                FrequentDishComponent(
                    frequent_dish_id=fd.id, product_id=pid, weight_g=weight
                )
            )
        await session.flush()

    # Current components
    comp_stmt = select(FrequentDishComponent).where(
        FrequentDishComponent.frequent_dish_id == frequent_dish_id
    )
    current_comps = list((await session.exec(comp_stmt)).all())

    # Nutrition logic
    if data.reset_nutrition:
        prod_ids = list({c.product_id for c in current_comps})
        products_map: dict[UUID, Product] = {}
        if prod_ids:
            p_stmt = select(Product).where(
                col(Product.id).in_(prod_ids), Product.user_id == user_id
            )
            for p in (await session.exec(p_stmt)).all():
                products_map[p.id] = p
        computed = compute_nutrition_per_100g(current_comps, products_map, fd.cooked_weight_g)
        for f in NUTR_FIELDS:
            setattr(fd, f, computed[f])
        fd.nutrition_overridden = False

    elif any(f in data.model_fields_set for f in NUTR_FIELDS):
        for f in NUTR_FIELDS:
            if f in data.model_fields_set:
                setattr(fd, f, getattr(data, f))
        fd.nutrition_overridden = True

    elif not fd.nutrition_overridden and (comps_changed or data.cooked_weight_g is not None):
        prod_ids = list({c.product_id for c in current_comps})
        products_map = {}
        if prod_ids:
            p_stmt = select(Product).where(
                col(Product.id).in_(prod_ids), Product.user_id == user_id
            )
            for p in (await session.exec(p_stmt)).all():
                products_map[p.id] = p
        computed = compute_nutrition_per_100g(current_comps, products_map, fd.cooked_weight_g)
        for f in NUTR_FIELDS:
            setattr(fd, f, computed[f])

    fd.updated_at = datetime.now(UTC)
    session.add(fd)
    await session.commit()
    await session.refresh(fd)

    return await get_frequent_dish(session, frequent_dish_id, user_id)


async def delete_frequent_dish(
    session: AsyncSession,
    frequent_dish_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a frequent dish."""
    stmt = select(FrequentDish).where(
        FrequentDish.id == frequent_dish_id, FrequentDish.user_id == user_id
    )
    fd = (await session.exec(stmt)).first()
    if fd is None:
        return False
    await session.delete(fd)
    await session.commit()
    return True


async def cook_frequent_dish(
    session: AsyncSession,
    frequent_dish_id: UUID,
    user_id: UUID,
    cooked_on: dt_date,
    settings: Settings | None = None,
) -> DishRead:
    """Cook a frequent dish: creates a dish copying components, weight, nutrition,

    and applies stock rule.
    """
    stmt = (
        select(FrequentDish, Recipe.name)
        .join(Recipe, col(FrequentDish.recipe_id) == col(Recipe.id))
        .where(FrequentDish.id == frequent_dish_id, FrequentDish.user_id == user_id)
    )
    row = (await session.exec(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frequent dish not found",
        )
    fd, recipe_name = row

    comp_stmt = select(FrequentDishComponent).where(
        FrequentDishComponent.frequent_dish_id == frequent_dish_id
    )
    comps = list((await session.exec(comp_stmt)).all())

    prod_ids = list({c.product_id for c in comps})
    products_map: dict[UUID, Product] = {}
    if prod_ids:
        p_stmt = select(Product).where(col(Product.id).in_(prod_ids), Product.user_id == user_id)
        for p in (await session.exec(p_stmt)).all():
            products_map[p.id] = p

    needed_weights: dict[UUID, int] = {}
    for c in comps:
        needed_weights[c.product_id] = needed_weights.get(c.product_id, 0) + c.weight_g

    warnings = apply_stock_rule(needed_weights, products_map, session)

    dish = Dish(
        user_id=user_id,
        recipe_id=fd.recipe_id,
        name=recipe_name,
        cooked_weight_g=fd.cooked_weight_g,
        remaining_g=fd.cooked_weight_g,
        cooked_on=cooked_on,
        kcal=fd.kcal,
        protein_g=fd.protein_g,
        fat_g=fd.fat_g,
        carbs_g=fd.carbs_g,
        fiber_g=fd.fiber_g,
        sugar_g=fd.sugar_g,
        sodium_mg=fd.sodium_mg,
    )
    session.add(dish)
    await session.flush()
    await session.refresh(dish)

    dish_comps: list[DishComponent] = []
    for c in comps:
        dc = DishComponent(dish_id=dish.id, product_id=c.product_id, weight_g=c.weight_g)
        session.add(dc)
        dish_comps.append(dc)

    await session.commit()
    await session.refresh(dish)
    for dc in dish_comps:
        await session.refresh(dc)

    comp_reads = [DishComponentRead.model_validate(c) for c in dish_comps]
    return DishRead(
        id=dish.id,
        user_id=dish.user_id,
        recipe_id=dish.recipe_id,
        name=dish.name,
        cooked_weight_g=dish.cooked_weight_g,
        remaining_g=dish.remaining_g,
        cooked_on=dish.cooked_on,
        notes=dish.notes,
        created_at=dish.created_at,
        updated_at=dish.updated_at,
        components=comp_reads,
        kcal=dish.kcal,
        protein_g=dish.protein_g,
        fat_g=dish.fat_g,
        carbs_g=dish.carbs_g,
        fiber_g=dish.fiber_g,
        sugar_g=dish.sugar_g,
        sodium_mg=dish.sodium_mg,
        stock_warnings=warnings,
    )


async def recompute_frequent_dishes_for_product(
    session: AsyncSession,
    user_id: UUID,
    product_id: UUID,
) -> None:
    """Recompute nutrition for non-overridden frequent dishes that use this product."""
    stmt = (
        select(FrequentDish)
        .join(
            FrequentDishComponent,
            col(FrequentDishComponent.frequent_dish_id) == col(FrequentDish.id),
        )
        .where(
            FrequentDish.user_id == user_id,
            FrequentDish.nutrition_overridden.is_(False),
            FrequentDishComponent.product_id == product_id,
        )
        .distinct()
    )
    frequent_dishes = list((await session.exec(stmt)).all())
    if not frequent_dishes:
        return

    for fd in frequent_dishes:
        comp_stmt = select(FrequentDishComponent).where(
            FrequentDishComponent.frequent_dish_id == fd.id
        )
        comps = list((await session.exec(comp_stmt)).all())
        prod_ids = list({c.product_id for c in comps})
        p_stmt = select(Product).where(col(Product.id).in_(prod_ids), Product.user_id == user_id)
        prods = {p.id: p for p in (await session.exec(p_stmt)).all()}

        computed = compute_nutrition_per_100g(comps, prods, fd.cooked_weight_g)
        for k in NUTR_FIELDS:
            setattr(fd, k, computed[k])
        fd.updated_at = datetime.now(UTC)
        session.add(fd)

    await session.commit()

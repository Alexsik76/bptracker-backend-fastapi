from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from nutrition.crud.product import create_product
from nutrition.crud.recipe import get_recipe_entity, resolve_recipe_name
from nutrition.models import (
    Dish,
    DishComponent,
    DishComponentRead,
    DishCreate,
    DishRead,
    DishUpdate,
    Product,
    ProductCreate,
    Recipe,
    RecipeProduct,
    StockWarning,
)


def apply_stock_rule(
    needed_weights: dict[UUID, int],
    products_map: dict[UUID, Product],
    session: AsyncSession,
) -> list[StockWarning]:
    """Subtract needed component weights from product stock down to 0.

    Never fail on shortage.
    Products without stock_g are not tracked (no warning).
    Returns list of StockWarning for products with a shortage.
    """
    warnings: list[StockWarning] = []
    for pid, needed in needed_weights.items():
        prod = products_map.get(pid)
        if prod is None:
            continue
        if prod.stock_g is not None:
            if prod.stock_g >= needed:
                prod.stock_g -= needed
            else:
                missing = needed - prod.stock_g
                prod.stock_g = 0
                warnings.append(
                    StockWarning(
                        product_id=prod.id,
                        product_name=prod.name,
                        missing_g=missing,
                    )
                )
            session.add(prod)
    return warnings


def return_stock(
    weights_to_return: dict[UUID, int],
    products_map: dict[UUID, Product],
    session: AsyncSession,
) -> None:
    """Return component weights to product stock."""
    for pid, weight in weights_to_return.items():
        prod = products_map.get(pid)
        if prod is not None and prod.stock_g is not None:
            prod.stock_g += weight
            session.add(prod)


def compute_nutrition_per_100g_tuples(
    components: Sequence[tuple[UUID, int]],
    products_map: dict[UUID, Product],
    cooked_weight_g: int,
) -> dict[str, float | None]:
    """Compute nutrition set per 100g cooked weight from (product_id, weight_g) tuples."""
    if not components or cooked_weight_g <= 0:
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

    for pid, weight_g in components:
        p = products_map.get(pid)
        if p is None:
            has_null_kcal = True
            has_null_protein = True
            has_null_fat = True
            has_null_carbs = True
            has_null_fiber = True
            has_null_sugar = True
            has_null_sodium = True
            continue

        ratio = weight_g / 100.0

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

    scale = 100.0 / cooked_weight_g
    return {
        "kcal": round(total_kcal * scale, 1) if not has_null_kcal else None,
        "protein_g": round(total_protein * scale, 1) if not has_null_protein else None,
        "fat_g": round(total_fat * scale, 1) if not has_null_fat else None,
        "carbs_g": round(total_carbs * scale, 1) if not has_null_carbs else None,
        "fiber_g": round(total_fiber * scale, 1) if not has_null_fiber else None,
        "sugar_g": round(total_sugar * scale, 1) if not has_null_sugar else None,
        "sodium_mg": round(total_sodium * scale, 1) if not has_null_sodium else None,
    }


def compute_nutrition_per_100g(
    components: Sequence[Any],
    products_map: dict[UUID, Product],
    cooked_weight_g: int,
) -> dict[str, float | None]:
    """Compute nutrition set per 100g cooked weight from component objects."""
    tuples = [(c.product_id, c.weight_g) for c in components]
    return compute_nutrition_per_100g_tuples(tuples, products_map, cooked_weight_g)


def compute_dish_nutrition(
    dish: Dish,
    components: Sequence[DishComponent],
    products_map: dict[UUID, Product],
) -> dict[str, float | None]:
    """Compute the nutrition set per 100g of cooked_weight_g for a dish."""
    return compute_nutrition_per_100g(components, products_map, dish.cooked_weight_g)


def _to_dish_read(
    dish: Dish,
    components: Sequence[DishComponent],
    stock_warnings: list[StockWarning] | None = None,
) -> DishRead:
    comp_reads = [DishComponentRead.model_validate(c) for c in components]
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
        stock_warnings=stock_warnings or [],
    )


async def create_dish(
    session: AsyncSession,
    user_id: UUID,
    data: DishCreate,
    settings: Settings | None = None,
) -> DishRead:
    """Create a new dish along with its components, updating tracked stock."""
    if settings is None:
        settings = get_settings()

    # 1. Resolve Recipe
    if data.new_recipe:
        recipe_name = await resolve_recipe_name(session, user_id, data.new_recipe.name)
        recipe = Recipe(user_id=user_id, name=recipe_name, archived=False)
        session.add(recipe)
        await session.flush()
        await session.refresh(recipe)
        recipe_id = recipe.id
    else:
        assert data.recipe_id is not None
        recipe = await get_recipe_entity(session, data.recipe_id, user_id)
        if recipe is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Recipe '{data.recipe_id}' not found",
            )
        recipe_id = recipe.id
        recipe_name = recipe.name

    # 2. Resolve components
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

    # If created via new_recipe, link recipe products
    if data.new_recipe:
        unique_pids = list(dict.fromkeys(pid for pid, _ in resolved_components))
        for pid in unique_pids:
            rp = RecipeProduct(recipe_id=recipe_id, product_id=pid)
            session.add(rp)

    # 3. Fetch products map and apply stock rule
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

    needed_weights: dict[UUID, int] = {}
    for pid, w in resolved_components:
        needed_weights[pid] = needed_weights.get(pid, 0) + w

    warnings = apply_stock_rule(needed_weights, products_map, session)

    # 4. Nutrition: explicit if given, else computed
    computed = compute_nutrition_per_100g_tuples(
        resolved_components, products_map, data.cooked_weight_g
    )
    nutr_fields = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")
    final_nutr: dict[str, float | None] = {}
    for f in nutr_fields:
        if f in data.model_fields_set and getattr(data, f) is not None:
            final_nutr[f] = getattr(data, f)
        else:
            final_nutr[f] = computed[f]

    remaining_g = data.remaining_g if data.remaining_g is not None else data.cooked_weight_g

    dish = Dish(
        user_id=user_id,
        recipe_id=recipe_id,
        name=recipe_name,
        cooked_weight_g=data.cooked_weight_g,
        remaining_g=remaining_g,
        cooked_on=data.cooked_on,
        notes=data.notes,
        kcal=final_nutr["kcal"],
        protein_g=final_nutr["protein_g"],
        fat_g=final_nutr["fat_g"],
        carbs_g=final_nutr["carbs_g"],
        fiber_g=final_nutr["fiber_g"],
        sugar_g=final_nutr["sugar_g"],
        sodium_mg=final_nutr["sodium_mg"],
    )
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

    return _to_dish_read(dish, components, stock_warnings=warnings)


async def get_dishes(
    session: AsyncSession,
    user_id: UUID,
    available: bool | None = None,
) -> list[DishRead]:
    """Return dishes for a user, optionally filtered by available (remaining_g > 0)."""
    statement = select(Dish).where(Dish.user_id == user_id)
    if available:
        statement = statement.where(Dish.remaining_g > 0)
    statement = statement.order_by(col(Dish.name).asc())
    dishes = list((await session.exec(statement)).all())

    if not dishes:
        return []

    dish_ids = [d.id for d in dishes]
    comp_stmt = select(DishComponent).where(col(DishComponent.dish_id).in_(dish_ids))
    comps = list((await session.exec(comp_stmt)).all())

    comps_by_dish: dict[UUID | None, list[DishComponent]] = {d.id: [] for d in dishes}
    for comp in comps:
        comps_by_dish[comp.dish_id].append(comp)

    return [_to_dish_read(dish, comps_by_dish.get(dish.id, [])) for dish in dishes]


async def get_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> DishRead | None:
    """Return a single dish by id with components and stored nutrition."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    comps = list((await session.exec(comp_stmt)).all())

    return _to_dish_read(dish, comps)


async def update_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
    data: DishUpdate,
    settings: Settings | None = None,
) -> DishRead | None:
    """Update a dish and its components, applying the stock rule."""
    if settings is None:
        settings = get_settings()

    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return None

    warnings: list[StockWarning] = []
    nutr_fields = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")

    if data.components is not None:
        old_comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
        old_comps = list((await session.exec(old_comp_stmt)).all())

        old_pids = list({c.product_id for c in old_comps})
        old_products_map: dict[UUID, Product] = {}
        if old_pids:
            p_stmt = select(Product).where(
                col(Product.id).in_(old_pids), Product.user_id == user_id
            )
            for p in (await session.exec(p_stmt)).all():
                old_products_map[p.id] = p

        computed_old = compute_nutrition_per_100g(old_comps, old_products_map, dish.cooked_weight_g)

        # Return old weights
        old_weights: dict[UUID, int] = {}
        for c in old_comps:
            old_weights[c.product_id] = old_weights.get(c.product_id, 0) + c.weight_g
        return_stock(old_weights, old_products_map, session)

        for old in old_comps:
            await session.delete(old)

        # Resolve new components
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

        new_prod_ids = list({pid for pid, _ in resolved_components})
        new_products_map: dict[UUID, Product] = {}
        if new_prod_ids:
            p_stmt = select(Product).where(
                col(Product.id).in_(new_prod_ids), Product.user_id == user_id
            )
            for p in (await session.exec(p_stmt)).all():
                new_products_map[p.id] = p

        new_weights: dict[UUID, int] = {}
        for pid, w in resolved_components:
            new_weights[pid] = new_weights.get(pid, 0) + w

        warnings = apply_stock_rule(new_weights, new_products_map, session)

        eff_cooked_w = (
            data.cooked_weight_g if data.cooked_weight_g is not None else dish.cooked_weight_g
        )
        computed_new = compute_nutrition_per_100g_tuples(
            resolved_components, new_products_map, eff_cooked_w
        )

        for pid, weight in resolved_components:
            comp = DishComponent(dish_id=dish.id, product_id=pid, weight_g=weight)
            session.add(comp)

        # Nutrition delta calculation
        for f in nutr_fields:
            if f in data.model_fields_set and getattr(data, f) is not None:
                setattr(dish, f, getattr(data, f))
            else:
                stored = getattr(dish, f)
                c_old = computed_old[f]
                c_new = computed_new[f]
                if stored is None:
                    setattr(dish, f, c_new)
                else:
                    if c_new is not None and c_old is not None:
                        setattr(dish, f, round(stored + (c_new - c_old), 1))
                    elif c_new is not None and c_old is None:
                        setattr(dish, f, c_new)
                    else:
                        setattr(dish, f, None)
    else:
        # Check explicit nutrition values
        for f in nutr_fields:
            if f in data.model_fields_set:
                setattr(dish, f, getattr(data, f))

    updates = data.model_dump(
        exclude_unset=True,
        exclude={"components", *nutr_fields},
    )
    for field, value in updates.items():
        setattr(dish, field, value)
    dish.updated_at = datetime.now(UTC)
    session.add(dish)

    await session.commit()
    await session.refresh(dish)

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    current_comps = list((await session.exec(comp_stmt)).all())

    return _to_dish_read(dish, current_comps, stock_warnings=warnings)


async def delete_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's dish, returning component weights to stock."""
    statement = select(Dish).where(Dish.id == dish_id, Dish.user_id == user_id)
    dish = (await session.exec(statement)).first()
    if dish is None:
        return False

    comp_stmt = select(DishComponent).where(DishComponent.dish_id == dish_id)
    comps = list((await session.exec(comp_stmt)).all())

    if comps:
        weights: dict[UUID, int] = {}
        for c in comps:
            weights[c.product_id] = weights.get(c.product_id, 0) + c.weight_g

        p_stmt = select(Product).where(
            col(Product.id).in_(list(weights.keys())), Product.user_id == user_id
        )
        products_map = {p.id: p for p in (await session.exec(p_stmt)).all()}
        return_stock(weights, products_map, session)

    await session.delete(dish)
    await session.commit()
    return True

import logging
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings, get_settings
from nutrition.crud.product import create_product
from nutrition.models import (
    Dish,
    DishComponent,
    DishComponentRead,
    DishCreate,
    DishRead,
    DishUpdate,
    MealType,
    NutritionEstimate,
    Product,
    ProductCreate,
)

logger = logging.getLogger(__name__)

NUTR_FIELDS = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g", "sodium_mg")


async def get_dish_entity(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> Dish | None:
    statement = select(Dish).where(
        Dish.id == dish_id,
        Dish.user_id == user_id,
    )
    return (await session.exec(statement)).first()


async def get_dish_components(
    session: AsyncSession,
    dish_id: UUID,
) -> list[DishComponentRead]:
    statement = (
        select(DishComponent)
        .where(DishComponent.dish_id == dish_id)
        .order_by(DishComponent.id)
    )
    results = (await session.exec(statement)).all()
    return [
        DishComponentRead(
            id=c.id,
            dish_id=c.dish_id,
            product_id=c.product_id,
            weight_g=c.weight_g,
        )
        for c in results
    ]


async def get_dish_composition(
    session: AsyncSession,
    dish_id: UUID,
) -> list[tuple[str, int]]:
    statement = (
        select(Product.name, DishComponent.weight_g)
        .join(Product, DishComponent.product_id == Product.id)
        .where(DishComponent.dish_id == dish_id)
        .order_by(DishComponent.id)
    )
    results = (await session.exec(statement)).all()
    return [(name, weight_g) for name, weight_g in results]


def _apply_nutrition_estimate(dish: Dish, est: NutritionEstimate) -> None:
    dish.kcal = est.kcal
    dish.protein_g = est.protein_g
    dish.fat_g = est.fat_g
    dish.carbs_g = est.carbs_g
    dish.fiber_g = est.fiber_g
    dish.sugar_g = est.sugar_g
    dish.sodium_mg = est.sodium_mg


def to_dish_read(dish: Dish, components: list[DishComponentRead] | None = None) -> DishRead:
    return DishRead(
        id=dish.id,
        user_id=dish.user_id,
        name=dish.name,
        is_ready=dish.is_ready,
        pinned_meal_type=dish.pinned_meal_type,
        nutrition_overridden=dish.nutrition_overridden,
        note=dish.note,
        kcal=dish.kcal,
        protein_g=dish.protein_g,
        fat_g=dish.fat_g,
        carbs_g=dish.carbs_g,
        fiber_g=dish.fiber_g,
        sugar_g=dish.sugar_g,
        sodium_mg=dish.sodium_mg,
        created_at=dish.created_at,
        updated_at=dish.updated_at,
        components=components or [],
    )


async def create_dish(
    session: AsyncSession,
    user_id: UUID,
    data: DishCreate,
    settings: Settings | None = None,
) -> DishRead:
    if settings is None:
        settings = get_settings()

    user_provided_nutrition = any(
        getattr(data, f) is not None for f in NUTR_FIELDS
    )
    nutrition_overridden = data.nutrition_overridden or user_provided_nutrition

    dish = Dish(
        user_id=user_id,
        name=data.name.strip(),
        is_ready=data.is_ready,
        pinned_meal_type=data.pinned_meal_type,
        nutrition_overridden=nutrition_overridden,
        note=data.note,
        kcal=data.kcal,
        protein_g=data.protein_g,
        fat_g=data.fat_g,
        carbs_g=data.carbs_g,
        fiber_g=data.fiber_g,
        sugar_g=data.sugar_g,
        sodium_mg=data.sodium_mg,
    )
    session.add(dish)
    await session.commit()
    await session.refresh(dish)

    # Process components if any
    created_components: list[DishComponentRead] = []
    if data.components:
        for comp in data.components:
            product_id = comp.product_id
            if comp.new_product:
                new_prod = await create_product(
                    session,
                    user_id,
                    ProductCreate(
                        name=comp.new_product.name,
                        available=comp.new_product.available,
                        note=comp.new_product.note,
                    ),
                )
                product_id = new_prod.id
            dc = DishComponent(dish_id=dish.id, product_id=product_id, weight_g=comp.weight_g)
            session.add(dc)
            await session.commit()
            await session.refresh(dc)
            created_components.append(
                DishComponentRead(
                    id=dc.id,
                    dish_id=dc.dish_id,
                    product_id=dc.product_id,
                    weight_g=dc.weight_g,
                )
            )

    # If nutrition is not provided, fill with estimator after components are created
    if not user_provided_nutrition:
        from nutrition.service.estimator import estimate_dish_nutrition

        composition = await get_dish_composition(session, dish.id)
        try:
            est = await estimate_dish_nutrition(
                session=session,
                user_id=user_id,
                dish_name=dish.name,
                settings=settings,
                composition=composition or None,
                raise_on_error=False,
            )
            if est:
                _apply_nutrition_estimate(dish, est)
                session.add(dish)
                await session.commit()
                await session.refresh(dish)
        except Exception as exc:
            logger.warning("Dish nutrition estimation failed for '%s': %s", dish.name, exc)

    return to_dish_read(dish, created_components)


async def get_dishes(
    session: AsyncSession,
    user_id: UUID,
    is_ready: bool | None = None,
    pinned_meal_type: MealType | None = None,
) -> Sequence[DishRead]:
    statement = select(Dish).where(Dish.user_id == user_id)
    if is_ready is not None:
        statement = statement.where(Dish.is_ready.is_(is_ready))
    if pinned_meal_type is not None:
        statement = statement.where(Dish.pinned_meal_type == pinned_meal_type)
    statement = statement.order_by(col(Dish.name).asc())
    dishes = list((await session.exec(statement)).all())

    result: list[DishRead] = []
    for d in dishes:
        components = await get_dish_components(session, d.id)
        result.append(to_dish_read(d, components))
    return result


async def get_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> DishRead | None:
    dish = await get_dish_entity(session, dish_id, user_id)
    if dish is None:
        return None
    components = await get_dish_components(session, dish.id)
    return to_dish_read(dish, components)


async def update_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
    data: DishUpdate,
    settings: Settings | None = None,
) -> DishRead | None:
    dish = await get_dish_entity(session, dish_id, user_id)
    if dish is None:
        return None

    if settings is None:
        settings = get_settings()

    reset_nutrition = data.reset_nutrition
    updates = data.model_dump(exclude_unset=True)
    updates.pop("reset_nutrition", None)

    nutrition_updated = any(f in updates for f in NUTR_FIELDS)
    components_present = "components" in updates
    components_data = updates.pop("components", None)

    if reset_nutrition:
        dish.nutrition_overridden = False
        updates.pop("nutrition_overridden", None)
        for f in NUTR_FIELDS:
            updates.pop(f, None)
    elif nutrition_updated:
        dish.nutrition_overridden = True

    for field, value in updates.items():
        if field == "name" and isinstance(value, str):
            value = value.strip()
        setattr(dish, field, value)

    dish.updated_at = datetime.now(UTC)
    session.add(dish)
    await session.commit()
    await session.refresh(dish)

    composition_changed = False
    if components_present:
        old_comps = (
            await session.exec(select(DishComponent).where(DishComponent.dish_id == dish_id))
        ).all()
        has_new_product = any(
            comp_dict.get("new_product") is not None
            for comp_dict in (components_data or [])
        )
        if has_new_product:
            composition_changed = True
        else:
            old_multiset = Counter(
                (str(c.product_id), c.weight_g) for c in old_comps
            )
            new_multiset = Counter(
                (str(c["product_id"]), c["weight_g"]) for c in (components_data or [])
            )
            composition_changed = old_multiset != new_multiset

        if composition_changed:
            # Replace components
            for oc in old_comps:
                await session.delete(oc)
            await session.commit()

            if components_data:
                for comp_dict in components_data:
                    pid = comp_dict.get("product_id")
                    new_p = comp_dict.get("new_product")
                    if new_p:
                        new_prod = await create_product(
                            session,
                            user_id,
                            ProductCreate(
                                name=new_p["name"],
                                available=new_p.get("available", True),
                                note=new_p.get("note"),
                            ),
                        )
                        pid = new_prod.id
                    dc = DishComponent(
                        dish_id=dish.id, product_id=pid, weight_g=comp_dict["weight_g"]
                    )
                    session.add(dc)
                await session.commit()

    should_reestimate = False
    if reset_nutrition:
        should_reestimate = True
    elif (
        components_present
        and composition_changed
        and not nutrition_updated
        and not dish.nutrition_overridden
    ):
        should_reestimate = True

    if should_reestimate:
        from nutrition.service.estimator import estimate_dish_nutrition

        composition = await get_dish_composition(session, dish.id)
        try:
            est = await estimate_dish_nutrition(
                session=session,
                user_id=user_id,
                dish_name=dish.name,
                settings=settings,
                composition=composition or None,
                raise_on_error=False,
            )
            if est:
                _apply_nutrition_estimate(dish, est)
                session.add(dish)
                await session.commit()
                await session.refresh(dish)
        except Exception as exc:
            logger.warning("Dish nutrition re-estimation failed for '%s': %s", dish.name, exc)

    components = await get_dish_components(session, dish.id)
    return to_dish_read(dish, components)


async def delete_dish(
    session: AsyncSession,
    dish_id: UUID,
    user_id: UUID,
) -> bool:
    dish = await get_dish_entity(session, dish_id, user_id)
    if dish is None:
        return False
    await session.delete(dish)
    await session.commit()
    return True

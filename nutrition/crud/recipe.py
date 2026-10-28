from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import (
    Dish,
    FrequentDish,
    Product,
    Recipe,
    RecipeCreate,
    RecipeProduct,
    RecipeRead,
    RecipeUpdate,
)


async def resolve_recipe_name(session: AsyncSession, user_id: UUID, base_name: str) -> str:
    """Resolve a unique recipe name for the user, auto-suffixing ' 2', ' 3', etc."""
    base_name = base_name.strip()
    stmt = select(Recipe.name).where(
        Recipe.user_id == user_id,
        Recipe.name.like(f"{base_name}%"),
    )
    existing_names = set((await session.exec(stmt)).all())
    if base_name not in existing_names:
        return base_name

    counter = 2
    while f"{base_name} {counter}" in existing_names:
        counter += 1
    return f"{base_name} {counter}"


async def get_recipe_entity(
    session: AsyncSession,
    recipe_id: UUID,
    user_id: UUID,
) -> Recipe | None:
    stmt = select(Recipe).where(Recipe.id == recipe_id, Recipe.user_id == user_id)
    return (await session.exec(stmt)).first()


async def create_recipe(
    session: AsyncSession,
    user_id: UUID,
    data: RecipeCreate,
) -> RecipeRead:
    """Create a new recipe with its product links."""
    name = await resolve_recipe_name(session, user_id, data.name)
    recipe = Recipe(user_id=user_id, name=name, archived=False)
    session.add(recipe)
    await session.flush()
    await session.refresh(recipe)

    # Verify and add products if any
    unique_pids = list(dict.fromkeys(data.product_ids))
    if unique_pids:
        p_stmt = select(Product.id).where(
            col(Product.id).in_(unique_pids), Product.user_id == user_id
        )
        existing_pids = set((await session.exec(p_stmt)).all())
        for pid in unique_pids:
            if pid not in existing_pids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product '{pid}' not found",
                )
            rp = RecipeProduct(recipe_id=recipe.id, product_id=pid)
            session.add(rp)

    await session.commit()
    await session.refresh(recipe)

    return RecipeRead(
        id=recipe.id,
        user_id=recipe.user_id,
        name=recipe.name,
        archived=recipe.archived,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
        product_ids=unique_pids,
    )


async def get_recipes(
    session: AsyncSession,
    user_id: UUID,
    include_archived: bool = False,
) -> list[RecipeRead]:
    """Return all recipes for user, hiding archived ones by default."""
    stmt = select(Recipe).where(Recipe.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(Recipe.archived.is_(False))
    stmt = stmt.order_by(col(Recipe.name).asc())
    recipes = list((await session.exec(stmt)).all())
    if not recipes:
        return []

    recipe_ids = [r.id for r in recipes]
    rp_stmt = select(RecipeProduct).where(col(RecipeProduct.recipe_id).in_(recipe_ids))
    rps = list((await session.exec(rp_stmt)).all())
    pids_by_recipe: dict[UUID, list[UUID]] = {r.id: [] for r in recipes}
    for rp in rps:
        pids_by_recipe[rp.recipe_id].append(rp.product_id)

    return [
        RecipeRead(
            id=r.id,
            user_id=r.user_id,
            name=r.name,
            archived=r.archived,
            created_at=r.created_at,
            updated_at=r.updated_at,
            product_ids=pids_by_recipe.get(r.id, []),
        )
        for r in recipes
    ]


async def get_recipe(
    session: AsyncSession,
    recipe_id: UUID,
    user_id: UUID,
) -> RecipeRead | None:
    """Return single recipe by ID."""
    recipe = await get_recipe_entity(session, recipe_id, user_id)
    if recipe is None:
        return None

    rp_stmt = select(RecipeProduct.product_id).where(RecipeProduct.recipe_id == recipe_id)
    product_ids = list((await session.exec(rp_stmt)).all())

    return RecipeRead(
        id=recipe.id,
        user_id=recipe.user_id,
        name=recipe.name,
        archived=recipe.archived,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
        product_ids=product_ids,
    )


async def update_recipe(
    session: AsyncSession,
    recipe_id: UUID,
    user_id: UUID,
    data: RecipeUpdate,
) -> RecipeRead | None:
    """Update recipe name and/or archived status. Composition is immutable."""
    recipe = await get_recipe_entity(session, recipe_id, user_id)
    if recipe is None:
        return None

    if data.name is not None:
        new_name = data.name.strip()
        if new_name != recipe.name:
            dup_stmt = select(Recipe).where(
                Recipe.user_id == user_id,
                Recipe.name == new_name,
                Recipe.id != recipe_id,
            )
            if (await session.exec(dup_stmt)).first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Recipe with name '{new_name}' already exists",
                )
            recipe.name = new_name

    if data.archived is not None:
        recipe.archived = data.archived

    recipe.updated_at = datetime.now(UTC)
    session.add(recipe)
    await session.commit()
    await session.refresh(recipe)

    return await get_recipe(session, recipe_id, user_id)


async def delete_recipe(
    session: AsyncSession,
    recipe_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete recipe. 409 if any dish or frequent dish references it."""
    recipe = await get_recipe_entity(session, recipe_id, user_id)
    if recipe is None:
        return False

    dish_ref_stmt = (
        select(func.count()).select_from(Dish).where(Dish.recipe_id == recipe_id)
    )
    dish_count = (await session.exec(dish_ref_stmt)).one()

    freq_ref_stmt = (
        select(func.count()).select_from(FrequentDish).where(FrequentDish.recipe_id == recipe_id)
    )
    freq_count = (await session.exec(freq_ref_stmt)).one()

    if dish_count > 0 or freq_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete recipe referenced by dishes or frequent dishes",
        )

    await session.delete(recipe)
    await session.commit()
    return True

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import RecipeCreate, RecipeRead, RecipeUpdate

router = APIRouter(prefix="/recipes")


@router.post("", response_model=RecipeRead, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    data: RecipeCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> RecipeRead:
    return await crud.create_recipe(session, user_id, data)


@router.get("", response_model=list[RecipeRead])
async def list_recipes(
    session: SessionDep,
    user_id: CurrentUserId,
    include_archived: bool = False,
) -> list[RecipeRead]:
    return await crud.get_recipes(session, user_id, include_archived=include_archived)


@router.get("/{recipe_id}", response_model=RecipeRead)
async def get_recipe(
    recipe_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> RecipeRead:
    recipe = await crud.get_recipe(session, recipe_id, user_id)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )
    return recipe


@router.patch("/{recipe_id}", response_model=RecipeRead)
async def update_recipe(
    recipe_id: UUID,
    data: RecipeUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> RecipeRead:
    recipe = await crud.update_recipe(session, recipe_id, user_id, data)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )
    return recipe


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_recipe(session, recipe_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )

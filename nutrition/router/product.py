from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import Product, ProductCreate, ProductRead, ProductUpdate

router = APIRouter(prefix="/products")


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Product:
    return await crud.create_product(session, user_id, data)


@router.get("", response_model=Sequence[ProductRead])
async def list_products(
    session: SessionDep,
    user_id: CurrentUserId,
) -> Sequence[Product]:
    return await crud.get_products(session, user_id)


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Product:
    product = await crud.get_product(session, product_id, user_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Product:
    product = await crud.update_product(session, product_id, user_id, data)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_product(session, product_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

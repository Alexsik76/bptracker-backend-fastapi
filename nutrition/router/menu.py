from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from nutrition import crud
from nutrition.models import (
    GenerationRequest,
    GenerationResultRead,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuNoteCreate,
    MenuNoteRead,
    MenuRead,
    MenuUpdate,
)
from nutrition.service.generator import (
    ProfileNotFoundError,
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    generate_menu,
)
from nutrition.service.notes import process_menu_note

menus_router = APIRouter(prefix="/menus")
items_router = APIRouter(prefix="/menu-items")


@menus_router.get("/{menu_date}", response_model=list[MenuRead])
async def get_day_menus(
    menu_date: date,
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[MenuRead]:
    menus = await crud.get_day_menus(session, user_id, menu_date)
    if not menus:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu not found",
        )
    return menus


@menus_router.patch("/{menu_id}", response_model=MenuRead)
async def update_menu(
    menu_id: UUID,
    data: MenuUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    updated = await crud.update_menu(session, menu_id, user_id, data)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu not found",
        )
    return updated


@menus_router.post("/{menu_date}/generate", response_model=GenerationResultRead)
async def generate_menu_items(
    menu_date: date,
    data: GenerationRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> GenerationResultRead:
    try:
        return await generate_menu(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope=data.scope,
            slot=data.slot,
            settings=settings,
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini service is unavailable",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@menus_router.post(
    "/{menu_date}/notes",
    response_model=MenuNoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_menu_note(
    menu_date: date,
    data: MenuNoteCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuNoteRead:
    try:
        return await process_menu_note(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            data=data,
            settings=settings,
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini service is unavailable",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@menus_router.post(
    "/{menu_date}/items",
    response_model=MenuItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_menu_item(
    menu_date: date,
    slot: str,
    data: MenuItemCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuItemRead:
    try:
        return await crud.add_menu_item(session, user_id, menu_date, slot, data)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0]),
        ) from exc


@items_router.patch("/{item_id}", response_model=MenuItemRead)
async def update_menu_item(
    item_id: UUID,
    data: MenuItemUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuItemRead:
    try:
        updated = await crud.update_menu_item(session, item_id, user_id, data)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0]),
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    return updated


@items_router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu_item(
    item_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_menu_item(session, item_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )

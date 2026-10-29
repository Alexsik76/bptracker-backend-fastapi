from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from config import get_settings
from db import SessionDep
from users import crud
from users.models import UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> UserRead:
    user = await crud.get_user(session, current_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    settings = get_settings()
    return UserRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        is_admin=user.email.lower() in settings.admin_emails,
    )


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> UserRead:
    user = await crud.update_display_name(session, current_user_id, payload.display_name)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    settings = get_settings()
    return UserRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        is_admin=user.email.lower() in settings.admin_emails,
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> None:
    deleted = await crud.delete_user(session, current_user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from users import crud
from users.models import User, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> User:
    user = await crud.get_user(session, current_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> User:
    user = await crud.update_display_name(session, current_user_id, payload.display_name)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


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

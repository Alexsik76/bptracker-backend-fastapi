from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import EmailStr, field_validator
from sqlmodel import Field, SQLModel, select

from auth.deps import CurrentUserId
from auth.models import User
from db import SessionDep

router = APIRouter(prefix="/users", tags=["users"])


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    display_name: str | None = None
    created_at: datetime


class UserUpdate(SQLModel):
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", mode="before")
    @classmethod
    def sanitize_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> User:
    statement = select(User).where(User.id == current_user_id)
    result = await session.exec(statement)
    user = result.first()
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
    statement = select(User).where(User.id == current_user_id)
    result = await session.exec(statement)
    user = result.first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.display_name = payload.display_name
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user_id: CurrentUserId,
    session: SessionDep,
) -> None:
    statement = select(User).where(User.id == current_user_id)
    result = await session.exec(statement)
    user = result.first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Delete magic link associated with user's email if it exists
    # magic_links is keyed by email (not user_id), so it is not cascading.
    from auth.models import MagicLink

    link_statement = select(MagicLink).where(MagicLink.email == user.email)
    link_result = await session.exec(link_statement)
    link = link_result.first()
    if link:
        await session.delete(link)

    # Delete the user. All child rows referencing users.id will be deleted
    # automatically via ON DELETE CASCADE in the database.
    await session.delete(user)
    await session.commit()

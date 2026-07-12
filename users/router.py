from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import EmailStr
from sqlmodel import SQLModel, select

from auth.deps import CurrentUserId
from auth.models import User
from db import SessionDep

router = APIRouter(prefix="/users", tags=["users"])


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    timezone: str | None
    created_at: datetime


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

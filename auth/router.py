from fastapi import APIRouter, HTTPException, status
from pydantic import EmailStr
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from auth import crud
from auth.models import TokenResponse, UserCreate
from auth.security import create_access_token, verify_password_or_dummy
from db import SessionDep

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(SQLModel):
    email: EmailStr
    password: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: SessionDep) -> TokenResponse:
    # Auto-login on register: a fresh account gets a token immediately.
    try:
        user = await crud.create_user(session, data)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: SessionDep) -> TokenResponse:
    user = await crud.get_user_by_email(session, data.email)
    # One bcrypt comparison runs regardless of whether the email exists, so login
    # response time doesn't reveal which emails are registered. Same 401 message
    # for unknown email and wrong password.
    ok = verify_password_or_dummy(data.password, user.password_hash if user else None)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    return TokenResponse(access_token=create_access_token(user.id))

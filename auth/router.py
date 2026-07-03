from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import EmailStr
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from auth import crud
from auth.models import TokenResponse, UserCreate
from auth.security import (
    create_access_token,
    generate_magic_token,
    hash_magic_token,
    verify_password_or_dummy,
)
from config import get_settings
from db import SessionDep
from email_infra import EmailSender, get_email_sender

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class LoginRequest(SQLModel):
    email: EmailStr
    password: str


class MagicLinkRequest(SQLModel):
    email: EmailStr


class MagicLinkConfirm(SQLModel):
    token: str


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


@router.post("/magic-link/request", status_code=status.HTTP_202_ACCEPTED)
async def request_magic_link(
    data: MagicLinkRequest,
    session: SessionDep,
    email_sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> dict[str, str]:
    user = await crud.get_user_by_email(session, data.email)
    if user:
        raw_token = generate_magic_token()
        token_hash = hash_magic_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.magic_link_ttl_minutes)

        await crud.upsert_magic_link(
            session=session,
            email=data.email,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        magic_link = f"{settings.magic_link_base_url}?token={raw_token}"

        subject = "Your Magic Login Link"
        text = (
            f"Hello,\n\n"
            f"Use the link below to log in to your account:\n"
            f"{magic_link}\n\n"
            f"This link will expire in {settings.magic_link_ttl_minutes} minutes."
        )
        html = (
            f"<p>Hello,</p>"
            f"<p>Use the link below to log in to your account:</p>"
            f'<p><a href="{magic_link}">Log in to BP Tracker</a></p>'
            f"<p>This link will expire in {settings.magic_link_ttl_minutes} minutes.</p>"
        )

        await email_sender.send(
            to=data.email,
            subject=subject,
            text=text,
            html=html,
        )

    return {"detail": "If the address is registered, a link has been sent"}


@router.post("/magic-link/confirm", response_model=TokenResponse)
async def confirm_magic_link(
    data: MagicLinkConfirm,
    session: SessionDep,
) -> TokenResponse:
    token_hash = hash_magic_token(data.token)
    link = await crud.get_magic_link_by_hash(session, token_hash)

    if not link:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect or expired token",
        )

    if link.expires_at < datetime.now(UTC):
        await crud.delete_magic_link(session, link)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect or expired token",
        )

    user = await crud.get_user_by_email(session, link.email)
    if not user:
        await crud.delete_magic_link(session, link)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect or expired token",
        )

    await crud.delete_magic_link(session, link)

    return TokenResponse(access_token=create_access_token(user.id))

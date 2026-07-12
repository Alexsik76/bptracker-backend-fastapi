from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from auth import crud, service
from auth.deps import CurrentUserId
from auth.models import NormalizedEmail, SessionRead, TokenResponse, UserCreate
from auth.security import (
    generate_magic_token,
    hash_token,
    verify_password_or_dummy,
)
from config import get_settings
from db import SessionDep
from email_infra import EmailSender, get_email_sender

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class LoginRequest(SQLModel):
    email: NormalizedEmail
    password: str


class MagicLinkRequest(SQLModel):
    email: NormalizedEmail


class MagicLinkConfirm(SQLModel):
    token: str


class RefreshRequest(SQLModel):
    refresh_token: str


class LogoutRequest(SQLModel):
    refresh_token: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    # Auto-login on register: a fresh account gets a token immediately.
    try:
        user = await crud.create_user(session, data)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    token_pair = await service.issue_session(session, user_id=user.id, user_agent=user_agent)
    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        expires_in=token_pair.expires_in,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    user = await crud.get_user_by_email(session, data.email)
    # One bcrypt comparison runs regardless of whether the email exists, so login
    # response time doesn't reveal which emails are registered. Same 401 message
    # for unknown email and wrong password.
    ok = verify_password_or_dummy(data.password, user.password_hash if user else None)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    token_pair = await service.issue_session(session, user_id=user.id, user_agent=user_agent)
    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        expires_in=token_pair.expires_in,
    )


@router.post("/magic-link/request", status_code=status.HTTP_202_ACCEPTED)
async def request_magic_link(
    data: MagicLinkRequest,
    session: SessionDep,
    email_sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> dict[str, str]:
    user = await crud.get_user_by_email(session, data.email)
    if user:
        raw_token = generate_magic_token()
        token_hash = hash_token(raw_token)
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
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    token_hash = hash_token(data.token)
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

    token_pair = await service.issue_session(session, user_id=user.id, user_agent=user_agent)
    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        expires_in=token_pair.expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshRequest,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    try:
        token_pair = await service.rotate_session(
            session,
            raw_token=data.refresh_token,
            user_agent=user_agent,
        )
        return TokenResponse(
            access_token=token_pair.access_token,
            refresh_token=token_pair.refresh_token,
            expires_in=token_pair.expires_in,
        )
    except service.InvalidOrExpiredTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutRequest,
    session: SessionDep,
) -> None:
    await service.revoke_session(session, raw_token=data.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    session: SessionDep,
    current_user_id: CurrentUserId,
) -> None:
    await service.revoke_all_user_sessions(session, user_id=current_user_id)


@router.get("/sessions", response_model=list[SessionRead])
async def get_sessions(
    session: SessionDep,
    current_user_id: CurrentUserId,
) -> list[SessionRead]:
    sessions = await service.list_active_sessions(session, user_id=current_user_id)
    return [
        SessionRead(
            id=s.id,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            expires_at=s.expires_at,
            user_agent=s.user_agent,
        )
        for s in sessions
    ]

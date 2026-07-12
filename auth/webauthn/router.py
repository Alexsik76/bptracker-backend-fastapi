from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response, status

from auth import service as auth_service
from auth.deps import CurrentUserId
from auth.models import TokenResponse, User
from auth.webauthn import service
from auth.webauthn.crud import delete_credential, list_credentials_by_user
from auth.webauthn.models import WebAuthnCredential, WebAuthnCredentialRead
from config import get_settings
from db import SessionDep

router = APIRouter(prefix="/auth/webauthn", tags=["auth"])
settings = get_settings()


@router.post("/register/options", status_code=status.HTTP_200_OK)
async def register_options(session: SessionDep, user_id: CurrentUserId) -> Response:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    options_json = await service.start_registration(
        session,
        user_id=user_id,
        email=user.email,
        settings=settings,
    )
    return Response(content=options_json, media_type="application/json")


@router.post(
    "/register/verify", response_model=WebAuthnCredentialRead, status_code=status.HTTP_201_CREATED
)
async def register_verify(
    session: SessionDep, user_id: CurrentUserId, body: dict
) -> WebAuthnCredential:
    try:
        credential = await service.finish_registration(
            session,
            user_id=user_id,
            body=body,
            settings=settings,
        )
        return credential
    except service.CeremonyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Registration failed"
        ) from exc


@router.post("/authenticate/options", status_code=status.HTTP_200_OK)
async def authenticate_options(session: SessionDep) -> Response:
    options_json = await service.start_authentication(
        session,
        settings=settings,
    )
    return Response(content=options_json, media_type="application/json")


@router.post("/authenticate/verify", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def authenticate_verify(
    session: SessionDep,
    body: dict,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    try:
        authenticated_user_id = await service.finish_authentication(
            session,
            body=body,
            settings=settings,
        )
    except service.CeremonyError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials"
        ) from exc

    token_pair = await auth_service.issue_session(
        session, user_id=authenticated_user_id, user_agent=user_agent
    )
    return TokenResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        expires_in=token_pair.expires_in,
    )


@router.get(
    "/credentials", response_model=list[WebAuthnCredentialRead], status_code=status.HTTP_200_OK
)
async def get_credentials(session: SessionDep, user_id: CurrentUserId) -> list[WebAuthnCredential]:
    return await list_credentials_by_user(session, user_id)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_credential(
    session: SessionDep, user_id: CurrentUserId, credential_id: UUID
) -> Response:
    cred = await session.get(WebAuthnCredential, credential_id)
    if not cred or cred.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    await delete_credential(session, cred)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

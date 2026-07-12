from fastapi import APIRouter, HTTPException, Response, status

from auth.deps import CurrentUserId
from auth.models import TokenResponse, User
from auth.security import create_access_token
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
    try:
        options_json = await service.start_registration(
            session,
            user_id=user_id,
            email=user.email,
            settings=settings,
        )
    except service.CeremonyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
    try:
        options_json = await service.start_authentication(
            session,
            settings=settings,
        )
    except service.CeremonyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return Response(content=options_json, media_type="application/json")


@router.post("/authenticate/verify", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def authenticate_verify(session: SessionDep, body: dict) -> TokenResponse:
    try:
        authenticated_user_id = await service.finish_authentication(
            session,
            body=body,
            settings=settings,
        )
        return TokenResponse(access_token=create_access_token(authenticated_user_id))
    except service.CeremonyError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials"
        ) from exc


@router.get(
    "/credentials", response_model=list[WebAuthnCredentialRead], status_code=status.HTTP_200_OK
)
async def get_credentials(session: SessionDep, user_id: CurrentUserId) -> list[WebAuthnCredential]:
    return await list_credentials_by_user(session, user_id)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_credential(
    session: SessionDep, user_id: CurrentUserId, credential_id: str
) -> Response:
    try:
        from uuid import UUID

        cred_uuid = UUID(credential_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found"
        ) from None

    cred = await session.get(WebAuthnCredential, cred_uuid)
    if not cred or cred.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    await delete_credential(session, cred)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

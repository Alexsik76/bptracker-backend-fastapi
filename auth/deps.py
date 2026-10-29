from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.security import InvalidTokenError, decode_access_token
from config import get_settings
from db import SessionDep
from users import crud as users_crud

# Bearer scheme: tokens are obtained via magic link confirmation, refresh, or WebAuthn.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> UUID:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Stateless by design: decoding the token is sufficient, no DB lookup here.
    # DB-existence checks (e.g. for deleted/revoked users) are a possible
    # future addition, deliberately not added now.
    try:
        return decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]


async def get_current_admin_id(
    user_id: CurrentUserId,
    session: SessionDep,
) -> UUID:
    user = await users_crud.get_user(session, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    if user.email.lower() not in settings.admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return user_id


CurrentAdminId = Annotated[UUID, Depends(get_current_admin_id)]

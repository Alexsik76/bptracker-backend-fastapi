from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from auth.security import InvalidTokenError, decode_access_token

# tokenUrl is configured to dummy value because direct token login is passwordless.
# Extraction here just reads the Bearer header regardless of how the token was obtained.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def get_current_user_id(token: Annotated[str, Depends(oauth2_scheme)]) -> UUID:
    # Stateless by design: decoding the token is sufficient, no DB lookup here.
    # DB-existence checks (e.g. for deleted/revoked users) are a possible
    # future addition, deliberately not added now.
    try:
        return decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]

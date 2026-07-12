import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

from config import get_settings

settings = get_settings()


class InvalidTokenError(ValueError):
    """Raised when a bearer token is malformed, has a bad signature, or is expired."""


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes of the input; longer passwords are
    # truncated silently by the algorithm itself. Not enforced here.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# Precomputed bcrypt hash of a throwaway value. Used so that login spends the same
# time hashing whether or not the account exists, preventing timing-based user
# enumeration (a missing user must cost the same as a wrong password).
_DUMMY_HASH = bcrypt.hashpw(b"timing-attack-dummy", bcrypt.gensalt()).decode()


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Constant-work password check for login.

    Always runs one bcrypt comparison. When `password_hash` is None (unknown email
    or passkey-only account), it compares against a dummy hash and returns False —
    same cost as a real check, so response time doesn't leak account existence.
    """
    return bcrypt.checkpw(password.encode(), (password_hash or _DUMMY_HASH).encode())


def create_access_token(user_id: UUID) -> str:
    """Issue a bearer token for a user_id. Takes only user_id — no password/login-method
    knowledge, so passkey/magic-link auth can reuse this unchanged.
    """
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> UUID:
    """Decode a bearer token and return its user_id. Raises InvalidTokenError on any
    signature, format, or expiry problem.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as exc:
        raise InvalidTokenError("Invalid or expired token") from exc
    return UUID(payload["sub"])


def generate_magic_token() -> str:
    return secrets.token_urlsafe(settings.magic_link_token_bytes)


def hash_magic_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_magic_token(token)

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from auth.deps import CurrentUserId
from auth.security import InvalidTokenError, create_access_token, decode_access_token
from config import get_settings


def test_create_and_decode_access_token_roundtrip():
    user_id = uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_decode_tampered_token_raises():
    token = create_access_token(uuid4())
    # Flip a character inside the signature segment (not the very last one — its
    # trailing bits can be base64-redundant and decode to the same bytes).
    tampered = token[:-6] + ("x" if token[-6] != "x" else "y") + token[-5:]
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_decode_expired_token_raises():
    settings = get_settings()
    expired_payload = {"sub": str(uuid4()), "exp": datetime.now(UTC) - timedelta(minutes=1)}
    expired_token = jwt.encode(
        expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired_token)


@pytest.mark.asyncio
async def test_get_current_user_id_dependency_end_to_end():
    # A throwaway probe app, independent of the main app/DB — this dependency
    # is stateless (decodes the token only), so no session/DB setup is needed.
    probe_app = FastAPI()

    @probe_app.get("/whoami")
    async def whoami(user_id: CurrentUserId):
        return {"user_id": str(user_id)}

    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        user_id = uuid4()
        token = create_access_token(user_id)

        valid = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert valid.status_code == 200
        assert valid.json()["user_id"] == str(user_id)

        missing = await c.get("/whoami")
        assert missing.status_code == 401

        invalid = await c.get("/whoami", headers={"Authorization": "Bearer garbage"})
        assert invalid.status_code == 401

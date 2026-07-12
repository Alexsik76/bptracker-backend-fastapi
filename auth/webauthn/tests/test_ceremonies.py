import copy
from datetime import UTC, datetime, timedelta

import pytest
from soft_webauthn import SoftWebauthnDevice
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from webauthn import base64url_to_bytes
from webauthn.helpers import bytes_to_base64url

from auth.deps import get_current_user_id
from auth.security import decode_access_token
from auth.webauthn.models import WebAuthnChallenge, WebAuthnCredential
from main import app


def prepare_options_for_soft_webauthn(options: dict) -> dict:
    import copy

    pub = copy.deepcopy(options)
    if "challenge" in pub and isinstance(pub["challenge"], str):
        pub["challenge"] = base64url_to_bytes(pub["challenge"])
    if "user" in pub and "id" in pub["user"] and isinstance(pub["user"]["id"], str):
        pub["user"]["id"] = base64url_to_bytes(pub["user"]["id"])
    if "excludeCredentials" in pub:
        for cred in pub["excludeCredentials"]:
            if "id" in cred and isinstance(cred["id"], str):
                cred["id"] = base64url_to_bytes(cred["id"])
    if "allowCredentials" in pub:
        for cred in pub["allowCredentials"]:
            if "id" in cred and isinstance(cred["id"], str):
                cred["id"] = base64url_to_bytes(cred["id"])
    return {"publicKey": pub}


def make_registration_verify_body(device_response: dict, label: str = "My Key") -> dict:
    return {
        "id": bytes_to_base64url(device_response["rawId"]),
        "rawId": bytes_to_base64url(device_response["rawId"]),
        "type": "public-key",
        "label": label,
        "response": {
            "clientDataJSON": bytes_to_base64url(device_response["response"]["clientDataJSON"]),
            "attestationObject": bytes_to_base64url(
                device_response["response"]["attestationObject"]
            ),
            "transports": ["internal", "hybrid"],
        },
    }


def make_authentication_verify_body(device_response: dict) -> dict:
    return {
        "id": bytes_to_base64url(device_response["rawId"]),
        "rawId": bytes_to_base64url(device_response["rawId"]),
        "type": "public-key",
        "response": {
            "authenticatorData": bytes_to_base64url(
                device_response["response"]["authenticatorData"]
            ),
            "clientDataJSON": bytes_to_base64url(device_response["response"]["clientDataJSON"]),
            "signature": bytes_to_base64url(device_response["response"]["signature"]),
            "userHandle": (
                bytes_to_base64url(device_response["response"]["userHandle"])
                if device_response["response"]["userHandle"]
                else None
            ),
        },
    }


@pytest.mark.asyncio
async def test_full_happy_path(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("happy@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # 1. Start registration
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    assert reg_options_resp.status_code == 200
    options_dict = reg_options_resp.json()

    # 2. Emulate device creation
    device = SoftWebauthnDevice()
    prepared_options = prepare_options_for_soft_webauthn(options_dict)
    device_response = device.create(prepared_options, origin=origin)
    verify_body = make_registration_verify_body(device_response, label="My Happy Key")

    # 3. Verify registration
    reg_verify_resp = await client.post("/auth/webauthn/register/verify", json=verify_body)
    assert reg_verify_resp.status_code == 201
    cred_read = reg_verify_resp.json()
    assert cred_read["label"] == "My Happy Key"
    assert cred_read["last_used_at"] is None

    # 4. Start authentication
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    assert auth_options_resp.status_code == 200
    auth_options_dict = auth_options_resp.json()

    # 5. Emulate device get
    prepared_auth_options = prepare_options_for_soft_webauthn(auth_options_dict)
    auth_device_response = device.get(prepared_auth_options, origin=origin)
    auth_verify_body = make_authentication_verify_body(auth_device_response)

    # 6. Verify authentication
    auth_verify_resp = await client.post(
        "/auth/webauthn/authenticate/verify", json=auth_verify_body
    )
    assert auth_verify_resp.status_code == 200
    token_response = auth_verify_resp.json()
    assert "access_token" in token_response
    assert token_response["token_type"] == "bearer"

    # Decode and check token payload
    decoded_user_id = decode_access_token(token_response["access_token"])
    assert decoded_user_id == user_id

    # 7. Check credentials list
    list_resp = await client.get("/auth/webauthn/credentials")
    assert list_resp.status_code == 200
    creds_list = list_resp.json()
    assert len(creds_list) == 1
    assert creds_list[0]["label"] == "My Happy Key"
    assert creds_list[0]["last_used_at"] is not None


@pytest.mark.asyncio
async def test_challenge_single_use(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("singleuse@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # Register device
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    options_dict = reg_options_resp.json()
    device = SoftWebauthnDevice()
    device_response = device.create(prepare_options_for_soft_webauthn(options_dict), origin=origin)
    await client.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(device_response)
    )

    # Authenticate
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    auth_options_dict = auth_options_resp.json()
    auth_device_response = device.get(
        prepare_options_for_soft_webauthn(auth_options_dict), origin=origin
    )
    auth_verify_body = make_authentication_verify_body(auth_device_response)

    # Verify first time - 200
    first_resp = await client.post("/auth/webauthn/authenticate/verify", json=auth_verify_body)
    assert first_resp.status_code == 200

    # Verify second time (replay challenge) - 401
    second_resp = await client.post("/auth/webauthn/authenticate/verify", json=auth_verify_body)
    assert second_resp.status_code == 401
    assert second_resp.json()["detail"] == "Invalid or expired credentials"


@pytest.mark.asyncio
async def test_expired_challenge(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("expired@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # Registration expired challenge test
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    options_dict = reg_options_resp.json()
    device = SoftWebauthnDevice()
    device_response = device.create(prepare_options_for_soft_webauthn(options_dict), origin=origin)
    verify_body = make_registration_verify_body(device_response)

    # Manually age challenge in DB
    challenge_bytes = base64url_to_bytes(options_dict["challenge"])
    stmt = select(WebAuthnChallenge).where(WebAuthnChallenge.challenge == challenge_bytes)
    res = await session.exec(stmt)
    db_challenge = res.one()
    db_challenge.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.add(db_challenge)
    await session.commit()

    reg_verify_resp = await client.post("/auth/webauthn/register/verify", json=verify_body)
    assert reg_verify_resp.status_code == 400
    assert reg_verify_resp.json()["detail"] == "Registration failed"

    # Authentication expired challenge test
    # Register properly first (generate a fresh challenge)
    reg_options_resp_fresh = await client.post("/auth/webauthn/register/options")
    options_dict_fresh = reg_options_resp_fresh.json()
    device_response_fresh = device.create(
        prepare_options_for_soft_webauthn(options_dict_fresh), origin=origin
    )
    verify_body_fresh = make_registration_verify_body(device_response_fresh)
    reg_verify_resp_fresh = await client.post(
        "/auth/webauthn/register/verify", json=verify_body_fresh
    )
    assert reg_verify_resp_fresh.status_code == 201

    # Auth options
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    auth_options_dict = auth_options_resp.json()
    auth_device_response = device.get(
        prepare_options_for_soft_webauthn(auth_options_dict), origin=origin
    )
    auth_verify_body = make_authentication_verify_body(auth_device_response)

    # Age auth challenge in DB
    auth_challenge_bytes = base64url_to_bytes(auth_options_dict["challenge"])

    stmt_auth = select(WebAuthnChallenge).where(WebAuthnChallenge.challenge == auth_challenge_bytes)
    res_auth = await session.exec(stmt_auth)
    db_auth_challenge = res_auth.one()
    db_auth_challenge.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.add(db_auth_challenge)
    await session.commit()

    auth_verify_resp = await client.post(
        "/auth/webauthn/authenticate/verify", json=auth_verify_body
    )
    assert auth_verify_resp.status_code == 401
    assert auth_verify_resp.json()["detail"] == "Invalid or expired credentials"


@pytest.mark.asyncio
async def test_unknown_credential(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("unknowncred@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # Register device
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    options_dict = reg_options_resp.json()
    device = SoftWebauthnDevice()
    device_response = device.create(prepare_options_for_soft_webauthn(options_dict), origin=origin)
    await client.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(device_response)
    )

    # Auth options
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    auth_options_dict = auth_options_resp.json()
    auth_device_response = device.get(
        prepare_options_for_soft_webauthn(auth_options_dict), origin=origin
    )

    # Mangle rawId to represent a non-existent credential
    auth_device_response_mangled = copy.deepcopy(auth_device_response)
    auth_device_response_mangled["rawId"] = b"mangled_credential_id_123"
    auth_verify_body = make_authentication_verify_body(auth_device_response_mangled)

    auth_verify_resp = await client.post(
        "/auth/webauthn/authenticate/verify", json=auth_verify_body
    )
    assert auth_verify_resp.status_code == 401
    assert auth_verify_resp.json()["detail"] == "Invalid or expired credentials"


@pytest.mark.asyncio
async def test_registration_challenge_bound_to_user(
    client_factory, make_user, session: AsyncSession
):
    user_a_id = await make_user("usera@example.com")
    user_b_id = await make_user("userb@example.com")

    client_a = client_factory(user_a_id)
    client_b = client_factory(user_b_id)
    origin = "http://localhost:5173"

    # User A requests options
    app.dependency_overrides[get_current_user_id] = lambda: user_a_id
    reg_options_resp = await client_a.post("/auth/webauthn/register/options")
    options_dict = reg_options_resp.json()
    device = SoftWebauthnDevice()
    device_response = device.create(prepare_options_for_soft_webauthn(options_dict), origin=origin)
    verify_body = make_registration_verify_body(device_response)

    # User B attempts to verify User A's challenge
    app.dependency_overrides[get_current_user_id] = lambda: user_b_id
    verify_resp = await client_b.post("/auth/webauthn/register/verify", json=verify_body)
    assert verify_resp.status_code == 400
    assert verify_resp.json()["detail"] == "Registration failed"


@pytest.mark.asyncio
async def test_credential_management(client_factory, make_user, session: AsyncSession):
    user_a_id = await make_user("user_a@example.com")
    user_b_id = await make_user("user_b@example.com")

    client_a = client_factory(user_a_id)
    client_b = client_factory(user_b_id)
    origin = "http://localhost:5173"

    # Register for User A
    app.dependency_overrides[get_current_user_id] = lambda: user_a_id
    options_a = await client_a.post("/auth/webauthn/register/options")
    device_a = SoftWebauthnDevice()
    resp_a = device_a.create(prepare_options_for_soft_webauthn(options_a.json()), origin=origin)
    verify_a = await client_a.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(resp_a)
    )
    cred_a_id = verify_a.json()["id"]

    # Register for User B
    app.dependency_overrides[get_current_user_id] = lambda: user_b_id
    options_b = await client_b.post("/auth/webauthn/register/options")
    device_b = SoftWebauthnDevice()
    resp_b = device_b.create(prepare_options_for_soft_webauthn(options_b.json()), origin=origin)
    verify_b = await client_b.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(resp_b)
    )
    cred_b_id = verify_b.json()["id"]

    # List shows only owner's credentials
    app.dependency_overrides[get_current_user_id] = lambda: user_a_id
    list_a = await client_a.get("/auth/webauthn/credentials")
    assert len(list_a.json()) == 1
    assert list_a.json()[0]["id"] == cred_a_id

    # User A tries to delete User B's credential -> 404
    delete_b_by_a = await client_a.delete(f"/auth/webauthn/credentials/{cred_b_id}")
    assert delete_b_by_a.status_code == 404

    # User A deletes own credential -> 204
    delete_a = await client_a.delete(f"/auth/webauthn/credentials/{cred_a_id}")
    assert delete_a.status_code == 204

    # Verification using deleted credential fails -> 401
    auth_options = await client_a.post("/auth/webauthn/authenticate/options")
    auth_resp = device_a.get(prepare_options_for_soft_webauthn(auth_options.json()), origin=origin)
    verify_deleted = await client_a.post(
        "/auth/webauthn/authenticate/verify", json=make_authentication_verify_body(auth_resp)
    )
    assert verify_deleted.status_code == 401


@pytest.mark.asyncio
async def test_sign_count_advances(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("signcount@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # Register
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    device = SoftWebauthnDevice()
    device_response = device.create(
        prepare_options_for_soft_webauthn(reg_options_resp.json()), origin=origin
    )
    await client.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(device_response)
    )

    # Retrieve row and check initial sign count
    stmt = select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
    res = await session.exec(stmt)
    cred = res.one()
    initial_sign_count = cred.sign_count

    # Authenticate
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    auth_device_response = device.get(
        prepare_options_for_soft_webauthn(auth_options_resp.json()), origin=origin
    )
    await client.post(
        "/auth/webauthn/authenticate/verify",
        json=make_authentication_verify_body(auth_device_response),
    )

    # Refresh row and verify it advanced
    await session.refresh(cred)
    assert cred.sign_count > initial_sign_count

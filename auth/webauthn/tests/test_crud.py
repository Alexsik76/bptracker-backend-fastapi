from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.webauthn.crud import (
    consume_challenge,
    create_challenge,
    create_credential,
    delete_credential,
    get_credential_by_credential_id,
    list_credentials_by_user,
    update_credential_after_auth,
)
from auth.webauthn.models import ChallengePurpose, WebAuthnChallenge


@pytest.mark.asyncio
async def test_credential_round_trip(session: AsyncSession, make_user):
    user_id = await make_user("user1@example.com")
    cred_id = b"credential_id_bytes_123"
    pub_key = b"public_key_bytes_123"

    cred = await create_credential(
        session,
        user_id=user_id,
        credential_id=cred_id,
        public_key=pub_key,
        sign_count=10,
        transports=["internal", "hybrid"],
        backup_eligible=True,
        backup_state=False,
        label="YubiKey 5C",
    )

    assert cred.id is not None
    assert cred.user_id == user_id
    assert cred.credential_id == cred_id
    assert cred.public_key == pub_key
    assert cred.sign_count == 10
    assert cred.transports == ["internal", "hybrid"]
    assert cred.backup_eligible is True
    assert cred.backup_state is False
    assert cred.label == "YubiKey 5C"
    assert cred.created_at is not None
    assert cred.last_used_at is None

    fetched = await get_credential_by_credential_id(session, cred_id)
    assert fetched is not None
    assert fetched.id == cred.id
    assert fetched.user_id == user_id
    assert fetched.public_key == pub_key
    assert fetched.sign_count == 10
    assert fetched.transports == ["internal", "hybrid"]
    assert fetched.backup_eligible is True
    assert fetched.backup_state is False
    assert fetched.label == "YubiKey 5C"

    await delete_credential(session, fetched)
    assert await get_credential_by_credential_id(session, cred_id) is None


@pytest.mark.asyncio
async def test_credential_id_uniqueness(session: AsyncSession, make_user):
    user_id_1 = await make_user("user1@example.com")
    user_id_2 = await make_user("user2@example.com")
    cred_id = b"shared_credential_id_bytes"

    await create_credential(
        session,
        user_id=user_id_1,
        credential_id=cred_id,
        public_key=b"pk1",
        sign_count=0,
        transports=None,
        backup_eligible=True,
        backup_state=True,
        label="Key 1",
    )

    with pytest.raises(IntegrityError):
        await create_credential(
            session,
            user_id=user_id_2,
            credential_id=cred_id,
            public_key=b"pk2",
            sign_count=0,
            transports=None,
            backup_eligible=True,
            backup_state=True,
            label="Key 2",
        )


@pytest.mark.asyncio
async def test_list_credentials_by_user(session: AsyncSession, make_user):
    user_id_1 = await make_user("user1@example.com")
    user_id_2 = await make_user("user2@example.com")

    cred1 = await create_credential(
        session,
        user_id=user_id_1,
        credential_id=b"cred1",
        public_key=b"pk1",
        sign_count=0,
        transports=None,
        backup_eligible=True,
        backup_state=True,
        label="Key 1",
    )

    cred2 = await create_credential(
        session,
        user_id=user_id_1,
        credential_id=b"cred2",
        public_key=b"pk2",
        sign_count=0,
        transports=None,
        backup_eligible=True,
        backup_state=True,
        label="Key 2",
    )

    cred3 = await create_credential(
        session,
        user_id=user_id_2,
        credential_id=b"cred3",
        public_key=b"pk3",
        sign_count=0,
        transports=None,
        backup_eligible=True,
        backup_state=True,
        label="Key 3",
    )

    user1_creds = await list_credentials_by_user(session, user_id_1)
    assert len(user1_creds) == 2
    assert user1_creds[0].id == cred1.id
    assert user1_creds[1].id == cred2.id

    user2_creds = await list_credentials_by_user(session, user_id_2)
    assert len(user2_creds) == 1
    assert user2_creds[0].id == cred3.id


@pytest.mark.asyncio
async def test_update_credential_after_auth(session: AsyncSession, make_user):
    user_id = await make_user("user1@example.com")
    cred = await create_credential(
        session,
        user_id=user_id,
        credential_id=b"cred",
        public_key=b"pk",
        sign_count=5,
        transports=None,
        backup_eligible=True,
        backup_state=True,
        label="Key",
    )
    assert cred.sign_count == 5
    assert cred.last_used_at is None

    updated = await update_credential_after_auth(session, cred, sign_count=6)
    assert updated.sign_count == 6
    assert updated.last_used_at.tzinfo is not None
    assert updated.last_used_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_consume_challenge_single_use(session: AsyncSession):
    challenge_bytes = b"random_challenge_nonce_123"
    expires = datetime.now(UTC) + timedelta(minutes=5)

    challenge = await create_challenge(
        session,
        challenge=challenge_bytes,
        user_id=None,
        purpose=ChallengePurpose.REGISTRATION,
        expires_at=expires,
    )

    assert challenge.challenge == challenge_bytes
    assert challenge.purpose == ChallengePurpose.REGISTRATION
    assert challenge.user_id is None

    consumed = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.REGISTRATION
    )
    assert consumed is not None
    assert consumed.challenge == challenge_bytes

    consumed_again = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.REGISTRATION
    )
    assert consumed_again is None


@pytest.mark.asyncio
async def test_consume_challenge_mismatched_purpose(session: AsyncSession):
    challenge_bytes = b"random_challenge_nonce_456"
    expires = datetime.now(UTC) + timedelta(minutes=5)

    await create_challenge(
        session,
        challenge=challenge_bytes,
        user_id=None,
        purpose=ChallengePurpose.REGISTRATION,
        expires_at=expires,
    )

    consumed = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.AUTHENTICATION
    )
    assert consumed is None

    statement = select(WebAuthnChallenge).where(WebAuthnChallenge.challenge == challenge_bytes)
    result = await session.exec(statement)
    row = result.first()
    assert row is not None
    assert row.purpose == ChallengePurpose.REGISTRATION


@pytest.mark.asyncio
async def test_consume_challenge_atomic_properties(session: AsyncSession):
    challenge_bytes = b"atomic_test_challenge_nonce"
    expires = datetime.now(UTC) + timedelta(minutes=5)

    await create_challenge(
        session,
        challenge=challenge_bytes,
        user_id=None,
        purpose=ChallengePurpose.REGISTRATION,
        expires_at=expires,
    )

    consumed_mismatch = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.AUTHENTICATION
    )
    assert consumed_mismatch is None

    statement = select(WebAuthnChallenge).where(WebAuthnChallenge.challenge == challenge_bytes)
    res = await session.exec(statement)
    assert res.first() is not None

    consumed_correct = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.REGISTRATION
    )
    assert consumed_correct is not None
    assert consumed_correct.challenge == challenge_bytes

    consumed_sequential = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.REGISTRATION
    )
    assert consumed_sequential is None

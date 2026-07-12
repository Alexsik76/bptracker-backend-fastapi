from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.webauthn.models import ChallengePurpose, WebAuthnChallenge, WebAuthnCredential


async def create_credential(
    session: AsyncSession,
    *,
    user_id: UUID,
    credential_id: bytes,
    public_key: bytes,
    sign_count: int,
    transports: list[str] | None,
    backup_eligible: bool,
    backup_state: bool,
    label: str | None,
) -> WebAuthnCredential:
    credential = WebAuthnCredential(
        user_id=user_id,
        credential_id=credential_id,
        public_key=public_key,
        sign_count=sign_count,
        transports=transports,
        backup_eligible=backup_eligible,
        backup_state=backup_state,
        label=label,
    )
    session.add(credential)
    await session.commit()
    await session.refresh(credential)
    return credential


async def get_credential_by_credential_id(
    session: AsyncSession, credential_id: bytes
) -> WebAuthnCredential | None:
    statement = select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
    result = await session.exec(statement)
    return result.first()


async def list_credentials_by_user(
    session: AsyncSession, user_id: UUID
) -> list[WebAuthnCredential]:
    statement = (
        select(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == user_id)
        .order_by(WebAuthnCredential.created_at.asc())
    )
    result = await session.exec(statement)
    return list(result.all())


async def delete_credential(session: AsyncSession, credential: WebAuthnCredential) -> None:
    await session.delete(credential)
    await session.commit()


async def update_credential_after_auth(
    session: AsyncSession, credential: WebAuthnCredential, *, sign_count: int
) -> WebAuthnCredential:
    credential.sign_count = sign_count
    credential.last_used_at = datetime.now(UTC)
    session.add(credential)
    await session.commit()
    await session.refresh(credential)
    return credential


async def create_challenge(
    session: AsyncSession,
    *,
    challenge: bytes,
    user_id: UUID | None,
    purpose: ChallengePurpose,
    expires_at: datetime,
) -> WebAuthnChallenge:
    webauthn_challenge = WebAuthnChallenge(
        challenge=challenge,
        user_id=user_id,
        purpose=purpose,
        expires_at=expires_at,
    )
    session.add(webauthn_challenge)
    await session.commit()
    await session.refresh(webauthn_challenge)
    return webauthn_challenge


async def consume_challenge(
    session: AsyncSession, *, challenge: bytes, purpose: ChallengePurpose
) -> WebAuthnChallenge | None:
    # To prevent a race condition where concurrent verify requests could both read the
    # same ephemeral challenge, we perform a single atomic DELETE ... RETURNING operation.
    # Placing the purpose check in the WHERE clause ensures we only delete and return
    # the challenge if it exactly matches the expected purpose, leaving it intact otherwise.
    statement = (
        delete(WebAuthnChallenge)
        .where(
            WebAuthnChallenge.challenge == challenge,
            WebAuthnChallenge.purpose == purpose,
        )
        .returning(WebAuthnChallenge)
    )
    result = await session.exec(statement)
    row = result.scalar_one_or_none()
    if row:
        await session.commit()
        return row
    return None

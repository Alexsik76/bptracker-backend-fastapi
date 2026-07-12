from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.webauthn.crud import (
    consume_challenge,
    create_challenge,
    create_credential,
    get_credential_by_credential_id,
    list_credentials_by_user,
    update_credential_after_auth,
)
from auth.webauthn.models import ChallengePurpose, WebAuthnCredential
from config import Settings
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_client_data_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


class CeremonyError(Exception):
    """Raised when any step of registration or authentication ceremony fails."""

    pass


async def start_registration(
    session: AsyncSession,
    *,
    user_id: UUID,
    email: str,
    settings: Settings,
) -> str:
    existing_creds = await list_credentials_by_user(session, user_id)
    exclude_credentials = [
        PublicKeyCredentialDescriptor(id=cred.credential_id) for cred in existing_creds
    ]

    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user_id.bytes,
        user_name=email,
        exclude_credentials=exclude_credentials,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    expires_at = datetime.now(UTC) + timedelta(minutes=settings.webauthn_challenge_ttl_minutes)
    await create_challenge(
        session,
        challenge=options.challenge,
        user_id=user_id,
        purpose=ChallengePurpose.REGISTRATION,
        expires_at=expires_at,
    )

    return options_to_json(options)


async def finish_registration(
    session: AsyncSession,
    *,
    user_id: UUID,
    body: dict,
    settings: Settings,
) -> WebAuthnCredential:
    client_data_json_b64 = body.get("response", {}).get("clientDataJSON")
    if not client_data_json_b64:
        raise CeremonyError("Missing clientDataJSON")

    try:
        client_data_json_bytes = base64url_to_bytes(client_data_json_b64)
        client_data = parse_client_data_json(client_data_json_bytes)
        challenge_bytes = client_data.challenge
    except Exception as e:
        raise CeremonyError(f"Invalid clientDataJSON format: {e}") from e

    challenge_row = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.REGISTRATION
    )
    if not challenge_row:
        raise CeremonyError("Registration challenge not found or purpose mismatch")

    if challenge_row.expires_at < datetime.now(UTC):
        raise CeremonyError("Registration challenge expired")

    if challenge_row.user_id != user_id:
        raise CeremonyError("Registration challenge not bound to this user")

    try:
        verification = verify_registration_response(
            credential=body,
            expected_challenge=challenge_row.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
        )
    except InvalidRegistrationResponse as e:
        raise CeremonyError(f"Registration verification failed: {e}") from e

    transports = body.get("response", {}).get("transports") or body.get("transports")

    try:
        return await create_credential(
            session,
            user_id=user_id,
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=transports,
            backup_eligible=(
                verification.credential_device_type == CredentialDeviceType.MULTI_DEVICE
            ),
            backup_state=verification.credential_backed_up,
            label=body.get("label"),
        )
    except IntegrityError as e:
        raise CeremonyError(f"Credential already exists: {e}") from e


async def start_authentication(
    session: AsyncSession,
    *,
    settings: Settings,
) -> str:
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=[],
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    expires_at = datetime.now(UTC) + timedelta(minutes=settings.webauthn_challenge_ttl_minutes)
    await create_challenge(
        session,
        challenge=options.challenge,
        user_id=None,
        purpose=ChallengePurpose.AUTHENTICATION,
        expires_at=expires_at,
    )

    return options_to_json(options)


async def finish_authentication(
    session: AsyncSession,
    *,
    body: dict,
    settings: Settings,
) -> UUID:
    client_data_json_b64 = body.get("response", {}).get("clientDataJSON")
    if not client_data_json_b64:
        raise CeremonyError("Missing clientDataJSON")

    try:
        client_data_json_bytes = base64url_to_bytes(client_data_json_b64)
        client_data = parse_client_data_json(client_data_json_bytes)
        challenge_bytes = client_data.challenge
    except Exception as e:
        raise CeremonyError(f"Invalid clientDataJSON format: {e}") from e

    # To prevent brute-force probing of credential IDs (rawId) without consuming
    # the single-use challenge, we consume and burn the challenge unconditionally
    # before performing the database lookup for the credential.
    challenge_row = await consume_challenge(
        session, challenge=challenge_bytes, purpose=ChallengePurpose.AUTHENTICATION
    )
    if not challenge_row:
        raise CeremonyError("Authentication challenge not found or purpose mismatch")

    if challenge_row.expires_at < datetime.now(UTC):
        raise CeremonyError("Authentication challenge expired")

    raw_id = body.get("rawId")
    if not raw_id:
        raise CeremonyError("Missing rawId")

    try:
        credential_id_bytes = base64url_to_bytes(raw_id)
    except Exception as e:
        raise CeremonyError(f"Invalid rawId: {e}") from e

    cred = await get_credential_by_credential_id(session, credential_id_bytes)
    if not cred:
        raise CeremonyError("Credential not found")

    try:
        verification = verify_authentication_response(
            credential=body,
            expected_challenge=challenge_row.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
        )
    except InvalidAuthenticationResponse as e:
        raise CeremonyError(f"Authentication verification failed: {e}") from e

    await update_credential_after_auth(session, cred, sign_count=verification.new_sign_count)
    return cred.user_id

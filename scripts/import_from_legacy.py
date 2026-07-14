"""One-off migration: legacy C# database → FastAPI database.

Migrates two tables:
  Measurements     → measurements
  UserCredentials  → webauthn_credentials

Run with:
  uv run python scripts/import_from_legacy.py \
      --legacy-url postgresql://user:pass@host:port/db \
      --target-url postgresql://user:pass@host:port/db \
      --user-id <uuid> \
      [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.webauthn.models import WebAuthnCredential
from measurements.models import Measurement


def _ensure_async_driver(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


async def _verify_target_user(session: AsyncSession, user_id: UUID) -> None:
    result = await session.execute(
        sa_text("SELECT id FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    if result.first() is None:
        print(f"ERROR: user {user_id} does not exist in the target database.", file=sys.stderr)
        raise SystemExit(1)


async def _check_existing_rows(session: AsyncSession, user_id: UUID, *, dry_run: bool) -> None:
    m_count = (
        await session.execute(
            sa_text("SELECT count(*) FROM measurements WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    wc_count = (
        await session.execute(
            sa_text("SELECT count(*) FROM webauthn_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    if dry_run:
        print(f"  Target measurements for this user:          {m_count}")
        print(f"  Target webauthn_credentials for this user:   {wc_count}")
        return

    if m_count > 0 or wc_count > 0:
        print(
            "ERROR: target tables already contain rows for this user.\n"
            "Clear them first:\n"
            f"  DELETE FROM measurements WHERE user_id = '{user_id}';\n"
            f"  DELETE FROM webauthn_credentials WHERE user_id = '{user_id}';",
            file=sys.stderr,
        )
        raise SystemExit(1)


async def _read_legacy_measurements(legacy_engine, user_id: UUID) -> list[Measurement]:
    async with AsyncSession(legacy_engine) as session:
        rows = (
            await session.execute(
                sa_text(
                    'SELECT "Id", "Sys", "Dia", "Pulse", "RecordedAt" '
                    'FROM "Measurements" '
                    'ORDER BY "RecordedAt"'
                )
            )
        ).all()

    measurements: list[Measurement] = []
    for row in rows:
        try:
            m = Measurement(
                sys=row.Sys,
                dia=row.Dia,
                pulse=row.Pulse,
                recorded_at=row.RecordedAt,
                user_id=user_id,
            )
        except ValidationError as exc:
            print(
                f"ERROR: measurement {row.Id} failed validation "
                f"(sys={row.Sys}, dia={row.Dia}, pulse={row.Pulse}):\n{exc}",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        measurements.append(m)

    return measurements


async def _read_legacy_credentials(legacy_engine, user_id: UUID) -> list[WebAuthnCredential]:
    async with AsyncSession(legacy_engine) as session:
        rows = (
            await session.execute(
                sa_text(
                    'SELECT "CredentialId", "PublicKey", "SignCount", '
                    '"DeviceName", "CreatedAt", "LastUsedAt" '
                    'FROM "UserCredentials" '
                    'ORDER BY "CreatedAt"'
                )
            )
        ).all()

    credentials: list[WebAuthnCredential] = []
    for row in rows:
        cred = WebAuthnCredential(
            credential_id=bytes(row.CredentialId),
            public_key=bytes(row.PublicKey),
            sign_count=row.SignCount,
            label=row.DeviceName,
            created_at=row.CreatedAt,
            last_used_at=row.LastUsedAt,
            user_id=user_id,
            # These three fields do not exist in the legacy schema.
            # They are advisory (drive UI hints only, not signature verification),
            # so assuming synced-platform-passkey values is safe and matches reality.
            transports=["internal", "hybrid"],
            backup_eligible=True,
            backup_state=True,
        )
        credentials.append(cred)

    return credentials


async def _run(args: argparse.Namespace) -> None:
    legacy_engine = create_async_engine(_ensure_async_driver(args.legacy_url), echo=False)
    target_engine = create_async_engine(_ensure_async_driver(args.target_url), echo=False)
    target_session_factory = async_sessionmaker(
        bind=target_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        measurements = await _read_legacy_measurements(legacy_engine, args.user_id)
        credentials = await _read_legacy_credentials(legacy_engine, args.user_id)
    finally:
        await legacy_engine.dispose()

    print(f"Read {len(measurements)} measurements from legacy DB.")
    print(f"Read {len(credentials)} webauthn_credentials from legacy DB.")

    try:
        if args.dry_run:
            print("\n--- DRY RUN ---")
            async with target_session_factory() as session:
                await _verify_target_user(session, args.user_id)
                await _check_existing_rows(session, args.user_id, dry_run=True)

            print(f"\nWould write {len(measurements)} measurements.")
            print(f"Would write {len(credentials)} webauthn_credentials.")
            print("No changes made.")
            return

        async with target_session_factory() as session:
            await _verify_target_user(session, args.user_id)
            await _check_existing_rows(session, args.user_id, dry_run=False)

            for m in measurements:
                session.add(m)
            for c in credentials:
                session.add(c)

            await session.commit()

        print(f"\nWritten {len(measurements)} measurements.")
        print(f"Written {len(credentials)} webauthn_credentials.")
        print("Import complete.")
    finally:
        await target_engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import data from the legacy C# backend database.",
    )
    parser.add_argument(
        "--legacy-url",
        required=True,
        help="PostgreSQL connection string for the legacy (source) database.",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="PostgreSQL connection string for the new (target) database.",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        type=UUID,
        help="UUID of the target user in the new database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read source data and report what would be written, without making changes.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

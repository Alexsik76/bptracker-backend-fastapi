"""One-off migration: legacy C# database → FastAPI database.

Migrates two tables:
  Measurements     → measurements
  UserCredentials  → webauthn_credentials

Everything else in the legacy schema is deliberately out of scope: prescriptions carry no
frequency or course axes to map onto, reminders hold test data only, and intake history is
not needed.

Run with:
  uv run python scripts/import_from_legacy.py \
      --legacy-url postgresql://user:pass@host:port/db \
      --target-url postgresql://user:pass@host:port/db \
      --user-id <uuid> \
      [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

# The script is executed as a file from scripts/, not as a module, so the project root
# is not on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
import typer
from psycopg import AsyncCursor
from psycopg.rows import dict_row
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.webauthn.models import WebAuthnCredential
from measurements.models import Measurement

console = Console()
error_console = Console(stderr=True)


def _target_async_url(url: str) -> str:
    """SQLAlchemy needs an explicit driver in the scheme; psycopg (source side) does not."""
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _describe(url: str) -> str:
    """Render 'host:port/database' for display, without leaking the password."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.path.lstrip("/")
    return f"{host}{port}/{database}"


async def _verify_target_user(session: AsyncSession, user_id: UUID) -> str:
    # session.connection() gives the plain SQLAlchemy connection. SQLModel's own execute()
    # is meant for model queries and warns when handed raw SQL.
    conn = await session.connection()
    row = (
        await conn.execute(
            sa_text("SELECT email FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
    ).first()

    if row is None:
        error_console.print(
            f"[bold red]ERROR:[/bold red] user {user_id} does not exist in the target database."
        )
        raise SystemExit(1)

    return str(row.email)


async def _count_existing(session: AsyncSession, user_id: UUID) -> tuple[int, int]:
    conn = await session.connection()

    measurements = (
        await conn.execute(
            sa_text("SELECT count(*) FROM measurements WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    credentials = (
        await conn.execute(
            sa_text("SELECT count(*) FROM webauthn_credentials WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalar_one()

    return int(measurements), int(credentials)


async def _read_measurements(cur: AsyncCursor[dict[str, Any]], user_id: UUID) -> list[Measurement]:
    await cur.execute(
        'SELECT "Id", "Sys", "Dia", "Pulse", "RecordedAt" FROM "Measurements" ORDER BY "RecordedAt"'
    )

    measurements: list[Measurement] = []
    for row in await cur.fetchall():
        try:
            measurements.append(
                Measurement(
                    sys=row["Sys"],
                    dia=row["Dia"],
                    pulse=row["Pulse"],
                    recorded_at=row["RecordedAt"],
                    user_id=user_id,
                )
            )
        except ValidationError as exc:
            error_console.print(
                f"[bold red]ERROR:[/bold red] measurement {row['Id']} failed validation "
                f"(sys={row['Sys']}, dia={row['Dia']}, pulse={row['Pulse']}):\n{exc}"
            )
            raise SystemExit(1) from exc

    return measurements


async def _read_credentials(
    cur: AsyncCursor[dict[str, Any]], user_id: UUID
) -> list[WebAuthnCredential]:
    await cur.execute(
        'SELECT "CredentialId", "PublicKey", "SignCount", "DeviceName", "CreatedAt", "LastUsedAt" '
        'FROM "UserCredentials" ORDER BY "CreatedAt"'
    )

    return [
        WebAuthnCredential(
            # Copied verbatim: any re-encoding of these two would break signature verification.
            credential_id=bytes(row["CredentialId"]),
            public_key=bytes(row["PublicKey"]),
            sign_count=row["SignCount"],
            label=row["DeviceName"],
            created_at=row["CreatedAt"],
            last_used_at=row["LastUsedAt"],
            user_id=user_id,
            # Absent from the legacy schema, and the last two are NOT NULL here. They are
            # advisory only — they drive UI hints and take no part in signature verification —
            # so assuming the values of a synced platform passkey is safe.
            transports=["internal", "hybrid"],
            backup_eligible=True,
            backup_state=True,
        )
        for row in await cur.fetchall()
    ]


def _results_table(measurements: int, credentials: int, *, dry_run: bool) -> Table:
    table = Table(
        title="Dry run — nothing written" if dry_run else "Import complete",
        header_style="bold yellow" if dry_run else "bold green",
    )
    table.add_column("Table", style="cyan")
    table.add_column("Rows read", justify="right")
    table.add_column("Would write" if dry_run else "Rows written", justify="right")

    table.add_row("measurements", str(measurements), str(measurements))
    table.add_row("webauthn_credentials", str(credentials), str(credentials))
    return table


async def _run(legacy_url: str, target_url: str, user_id: UUID, *, dry_run: bool) -> None:
    target_engine = create_async_engine(_target_async_url(target_url), echo=False)
    session_factory = async_sessionmaker(
        bind=target_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        # Preflight first: a typo in --user-id should fail before we read a single source row.
        async with session_factory() as session:
            user_email = await _verify_target_user(session, user_id)
            existing_measurements, existing_credentials = await _count_existing(session, user_id)

        target_description = _describe(target_url)

        preflight = Table(title="Preflight", header_style="bold magenta")
        preflight.add_column("Property", style="cyan")
        preflight.add_column("Value")
        preflight.add_row("Source", _describe(legacy_url))
        preflight.add_row("Target", target_description)
        preflight.add_row("User", f"{user_email} ({user_id})")
        preflight.add_row("Existing measurements", str(existing_measurements))
        preflight.add_row("Existing credentials", str(existing_credentials))
        console.print(preflight)

        if not dry_run and (existing_measurements or existing_credentials):
            error_console.print(
                "\n[bold red]ERROR:[/bold red] the target already holds rows for this user. "
                "Refusing to import, to avoid duplicates.\n"
                "Clear them first:\n"
                f"  DELETE FROM measurements WHERE user_id = '{user_id}';\n"
                f"  DELETE FROM webauthn_credentials WHERE user_id = '{user_id}';"
            )
            raise SystemExit(1)

        with console.status("Reading legacy database..."):
            async with await psycopg.AsyncConnection.connect(legacy_url) as legacy:
                async with legacy.cursor(row_factory=dict_row) as cur:
                    measurements = await _read_measurements(cur, user_id)
                    credentials = await _read_credentials(cur, user_id)

        if dry_run:
            console.print(_results_table(len(measurements), len(credentials), dry_run=True))
            return

        typer.confirm(
            f"\nWrite {len(measurements)} measurements and {len(credentials)} credentials "
            f"to {target_description} for {user_email}?",
            abort=True,
        )

        with console.status("Writing to target database..."):
            async with session_factory() as session:
                session.add_all(measurements)
                session.add_all(credentials)
                await session.commit()

        console.print(_results_table(len(measurements), len(credentials), dry_run=False))
    finally:
        await target_engine.dispose()


def main(
    legacy_url: Annotated[
        str, typer.Option(help="Connection string for the legacy (source) database.")
    ],
    target_url: Annotated[
        str, typer.Option(help="Connection string for the new (target) database.")
    ],
    user_id: Annotated[UUID, typer.Option(help="UUID of the target user in the new database.")],
    dry_run: Annotated[
        bool, typer.Option(help="Read the source and report, without writing anything.")
    ] = False,
) -> None:
    """Import measurements and passkeys from the legacy C# backend database."""
    if sys.platform == "win32":
        # psycopg's async mode cannot run on Windows' default ProactorEventLoop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(_run(legacy_url, target_url, user_id, dry_run=dry_run))


if __name__ == "__main__":
    typer.run(main)

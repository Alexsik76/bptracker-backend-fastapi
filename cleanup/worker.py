import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import delete

from auth.models import MagicLink, Session
from auth.webauthn.models import WebAuthnChallenge
from config import Settings
from email_infra.models import EmailOutbox, EmailStatus

logger = logging.getLogger(__name__)


async def run_cleanup_worker(
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    interval_seconds = settings.cleanup_interval_minutes * 60

    while True:
        try:
            async with session_factory() as session:
                now = datetime.now(UTC)

                # 1. magic_links where expires_at < now(UTC)
                magic_links_stmt = delete(MagicLink).where(MagicLink.expires_at < now)
                magic_links_res = await session.exec(magic_links_stmt)
                deleted_magic_links = magic_links_res.rowcount

                # 2. webauthn_challenges where expires_at < now(UTC)
                challenges_stmt = delete(WebAuthnChallenge).where(
                    WebAuthnChallenge.expires_at < now
                )
                challenges_res = await session.exec(challenges_stmt)
                deleted_challenges = challenges_res.rowcount

                # 3. sessions where expires_at < now(UTC) - 30 days
                # We keep recently expired session rows for a grace period so that refresh-token
                # reuse detection still recognises tokens it has retired.
                sessions_stmt = delete(Session).where(Session.expires_at < now - timedelta(days=30))
                sessions_res = await session.exec(sessions_stmt)
                deleted_sessions = sessions_res.rowcount

                # 4. email_outbox where status = SENT and created_at < now(UTC) - 30 days
                outbox_stmt = delete(EmailOutbox).where(
                    EmailOutbox.status == EmailStatus.SENT,
                    EmailOutbox.created_at < now - timedelta(days=30),
                )
                outbox_res = await session.exec(outbox_stmt)
                deleted_outbox = outbox_res.rowcount

                await session.commit()

                logger.info(
                    "Cleanup run completed. "
                    "Deleted: magic_links=%d, webauthn_challenges=%d, sessions=%d, email_outbox=%d",
                    deleted_magic_links,
                    deleted_challenges,
                    deleted_sessions,
                    deleted_outbox,
                )
        except asyncio.CancelledError:
            logger.info("Cleanup worker task cancelled. Exiting cleanly.")
            raise
        except Exception:
            logger.exception("Unexpected error in cleanup worker loop")

        await asyncio.sleep(interval_seconds)

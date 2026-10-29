import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from activity.service import purge_expired_activity_events
from auth import crud as auth_crud
from auth.webauthn import crud as webauthn_crud
from config import Settings
from email_infra import crud as email_infra_crud

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

                deleted_magic_links = await auth_crud.purge_expired_magic_links(session, now=now)
                deleted_challenges = await webauthn_crud.purge_expired_challenges(session, now=now)
                deleted_sessions = await auth_crud.purge_expired_sessions(session, now=now)
                deleted_outbox = await email_infra_crud.purge_sent_emails(session, now=now)
                deleted_requests, deleted_errors = await purge_expired_activity_events(
                    session, retention_days=settings.activity_retention_days, now=now
                )

                await session.commit()

                logger.info(
                    "Cleanup run completed. "
                    "Deleted: magic_links=%d, webauthn_challenges=%d, sessions=%d, "
                    "email_outbox=%d, request_events=%d, error_events=%d",
                    deleted_magic_links,
                    deleted_challenges,
                    deleted_sessions,
                    deleted_outbox,
                    deleted_requests,
                    deleted_errors,
                )
        except asyncio.CancelledError:
            logger.info("Cleanup worker task cancelled. Exiting cleanly.")
            raise
        except Exception:
            logger.exception("Unexpected error in cleanup worker loop")

        await asyncio.sleep(interval_seconds)

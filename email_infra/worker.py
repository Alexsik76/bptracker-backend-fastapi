import asyncio
import base64
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from config import Settings
from email_infra.crud import claim_batch, mark_failed, mark_sent
from email_infra.models import EmailStatus
from email_infra.sender import SmtpEmailSender
from email_infra.types import EmailAttachment

logger = logging.getLogger(__name__)


async def run_email_outbox_worker(
    session_factory: async_sessionmaker,
    smtp_sender: SmtpEmailSender,
    settings: Settings,
) -> None:
    poll_seconds = settings.email_outbox_poll_seconds
    batch_size = settings.email_outbox_batch_size
    max_attempts = settings.email_outbox_max_attempts

    while True:
        try:
            async with session_factory() as session:
                batch = await claim_batch(
                    session,
                    limit=batch_size,
                    lease_seconds=settings.email_outbox_lease_seconds,
                )
                if batch:
                    logger.info("Claimed %d email outbox items for sending", len(batch))
                    for item in batch:
                        send_success = False
                        send_exc = None
                        try:
                            attachments = []
                            if item.attachments:
                                for att in item.attachments:
                                    filename = att["filename"]
                                    content_type = att["content_type"]
                                    content_bytes = base64.b64decode(att["content_b64"])
                                    attachments.append(
                                        EmailAttachment(
                                            filename=filename,
                                            content=content_bytes,
                                            content_type=content_type,
                                        )
                                    )

                            await smtp_sender.send(
                                to=item.to,
                                subject=item.subject,
                                text=item.body,
                                attachments=attachments,
                            )
                            send_success = True
                        except Exception as exc:
                            send_exc = exc

                        if send_success:
                            await mark_sent(session, item)
                            logger.info("Email %s to %s sent successfully", item.id, item.to)
                        else:
                            await mark_failed(
                                session,
                                item,
                                error=str(send_exc),
                                max_attempts=max_attempts,
                            )
                            if item.status == EmailStatus.DEAD:
                                logger.error(
                                    "Email %s to %s marked DEAD after %d/%d attempts. Error: %s",
                                    item.id,
                                    item.to,
                                    item.attempts,
                                    max_attempts,
                                    send_exc,
                                )
                            else:
                                logger.warning(
                                    "Email %s to %s failed (attempt %d/%d). "
                                    "Scheduled retry. Error: %s",
                                    item.id,
                                    item.to,
                                    item.attempts,
                                    max_attempts,
                                    send_exc,
                                )
        except asyncio.CancelledError:
            logger.info("Email outbox worker task cancelled. Exiting cleanly.")
            raise
        except Exception:
            logger.exception("Unexpected error in email outbox worker loop")

        await asyncio.sleep(poll_seconds)

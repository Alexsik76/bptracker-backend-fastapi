import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from email_infra.models import EmailOutbox, EmailStatus
from email_infra.sender import EmailAttachment


async def enqueue(
    session: AsyncSession,
    *,
    to: str,
    subject: str,
    body: str,
    attachments: list[EmailAttachment] | None = None,
    user_id: UUID | None = None,
) -> EmailOutbox:
    encoded_attachments = None
    if attachments is not None:
        encoded_attachments = []
        for attachment in attachments:
            content_b64 = base64.b64encode(attachment.content).decode("utf-8")
            encoded_attachments.append(
                {
                    "filename": attachment.filename,
                    "content_b64": content_b64,
                    "content_type": attachment.content_type,
                }
            )

    item = EmailOutbox(
        to=to,
        subject=subject,
        body=body,
        attachments=encoded_attachments,
        status=EmailStatus.PENDING,
        attempts=0,
        next_attempt_at=datetime.now(UTC),
        user_id=user_id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def claim_batch(session: AsyncSession, *, limit: int) -> list[EmailOutbox]:
    """Selects pending or failed emails that are due for delivery.

    This query uses SELECT ... FOR UPDATE SKIP LOCKED to ensure multiple concurrent
    workers do not claim and process the same email. This is essential for horizontal
    scaling in production.
    """
    now_utc = datetime.now(UTC)
    statement = (
        select(EmailOutbox)
        .where(
            EmailOutbox.status.in_([EmailStatus.PENDING, EmailStatus.FAILED]),
            EmailOutbox.next_attempt_at <= now_utc,
        )
        .order_by(EmailOutbox.next_attempt_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.exec(statement)
    return list(result.all())


async def mark_sent(session: AsyncSession, item: EmailOutbox) -> None:
    item.status = EmailStatus.SENT
    item.attempts += 1
    session.add(item)
    await session.commit()


async def mark_failed(
    session: AsyncSession,
    item: EmailOutbox,
    *,
    error: str,
    max_attempts: int,
) -> None:
    item.attempts += 1
    item.last_error = error
    if item.attempts >= max_attempts:
        item.status = EmailStatus.DEAD
    else:
        item.status = EmailStatus.FAILED
        backoff_minutes = 5 * (2 ** (item.attempts - 1))
        item.next_attempt_at = datetime.now(UTC) + timedelta(minutes=backoff_minutes)

    session.add(item)
    await session.commit()

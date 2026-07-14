import asyncio
import base64
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings
from email_infra.crud import claim_batch, enqueue, mark_sent
from email_infra.models import EmailOutbox, EmailStatus
from email_infra.sender import OutboxEmailSender, SmtpEmailSender
from email_infra.types import EmailAttachment
from email_infra.worker import run_email_outbox_worker


@pytest.mark.asyncio
async def test_enqueue_writes_pending_row(session: AsyncSession):
    attachment = EmailAttachment(
        filename="test.csv",
        content=b"col1,col2\nval1,val2",
        content_type="text/csv",
    )

    item = await enqueue(
        session,
        to="test@example.com",
        subject="Report",
        body="Here is your report",
        attachments=[attachment],
        user_id=None,
    )
    await session.commit()

    assert item.status == EmailStatus.PENDING
    assert item.to == "test@example.com"
    assert item.subject == "Report"
    assert item.body == "Here is your report"
    assert item.attempts == 0
    assert item.next_attempt_at <= datetime.now(UTC)
    assert item.attachments is not None
    assert len(item.attachments) == 1

    saved_att = item.attachments[0]
    assert saved_att["filename"] == "test.csv"
    assert saved_att["content_type"] == "text/csv"
    decoded_bytes = base64.b64decode(saved_att["content_b64"])
    assert decoded_bytes == b"col1,col2\nval1,val2"


@pytest.mark.asyncio
async def test_smtp_email_sender_with_attachment():
    sender = SmtpEmailSender(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="password",
        smtp_from="noreply@example.com",
        smtp_starttls=True,
    )

    attachment = EmailAttachment(
        filename="report.csv",
        content=b"data1,data2",
        content_type="text/csv",
    )

    with patch("email_infra.sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(
            to="recipient@example.com",
            subject="Test Attachment",
            text="Body text",
            attachments=[attachment],
        )

        mock_send.assert_called_once()
        call_args, _ = mock_send.call_args
        message = call_args[0]

        assert isinstance(message, EmailMessage)
        assert message.is_multipart()

        parts = list(message.iter_parts())
        assert len(parts) >= 2

        att_part = None
        for part in parts:
            if part.get_filename() == "report.csv":
                att_part = part
                break

        assert att_part is not None
        assert att_part.get_content_type() == "text/csv"
        assert att_part.get_payload(decode=True) == b"data1,data2"


@pytest.mark.asyncio
async def test_worker_happy_path(session: AsyncSession):
    attachment = EmailAttachment(
        filename="hello.txt",
        content=b"hello world",
        content_type="text/plain",
    )

    item = await enqueue(
        session,
        to="recipient@example.com",
        subject="Hello",
        body="Plain text",
        attachments=[attachment],
    )
    await session.commit()

    settings = Settings(
        postgres_user="dummy",
        postgres_password="dummy",
        postgres_db="dummy",
        jwt_secret="dummy",
        smtp_host="smtp.example.com",
        smtp_username="user",
        smtp_password="pass",
        smtp_from="noreply@example.com",
        magic_link_base_url="http://localhost",
        webauthn_rp_id="localhost",
        webauthn_origins=["http://localhost"],
        email_outbox_poll_seconds=1,
        email_outbox_batch_size=50,
        email_outbox_max_attempts=10,
    )

    session_factory = async_sessionmaker(
        bind=session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    mock_smtp_sender = AsyncMock(spec=SmtpEmailSender)

    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await run_email_outbox_worker(
                session_factory=session_factory,
                smtp_sender=mock_smtp_sender,
                settings=settings,
            )
        except asyncio.CancelledError:
            pass

    mock_smtp_sender.send.assert_called_once_with(
        to="recipient@example.com",
        subject="Hello",
        text="Plain text",
        attachments=[
            EmailAttachment(
                filename="hello.txt",
                content=b"hello world",
                content_type="text/plain",
            )
        ],
    )

    await session.refresh(item)
    assert item.status == EmailStatus.SENT
    assert item.attempts == 1


@pytest.mark.asyncio
async def test_worker_retry_on_failure(session: AsyncSession):
    item = await enqueue(
        session,
        to="recipient@example.com",
        subject="Fail Subject",
        body="Plain text",
    )
    await session.commit()

    settings = Settings(
        postgres_user="dummy",
        postgres_password="dummy",
        postgres_db="dummy",
        jwt_secret="dummy",
        smtp_host="smtp.example.com",
        smtp_username="user",
        smtp_password="pass",
        smtp_from="noreply@example.com",
        magic_link_base_url="http://localhost",
        webauthn_rp_id="localhost",
        webauthn_origins=["http://localhost"],
        email_outbox_poll_seconds=1,
        email_outbox_batch_size=50,
        email_outbox_max_attempts=3,
    )

    session_factory = async_sessionmaker(
        bind=session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    mock_smtp_sender = AsyncMock(spec=SmtpEmailSender)
    mock_smtp_sender.send.side_effect = Exception("SMTP Error")

    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await run_email_outbox_worker(
                session_factory=session_factory,
                smtp_sender=mock_smtp_sender,
                settings=settings,
            )
        except asyncio.CancelledError:
            pass

    mock_smtp_sender.send.assert_called_once()

    await session.refresh(item)
    assert item.status == EmailStatus.FAILED
    assert item.attempts == 1
    assert item.last_error == "SMTP Error"

    expected_next = datetime.now(UTC) + timedelta(minutes=5)
    assert abs((item.next_attempt_at - expected_next).total_seconds()) < 10


@pytest.mark.asyncio
async def test_worker_death_after_max_attempts(session: AsyncSession):
    item = await enqueue(
        session,
        to="recipient@example.com",
        subject="Dead Subject",
        body="Plain text",
    )
    await session.commit()

    settings = Settings(
        postgres_user="dummy",
        postgres_password="dummy",
        postgres_db="dummy",
        jwt_secret="dummy",
        smtp_host="smtp.example.com",
        smtp_username="user",
        smtp_password="pass",
        smtp_from="noreply@example.com",
        magic_link_base_url="http://localhost",
        webauthn_rp_id="localhost",
        webauthn_origins=["http://localhost"],
        email_outbox_poll_seconds=1,
        email_outbox_batch_size=50,
        email_outbox_max_attempts=2,
    )

    session_factory = async_sessionmaker(
        bind=session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    mock_smtp_sender = AsyncMock(spec=SmtpEmailSender)
    mock_smtp_sender.send.side_effect = Exception("SMTP Error")

    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await run_email_outbox_worker(
                session_factory=session_factory,
                smtp_sender=mock_smtp_sender,
                settings=settings,
            )
        except asyncio.CancelledError:
            pass

    await session.refresh(item)
    assert item.status == EmailStatus.FAILED
    assert item.attempts == 1

    item.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(item)
    await session.commit()

    mock_smtp_sender.send.reset_mock()
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await run_email_outbox_worker(
                session_factory=session_factory,
                smtp_sender=mock_smtp_sender,
                settings=settings,
            )
        except asyncio.CancelledError:
            pass

    await session.refresh(item)
    assert item.status == EmailStatus.DEAD
    assert item.attempts == 2

    claimed = await claim_batch(session, limit=10)
    assert item not in claimed


@pytest.mark.asyncio
async def test_concurrency_skip_locked(session: AsyncSession):
    item = await enqueue(
        session,
        to="concurrent@example.com",
        subject="Concurrency test",
        body="Plain text",
    )
    await session.commit()

    from conftest import test_session_factory

    async with test_session_factory() as session1:
        async with test_session_factory() as session2:
            batch1 = await claim_batch(session1, limit=1)
            assert len(batch1) == 1
            assert batch1[0].id == item.id

            batch2 = await claim_batch(session2, limit=1)
            assert len(batch2) == 0

            await session1.rollback()
            await session2.rollback()


@pytest.mark.asyncio
async def test_outbox_email_sender_enqueues(session: AsyncSession):
    sender = OutboxEmailSender(session)
    attachment = EmailAttachment(
        filename="sent.txt",
        content=b"outbox sender test",
        content_type="text/plain",
    )

    await sender.send(
        to="outbox-user@example.com",
        subject="Outbox Subject",
        text="Outbox Text",
        attachments=[attachment],
    )
    await session.commit()

    statement = select(EmailOutbox).where(EmailOutbox.to == "outbox-user@example.com")
    result = await session.exec(statement)
    item = result.first()

    assert item is not None
    assert item.subject == "Outbox Subject"
    assert item.body == "Outbox Text"
    assert item.status == EmailStatus.PENDING
    assert item.attachments is not None
    assert len(item.attachments) == 1
    assert item.attachments[0]["filename"] == "sent.txt"


@pytest.mark.asyncio
async def test_lease_survives_intermediate_commit(session: AsyncSession):
    await enqueue(session, to="lease1@example.com", subject="S1", body="B1")
    await enqueue(session, to="lease2@example.com", subject="S2", body="B2")
    await session.commit()

    from conftest import test_session_factory

    async with test_session_factory() as session1:
        # Claim batch of 2, lease them for 300 seconds
        batch = await claim_batch(session1, limit=2, lease_seconds=300)
        assert len(batch) == 2

        # Mark the first as sent (which commits session1's transaction)
        await mark_sent(session1, batch[0])

    async with test_session_factory() as session2:
        # Second session attempts claim_batch — it must return zero rows
        batch2 = await claim_batch(session2, limit=2)
        assert len(batch2) == 0

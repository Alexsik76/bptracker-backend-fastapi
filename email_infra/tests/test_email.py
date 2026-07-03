from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import pytest

from email_infra.sender import SmtpEmailSender


@pytest.mark.asyncio
async def test_send_text_email():
    sender = SmtpEmailSender(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="password",
        smtp_from="noreply@example.com",
        smtp_starttls=True,
    )

    with patch("email_infra.sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(
            to="recipient@example.com",
            subject="Test Subject",
            text="Hello, this is a plain text email.",
        )

        mock_send.assert_called_once()
        call_args, call_kwargs = mock_send.call_args
        message = call_args[0]

        assert isinstance(message, EmailMessage)
        assert message["From"] == "noreply@example.com"
        assert message["To"] == "recipient@example.com"
        assert message["Subject"] == "Test Subject"

        payload = message.get_payload()
        assert payload.strip() == "Hello, this is a plain text email."
        assert not message.is_multipart()


@pytest.mark.asyncio
async def test_send_html_email():
    sender = SmtpEmailSender(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="password",
        smtp_from="noreply@example.com",
        smtp_starttls=True,
    )

    with patch("email_infra.sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(
            to="recipient@example.com",
            subject="Test Subject",
            text="Hello, this is a plain text email.",
            html="<h1>Hello</h1><p>This is HTML.</p>",
        )

        mock_send.assert_called_once()
        call_args, call_kwargs = mock_send.call_args
        message = call_args[0]

        assert isinstance(message, EmailMessage)
        assert message.is_multipart()

        parts = list(message.iter_parts())
        assert len(parts) == 2

        assert parts[0].get_content_type() == "text/plain"
        assert parts[0].get_payload(decode=True).decode().strip() == (
            "Hello, this is a plain text email."
        )

        assert parts[1].get_content_type() == "text/html"
        assert parts[1].get_payload(decode=True).decode().strip() == (
            "<h1>Hello</h1><p>This is HTML.</p>"
        )


@pytest.mark.asyncio
async def test_transport_parameters():
    sender_starttls_true = SmtpEmailSender(
        smtp_host="smtp.custom.com",
        smtp_port=587,
        smtp_username="custom_user",
        smtp_password="custom_password",
        smtp_from="custom_from@custom.com",
        smtp_starttls=True,
        smtp_timeout=10,
    )

    with patch("email_infra.sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender_starttls_true.send(
            to="recipient@example.com",
            subject="Test",
            text="Hello",
        )

        mock_send.assert_called_once()
        _, call_kwargs = mock_send.call_args
        assert call_kwargs["hostname"] == "smtp.custom.com"
        assert call_kwargs["port"] == 587
        assert call_kwargs["username"] == "custom_user"
        assert call_kwargs["password"] == "custom_password"
        assert call_kwargs["start_tls"] is True
        assert call_kwargs["timeout"] == 10

    sender_starttls_false = SmtpEmailSender(
        smtp_host="smtp.custom.com",
        smtp_port=465,
        smtp_username="custom_user",
        smtp_password="custom_password",
        smtp_from="custom_from@custom.com",
        smtp_starttls=False,
        smtp_timeout=15,
    )

    with patch("email_infra.sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender_starttls_false.send(
            to="recipient@example.com",
            subject="Test",
            text="Hello",
        )

        mock_send.assert_called_once()
        _, call_kwargs = mock_send.call_args
        assert call_kwargs["start_tls"] is False
        assert call_kwargs["timeout"] == 15

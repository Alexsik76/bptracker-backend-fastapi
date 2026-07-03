from email.message import EmailMessage
from typing import Annotated, Protocol

import aiosmtplib
from fastapi import Depends

from config import Settings, get_settings


class EmailSender(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> None: ...


class SmtpEmailSender:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_from: str,
        smtp_starttls: bool = True,
        smtp_timeout: int = 10,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from
        self.smtp_starttls = smtp_starttls
        self.smtp_timeout = smtp_timeout

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> None:
        message = EmailMessage()
        message["From"] = self.smtp_from
        message["To"] = to
        message["Subject"] = subject

        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")

        await aiosmtplib.send(
            message,
            hostname=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_username,
            password=self.smtp_password,
            start_tls=self.smtp_starttls,
            timeout=self.smtp_timeout,
        )


def get_email_sender(settings: Annotated[Settings, Depends(get_settings)]) -> EmailSender:
    return SmtpEmailSender(
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        smtp_from=settings.smtp_from,
        smtp_starttls=settings.smtp_starttls,
        smtp_timeout=settings.smtp_timeout,
    )

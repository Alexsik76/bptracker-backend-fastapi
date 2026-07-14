from email.message import EmailMessage

import aiosmtplib
from sqlmodel.ext.asyncio.session import AsyncSession

from email_infra.crud import enqueue
from email_infra.types import EmailAttachment


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
        attachments: list[EmailAttachment] | None = None,
    ) -> None:
        message = EmailMessage()
        message["From"] = self.smtp_from
        message["To"] = to
        message["Subject"] = subject

        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")

        if attachments:
            for attachment in attachments:
                if "/" in attachment.content_type:
                    maintype, subtype = attachment.content_type.split("/", 1)
                else:
                    maintype, subtype = "application", attachment.content_type
                message.add_attachment(
                    attachment.content,
                    maintype=maintype,
                    subtype=subtype,
                    filename=attachment.filename,
                )

        await aiosmtplib.send(
            message,
            hostname=self.smtp_host,
            port=self.smtp_port,
            username=self.smtp_username,
            password=self.smtp_password,
            start_tls=self.smtp_starttls,
            timeout=self.smtp_timeout,
        )


class OutboxEmailSender:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        attachments: list[EmailAttachment] | None = None,
    ) -> None:
        await enqueue(
            self._session,
            to=to,
            subject=subject,
            body=text,
            attachments=attachments,
        )

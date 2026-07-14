from typing import Annotated

from fastapi import Depends

from config import Settings, get_settings
from email_infra.sender import SmtpEmailSender
from email_infra.types import EmailSender


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

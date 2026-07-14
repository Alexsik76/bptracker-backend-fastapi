from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str


class EmailSender(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        attachments: list[EmailAttachment] | None = None,
    ) -> None: ...

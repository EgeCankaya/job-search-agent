"""What the code knows about one Gmail message: its headers, and once it is
read, its text parts and any calendar invite.

These objects never go into logs (NFR-PRIV-3).
"""

import email
import email.policy
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr

from inbox_agent.calendar_invite import CalendarInvite, find_invite


@dataclass(frozen=True)
class EmailHeader:
    message_id: str
    sender: str  # the From header, "Name <address>" or a bare address
    subject: str
    received_at: datetime

    @property
    def sender_address(self) -> str:
        return parseaddr(self.sender)[1].lower()

    @property
    def sender_domain(self) -> str:
        return self.sender_address.rpartition("@")[2]


@dataclass(frozen=True)
class FetchedEmail:
    header: EmailHeader
    text: str | None  # the text/plain body, if any
    html: str | None  # the text/html body, if any
    invite: CalendarInvite | None  # the first text/calendar part's event (CLS-2)


def _body(message, subtype: str) -> str | None:
    part = message.get_body(preferencelist=(subtype,))
    if part is None:
        return None
    try:
        return part.get_content()
    except (LookupError, UnicodeError):  # an unknown or wrong charset
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes, *, message_id: str, received_at: datetime) -> FetchedEmail:
    """A raw RFC 822 message, as Gmail's format=raw returns it."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    header = EmailHeader(
        message_id=message_id,
        sender=str(message.get("From", "")),
        subject=str(message.get("Subject", "")),
        received_at=received_at,
    )
    return FetchedEmail(header=header, text=_body(message, "plain"), html=_body(message, "html"), invite=find_invite(message))

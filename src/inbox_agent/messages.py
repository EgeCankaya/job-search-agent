"""What the code knows about one Gmail message before it is read.

Header fields only. These objects never go into logs (NFR-PRIV-3).
"""

from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr


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

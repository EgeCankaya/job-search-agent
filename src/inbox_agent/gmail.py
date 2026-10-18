"""The Gmail interface the poller uses (ING-1).

The real client arrives with the owner's Google OAuth app and the
`gmail.readonly` grant (ING-4). Until then only the fake in the tests
implements it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class GmailMessageRef:
    """IDs and the receipt time only: what the poller needs, and no email text."""

    message_id: str
    thread_id: str
    received_at: datetime  # Gmail's internalDate, for NFR-LAT-2


class GmailClient(Protocol):
    def search(self, query: str) -> list[GmailMessageRef]:
        """Every message matching the query, across all result pages."""
        ...


def gmail_client() -> GmailClient:
    raise NotImplementedError(
        "No Gmail client yet: it needs the owner's Google OAuth app and the gmail.readonly grant (ING-4)."
    )

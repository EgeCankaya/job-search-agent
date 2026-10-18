"""TRK-1: the tracker row one classified email creates or updates.

Pure logic: the caller passes the tracker rows and writes the result back
(from week 3, through `upsert_application`). Rows get the extracted company
and role, dates and Gmail message IDs, never email text (NFR-PRIV-3).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from inbox_agent.classes import EmailClass
from inbox_agent.matching import company_key, match_application, role_key

# The status mapping (TRK-1, OQ-13). Recruiter outreach sets
# `recruiter_contact` on a new row only; "other" changes only last contact.
STATUS_FOR_CLASS = {
    EmailClass.INTERVIEW_INVITE: "interviewing",
    EmailClass.ASSESSMENT: "assessment",
    EmailClass.OFFER: "offer",
    EmailClass.REJECTION: "rejected",
}

# Automatic updates never replace these with an earlier stage (TRK-1).
FINAL_STATUSES = frozenset({"offer", "rejected", "withdrawn"})
EARLIER_STAGES = frozenset({"applied", "recruiter_contact", "assessment", "interviewing"})

# The dated events a key date can be (TRK-1 field table).
KEY_DATE_TYPES = {
    EmailClass.INTERVIEW_INVITE: "interview",
    EmailClass.ASSESSMENT: "assessment_deadline",
}


@dataclass(frozen=True)
class RowUpdate:
    item: dict  # the whole row to write back
    created: bool


def new_row_status(email_class: EmailClass) -> str:
    """A new row starts as `recruiter_contact` for outreach and `applied`
    otherwise; the mapping then moves it forward (an invite starts it at
    `interviewing`)."""
    base = "recruiter_contact" if email_class is EmailClass.RECRUITER_OUTREACH else "applied"
    return STATUS_FOR_CLASS.get(email_class, base)


def updated_status(current: str, email_class: EmailClass) -> str:
    target = STATUS_FOR_CLASS.get(email_class)
    if target is None:
        return current
    if current in FINAL_STATUSES and target in EARLIER_STAGES:
        return current
    return target


def update_for_email(
    rows: Iterable[Mapping],
    email_class: EmailClass,
    *,
    company: str,
    role: str | None,
    message_id: str,
    received_on: date,
    key_date: date | datetime | None = None,
    aliases: Mapping[str, str] | None = None,
) -> RowUpdate:
    """The row after one email: `company`, `role` and `key_date` come from
    extraction (CLS-2), `received_on` is the email's date.

    Processing the same message twice gives the same row.
    """
    if isinstance(received_on, datetime):
        # Which day a timestamp falls on depends on the time zone, so the
        # caller picks it; a timestamp would also break the date comparison.
        raise TypeError("received_on must be a date, not a datetime")
    existing = match_application(rows, company, role, aliases)
    if existing is None:
        item = {
            "company_key": company_key(company, aliases),
            "role_key": role_key(role),
            "company": company.strip(),
            "status": new_row_status(email_class),
        }
        if role and role.strip():
            item["role"] = role.strip()
    else:
        item = dict(existing)
        item["status"] = updated_status(item.get("status", "applied"), email_class)

    # Last contact is the latest inbound email, so an older email processed
    # late doesn't move it back. ISO dates compare as strings.
    item["last_contact"] = max(item.get("last_contact") or "", received_on.isoformat())

    ids = list(item.get("source_message_ids", []))
    if message_id not in ids:
        ids.append(message_id)
    item["source_message_ids"] = ids

    # A key date only when the email's status took effect: a late assessment
    # on a rejected row must not put a deadline on the dashboard.
    if key_date is not None and email_class in KEY_DATE_TYPES and item["status"] == STATUS_FOR_CLASS[email_class]:
        item["key_date"] = key_date.isoformat()
        item["key_date_type"] = KEY_DATE_TYPES[email_class]

    return RowUpdate(item=item, created=existing is None)

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
from inbox_agent.tracker import FOLLOW_UP, SKIPS_IN_A_ROW

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


# Statuses that only name a new row's starting point. An automatic update
# never sets them on an existing row (TRK-1: outreach sets
# `recruiter_contact` "for a new row only").
NEW_ROW_STATUSES = frozenset({"applied", "recruiter_contact"})


def status_for_class(email_class: EmailClass) -> str | None:
    """The status an email of this class asks for, or None ("other": only
    last contact changes). Outreach asks for `recruiter_contact`, which
    takes effect on a new row only."""
    if email_class is EmailClass.RECRUITER_OUTREACH:
        return "recruiter_contact"
    return STATUS_FOR_CLASS.get(email_class)


def new_row_status(email_class: EmailClass) -> str:
    """A new row starts as `recruiter_contact` for outreach and `applied`
    otherwise; the mapping then moves it forward (an invite starts it at
    `interviewing`)."""
    return status_for_class(email_class) or "applied"


def updated_status(current: str, email_class: EmailClass) -> str:
    return _automatic_status(current, STATUS_FOR_CLASS.get(email_class))


def _automatic_status(current: str, target: str | None) -> str:
    if target is None or target in NEW_ROW_STATUSES:
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
    return update_for_status(
        rows,
        status=status_for_class(email_class),
        company=company,
        role=role,
        message_id=message_id,
        received_on=received_on,
        key_date=key_date,
        key_date_type=KEY_DATE_TYPES.get(email_class),
        aliases=aliases,
    )


def update_for_status(
    rows: Iterable[Mapping],
    *,
    status: str | None,
    company: str,
    role: str | None,
    message_id: str,
    received_on: date,
    key_date: date | datetime | None = None,
    key_date_type: str | None = None,
    aliases: Mapping[str, str] | None = None,
) -> RowUpdate:
    """An automatic update from one email, as `upsert_application` gets it:
    the status the email's class maps to (None for "other"), never moving a
    row back from a final status (TRK-1)."""
    if isinstance(received_on, datetime):
        # Which day a timestamp falls on depends on the time zone, so the
        # caller picks it; a timestamp would also break the date comparison.
        raise TypeError("received_on must be a date, not a datetime")
    existing = match_application(rows, company, role, aliases)
    if existing is None:
        item = _new_row(company, role, status or "applied", aliases)
    else:
        item = dict(existing)
        item["status"] = _automatic_status(item.get("status", "applied"), status)

    # Last contact is the latest inbound email, so an older email processed
    # late doesn't move it back. ISO dates compare as strings.
    item["last_contact"] = max(item.get("last_contact") or "", received_on.isoformat())

    ids = list(item.get("source_message_ids", []))
    if message_id not in ids:
        ids.append(message_id)
        # A new email ends a skip streak (APR-4: "2 times in a row since its
        # last new email"; OQ-44 g).
        if item.get(FOLLOW_UP, {}).get(SKIPS_IN_A_ROW):
            item[FOLLOW_UP] = {**item[FOLLOW_UP], SKIPS_IN_A_ROW: 0}
    item["source_message_ids"] = ids

    # A key date only when the email's status took effect: a late assessment
    # on a rejected row must not put a deadline on the dashboard.
    if key_date is not None and key_date_type is not None and status is not None and item["status"] == status:
        item["key_date"] = key_date.isoformat()
        item["key_date_type"] = key_date_type

    return RowUpdate(item=item, created=existing is None)


def manual_update(
    rows: Iterable[Mapping],
    *,
    company: str,
    role: str | None,
    status: str | None = None,
    last_contact: date | None = None,
    next_step: str | None = None,
    key_date: date | datetime | None = None,
    key_date_type: str | None = None,
    aliases: Mapping[str, str] | None = None,
) -> RowUpdate:
    """A change I ask for on the Ask page: any status, in any direction
    (TRK-1). Only the fields given change."""
    existing = match_application(rows, company, role, aliases)
    item = _new_row(company, role, status or "applied", aliases) if existing is None else dict(existing)
    if status is not None:
        item["status"] = status
    if last_contact is not None:
        item["last_contact"] = last_contact.isoformat()
    if next_step is not None:
        item["next_step"] = next_step
    if key_date is not None:
        item["key_date"] = key_date.isoformat()
        item["key_date_type"] = key_date_type
    return RowUpdate(item=item, created=existing is None)


def _new_row(company: str, role: str | None, status: str, aliases: Mapping[str, str] | None) -> dict:
    item = {
        "company_key": company_key(company, aliases),
        "role_key": role_key(role),
        "company": company.strip(),
        "status": status,
    }
    if role and role.strip():
        item["role"] = role.strip()
    return item

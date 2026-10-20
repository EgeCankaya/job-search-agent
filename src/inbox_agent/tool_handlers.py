"""The six tools (TOOL-1, OQ-42 c and d, OQ-44 e), and the in-process
`Tools` that runs them.

Each handler gets only the dependency it needs: `read_email` the Gmail read
grant, `search_gmail` the read grant and the tracker, `upsert_application`
and `list_applications` the tracker, `notify_owner` the alert topic, and
`create_gmail_draft` the pending-draft records and the drafts-only client. In cloud mode each runs in its own Lambda
with a role to match (tool_lambda.py); in MacBook mode the local MCP server
runs the same code.

Results carry tracker fields and, for `read_email`, the trimmed email, which
go back to the caller. None of it goes into logs (NFR-PRIV-3).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from inbox_agent import settings
from inbox_agent.alerts import AlertTopic, build_alert, drafts_ready_alert
from inbox_agent.classes import URGENT_CLASSES, EmailClass
from inbox_agent.gmail import DraftsOnlyGmail, GmailClient, GmailError, draft_message
from inbox_agent.matching import company_key, is_open, match_application
from inbox_agent.pending_drafts import PendingDrafts, check_approved
from inbox_agent.query import build_query, company_search_query
from inbox_agent.tools import (
    DRAFTS_READY,
    OPEN,
    SEARCH_LIMIT,
    TOOL_SPECS,
    WEEK3_TOOLS,
    ToolInputError,
    ToolNotAllowed,
    ToolSpec,
    parse_date,
    parse_date_or_datetime,
    validate,
)
from inbox_agent.tracker import RowChanged, TrackerTable
from inbox_agent.tracker_update import manual_update, update_for_status
from inbox_agent.trim import trim_email

# What list_applications returns of a row: no message IDs, follow-up state,
# company domains or recruiter senders (OQ-42 d).
LISTED_FIELDS = ("company", "role", "status", "last_contact", "next_step", "key_date", "key_date_type")
# Whole-row writes that lost to a concurrent change are recomputed this often.
ROW_WRITE_ATTEMPTS = 3


def _today() -> date:
    return datetime.now(settings.OWNER_TIMEZONE).date()


@dataclass
class ToolDeps:
    tracker: TrackerTable | None = None
    gmail: GmailClient | None = None
    topic: AlertTopic | None = None
    pending: PendingDrafts | None = None
    drafts_gmail: DraftsOnlyGmail | None = None
    # The owner's IAM role ID, which the consumer records on an Approve (OQ-44 d).
    owner_role_id: Callable[[], str] = field(default=lambda: "")
    today: Callable[[], date] = field(default=_today)


def read_email(deps: ToolDeps, *, message_id: str) -> dict:
    fetched = deps.gmail.fetch(message_id)
    header = fetched.header
    # Trimmed here, so no path can hand a model the whole body (NFR-PRIV-2).
    trimmed = trim_email(
        subject=header.subject, sender=header.sender, date=header.received_at, text=fetched.text, html=fetched.html
    )
    return {
        "message_id": message_id,
        "sender": trimmed.sender,
        "subject": trimmed.subject,
        "received_at": trimmed.date,
        "text": trimmed.body,
        "calendar_start": fetched.invite.start.isoformat() if fetched.invite else None,
    }


def _row_summary(item: Mapping, *, created: bool, written: bool) -> dict:
    return {
        "company": item["company"],
        "role": item.get("role"),
        "status": item["status"],
        "created": created,
        "written": written,
    }


def upsert_application(
    deps: ToolDeps,
    *,
    company: str,
    role: str | None = None,
    status: str | None = None,
    last_contact: str | None = None,
    next_step: str | None = None,
    message_id: str | None = None,
    key_date: str | None = None,
    key_date_type: str | None = None,
) -> dict:
    if (key_date is None) != (key_date_type is None):
        raise ToolInputError("key_date and key_date_type go together")
    aliases = deps.tracker.aliases()
    try:
        company_key(company, aliases)
    except ValueError:
        raise ToolInputError("company is empty after normalization") from None
    if message_id is not None and last_contact is None:
        raise ToolInputError("an update from an email needs last_contact, the email's date")
    when = parse_date_or_datetime(key_date) if key_date is not None else None
    next_step = " ".join(next_step.split()) if next_step is not None else None

    # The approval workflow updates a row's follow-up state in place
    # (OQ-44 g), so a whole-row write is guarded; if the row moved since the
    # read, read it again.
    for _ in range(ROW_WRITE_ATTEMPTS):
        rows = deps.tracker.rows()
        if message_id is None:
            update = manual_update(
                rows,
                company=company,
                role=role,
                status=status,
                last_contact=parse_date(last_contact) if last_contact is not None else None,
                next_step=next_step,
                key_date=when,
                key_date_type=key_date_type,
                aliases=aliases,
            )
        else:
            existing = match_application(rows, company, role, aliases)
            if existing is not None and message_id in existing.get("source_message_ids", []):
                # This email already landed on the row (ING-2): nothing to write.
                return _row_summary(existing, created=False, written=False)
            update = update_for_status(
                rows,
                status=status,
                company=company,
                role=role,
                message_id=message_id,
                received_on=parse_date(last_contact),
                key_date=when,
                key_date_type=key_date_type,
                aliases=aliases,
            )
            if next_step is not None:
                update.item["next_step"] = next_step
        read = None if update.created else _same_row(rows, update.item)
        try:
            deps.tracker.put_row(update.item, read=read)
        except RowChanged:
            continue
        return _row_summary(update.item, created=update.created, written=True)
    raise RowChanged()


def _same_row(rows: list[dict], item: Mapping) -> dict:
    return next(r for r in rows if (r["company_key"], r["role_key"]) == (item["company_key"], item["role_key"]))


def list_applications(
    deps: ToolDeps,
    *,
    status: str | None = None,
    company: str | None = None,
    min_days_since_contact: int | None = None,
) -> dict:
    rows = deps.tracker.rows()
    if company is not None:
        try:
            wanted = company_key(company, deps.tracker.aliases())
        except ValueError:
            raise ToolInputError("company is empty after normalization") from None
        rows = [row for row in rows if row["company_key"] == wanted]
    if status == OPEN:
        rows = [row for row in rows if is_open(row)]
    elif status is not None:
        rows = [row for row in rows if row.get("status") == status]

    today = deps.today()
    listed = []
    for row in rows:
        last = row.get("last_contact")
        days = (today - date.fromisoformat(last)).days if last else None
        if min_days_since_contact is not None and (days is None or days < min_days_since_contact):
            continue
        entry = {key: row[key] for key in LISTED_FIELDS if row.get(key) is not None}
        entry["days_since_contact"] = days
        listed.append(entry)
    listed.sort(key=lambda entry: (entry["company"].casefold(), entry.get("role") or ""))
    return {"today": today.isoformat(), "count": len(listed), "applications": listed}


def notify_owner(
    deps: ToolDeps,
    *,
    kind: str,
    email_class: str | None = None,
    company: str | None = None,
    role: str | None = None,
    date: str | None = None,
    count: int | None = None,
) -> dict:
    if kind == DRAFTS_READY:
        # The count only (APR-2, OQ-44 f).
        if count is None:
            raise ToolInputError("drafts_ready needs count")
        if any(value is not None for value in (email_class, company, role, date)):
            raise ToolInputError("drafts_ready takes count only")
        deps.topic.publish(drafts_ready_alert(count))
        return {"published": True}
    if count is not None:
        raise ToolInputError("count goes with drafts_ready only")
    if email_class is None or company is None:
        raise ToolInputError("an email alert needs email_class and company")
    cls = EmailClass(email_class)
    possible = kind == "possible"
    # Urgent alerts are for the urgent classes; "Possible:" for the rest (ALR-1).
    if possible == (cls in URGENT_CLASSES):
        raise ToolInputError("kind doesn't fit email_class")
    alert = build_alert(
        cls,
        possible=possible,
        company=company,
        role=role,
        key_date=parse_date_or_datetime(date) if date is not None else None,
    )
    deps.topic.publish(alert)
    return {"published": True}


def search_gmail(deps: ToolDeps, *, company: str) -> dict:
    """One tracker company's newest job-search emails: IDs, thread, sender,
    time and Message-ID only (OQ-44 e). Only a company in the tracker can be
    searched, and always within the job-search query."""
    try:
        wanted = company_key(company, deps.tracker.aliases())
    except ValueError:
        raise ToolInputError("company is empty after normalization") from None
    rows = deps.tracker.rows()
    company_rows = [row for row in rows if row["company_key"] == wanted]
    if not company_rows:
        raise ToolInputError("no tracker row has that company")
    # `after:` isn't used, so any time will do for the job-search part.
    query = company_search_query(
        build_query(rows, datetime.now(settings.OWNER_TIMEZONE)), company_rows, company_rows[0]["company"]
    )
    found = deps.gmail.search_metadata(query, SEARCH_LIMIT) if query else []
    return {
        "count": len(found),
        "messages": [
            {
                "message_id": meta.message_id,
                "thread_id": meta.thread_id,
                "sender": meta.sender,
                "received_at": meta.received_at.isoformat(),
                "rfc822_message_id": meta.rfc822_message_id,
            }
            for meta in found
        ],
    }


def create_gmail_draft(deps: ToolDeps, *, approval_id: str) -> dict:
    """TOOL-4: a draft only for a record I approved, with the text I
    approved, once. Everything but the ID comes from the record (OQ-44 e)."""
    record = deps.pending.get(approval_id)
    approved = check_approved(record, deps.owner_role_id())
    message = draft_message(
        to_address=approved.to_address, subject=approved.subject, text=approved.text, in_reply_to=approved.in_reply_to
    )
    deps.pending.claim_for_draft(record)
    claimed = deps.pending.get(approval_id)
    try:
        draft_id = deps.drafts_gmail.create_draft(message, approved.thread_id)
    except GmailError:
        # Nothing was saved; the workflow's retry may try again.
        deps.pending.release_claim(claimed)
        raise
    deps.pending.mark_draft_saved(claimed, draft_id)
    return {"approval_id": approval_id, "draft_id": draft_id, "saved": True}


HANDLERS: dict[str, Callable[..., dict]] = {
    "read_email": read_email,
    "upsert_application": upsert_application,
    "list_applications": list_applications,
    "notify_owner": notify_owner,
    "search_gmail": search_gmail,
    "create_gmail_draft": create_gmail_draft,
}


class InProcessTools:
    """The tools run in this process, limited to `names`. The tool Lambdas,
    the local MCP server and the tests use it."""

    def __init__(self, deps: ToolDeps, names: tuple[str, ...] = WEEK3_TOOLS) -> None:
        unknown = set(names) - set(HANDLERS)
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        self._deps = deps
        self._names = tuple(names)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(TOOL_SPECS[name] for name in self._names)

    def only(self, *names: str) -> "InProcessTools":
        """A narrower set: the chat path gets `list_applications` only (OQ-42 h)."""
        if not set(names) <= set(self._names):
            raise ToolNotAllowed("can only narrow the tool set")
        return InProcessTools(self._deps, names)

    def call(self, name: str, arguments: Mapping) -> dict:
        if name not in self._names:
            raise ToolNotAllowed(f"{name} is not available here")
        return HANDLERS[name](self._deps, **validate(TOOL_SPECS[name], arguments))

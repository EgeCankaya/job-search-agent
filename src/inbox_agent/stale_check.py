"""APR-1 and APR-2: the daily stale check, a fixed sequence like the batch
path (OQ-42 b, OQ-44 c).

    select rows (code) -> per row: search_gmail -> read_email -> draft (model) -> checks
    -> notify_owner drafts_ready (once a day) -> the drafts, to the queue consumer

Eligibility is code (APR-4, eligibility.py) over a direct tracker read
(OQ-44 g); the model writes the text only. The recipient is the newest
sender at the company who isn't an automated address, and comes from Gmail's
headers, never from the model (APR-2). With no such sender, To is empty and
the draft starts a new thread.

The consumer starts one approval workflow per returned draft (OQ-44 a);
nothing here writes a pending-draft record. `approval_id` depends only on
the row and the check's date, so a redelivered check proposes nothing twice.

Results hold draft text and addresses; logs get counts only (NFR-PRIV-3).
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from inbox_agent.eligibility import select_for_check
from inbox_agent.followup_draft import (
    DEFAULT_LANGUAGE,
    DraftRejected,
    FollowUpRequest,
    new_thread_subject,
    reply_subject,
    write_follow_up,
)
from inbox_agent.model import Model
from inbox_agent.processed_log import ProcessedLog
from inbox_agent.tools import DRAFTS_READY, Tools

# APR-2: senders that are never the person to reply to.
AUTOMATED_PREFIXES = ("noreply", "no-reply", "donotreply", "mailer-daemon")


def is_person(address: str) -> bool:
    local = address.rpartition("@")[0].lower()
    return bool(local) and not local.startswith(AUTOMATED_PREFIXES)


def approval_id(company_key: str, role_key: str, check_date: date) -> str:
    return hashlib.sha256(f"{company_key}\n{role_key}\n{check_date.isoformat()}".encode()).hexdigest()[:32]


def notice_key(check_date: date) -> str:
    return f"#drafts-ready-{check_date.isoformat()}"


@dataclass
class StaleCheckDeps:
    tools: Tools  # search_gmail, read_email, notify_owner
    model: Model  # the chat model (Nova Lite)
    log: ProcessedLog
    tracker_rows: Callable[[], list[dict]]
    now: Callable[[], datetime]
    # MEM-2: my tone preference, and the company's summary by company key.
    tone: Callable[[], str | None] = field(default=lambda: None)
    summary: Callable[[str], str | None] = field(default=lambda company_key: None)


def _draft_for(row: Mapping, deps: StaleCheckDeps, check_date: date) -> dict:
    found = deps.tools.call("search_gmail", {"company": row["company"]})["messages"]
    person = next((m for m in found if is_person(m["sender"])), None)
    # The language comes from the person's email, else the company's latest
    # email of any sender, else English (OQ-44 m).
    source = person or (found[0] if found else None)
    email = deps.tools.call("read_email", {"message_id": source["message_id"]}) if source else None
    request = FollowUpRequest(
        company=row["company"],
        role=row.get("role"),
        last_contact=date.fromisoformat(row["last_contact"]),
        email=email,
        tone=deps.tone(),
        summary=deps.summary(row["company_key"]),
    )
    follow_up = write_follow_up(request, deps.model)
    if person is not None:
        to_address, thread_id = person["sender"], person["thread_id"]
        in_reply_to = person["rfc822_message_id"] or None
        subject = reply_subject(email["subject"])
    else:
        to_address, thread_id, in_reply_to = "", None, None
        subject = new_thread_subject(row.get("role"), row["company"], follow_up.language)
    return {
        "approval_id": approval_id(row["company_key"], row["role_key"], check_date),
        "company": row["company"],
        "role": row.get("role"),
        "company_key": row["company_key"],
        "role_key": row["role_key"],
        "last_contact": row["last_contact"],
        "proposed_on": check_date.isoformat(),
        "to_address": to_address,
        "thread_id": thread_id,
        "in_reply_to": in_reply_to,
        "subject": subject,
        "language": follow_up.language if email is not None else DEFAULT_LANGUAGE,
        "text": follow_up.text,
    }


def run_stale_check(deps: StaleCheckDeps, check_date: date) -> dict:
    """The drafts for today's check, and counts for the log."""
    chosen = select_for_check(deps.tracker_rows(), check_date)
    drafts, rejected = [], 0
    for row in chosen:
        try:
            drafts.append(_draft_for(row, deps, check_date))
        except DraftRejected:
            # The row stays eligible and comes up tomorrow.
            rejected += 1
    notified = False
    if drafts and deps.log.claim_notice(notice_key(check_date), deps.now()):
        try:
            deps.tools.call("notify_owner", {"kind": DRAFTS_READY, "count": len(drafts)})
        except Exception:
            deps.log.release_notice(notice_key(check_date))
            raise
        notified = True
    return {
        "check_date": check_date.isoformat(),
        "eligible": len(chosen),
        "rejected": rejected,
        "notified": notified,
        "drafts": drafts,
    }

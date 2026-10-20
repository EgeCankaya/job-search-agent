"""APR-1 and APR-4: which applications the daily check proposes a follow-up
for.

Pure logic on tracker rows and the check's date (Europe/Istanbul, as the
schedule fires at 09:00 there). A row is eligible only if all hold:
- it is open, and its last contact was 10 or more days ago;
- it was not skipped, expired or approved in the last 10 days (an approval
  counts from the draft date, as the poller can't see my sent mail), and no
  proposal of the last 10 days is still waiting for my decision;
- it has not been skipped 2 times in a row since its last new email.

A check proposes at most 5, oldest last contact first; the rest stay
eligible for the next day (OQ-44 c).
"""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from inbox_agent.matching import is_open
from inbox_agent.tracker import (
    FOLLOW_UP,
    LAST_DRAFT,
    LAST_EXPIRED,
    LAST_PROPOSED,
    LAST_SKIPPED,
    SKIPS_IN_A_ROW,
)

STALE_AFTER = timedelta(days=10)
COOL_DOWN = timedelta(days=10)
MAX_SKIPS_IN_A_ROW = 2
MAX_PROPOSALS_PER_CHECK = 5

# Every way a proposal ends starts the cool-down, and so does a proposal
# still waiting: a decision comes within 7 days (APR-3), so a pending one
# is always inside the cool-down of its own proposal date.
COOL_DOWN_DATES = (LAST_PROPOSED, LAST_SKIPPED, LAST_EXPIRED, LAST_DRAFT)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None  # a broken value never makes a row eligible by itself


def is_eligible(row: Mapping, today: date) -> bool:
    last_contact = _date(row.get("last_contact"))
    if not is_open(row) or last_contact is None or today - last_contact < STALE_AFTER:
        return False
    follow_up = row.get(FOLLOW_UP) or {}
    for name in COOL_DOWN_DATES:
        when = _date(follow_up.get(name))
        if follow_up.get(name) and when is None:
            return False
        if when is not None and today - when < COOL_DOWN:
            return False
    return int(follow_up.get(SKIPS_IN_A_ROW, 0)) < MAX_SKIPS_IN_A_ROW


def select_for_check(rows: Iterable[Mapping], today: date, limit: int = MAX_PROPOSALS_PER_CHECK) -> list[Mapping]:
    """The rows one check proposes: eligible ones, oldest last contact first,
    then by key so the order is stable."""
    eligible = [row for row in rows if is_eligible(row, today)]
    eligible.sort(key=lambda row: (row["last_contact"], row["company_key"], row["role_key"]))
    return eligible[:limit]

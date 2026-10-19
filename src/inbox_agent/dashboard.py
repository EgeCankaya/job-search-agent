"""DASH-1: the numbers on the Mac app's Dashboard page.

Pure functions over tracker rows and alert claims, so every number can be
checked against a direct count on a seeded table. Reading them is the app
backend's job (app_backend.py), and it only scans: the Dashboard never
writes.

"This week" is the calendar week in the owner's time zone, Monday to now.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from inbox_agent import settings
from inbox_agent.matching import is_open
from inbox_agent.tracker import STATUSES

SILENT_DAYS = 10  # APR-1's threshold, and the Dashboard's "silent" list
UPCOMING_DAYS = 7


@dataclass(frozen=True)
class Silent:
    company: str
    role: str | None
    status: str
    last_contact: date
    days: int


@dataclass(frozen=True)
class Upcoming:
    company: str
    role: str | None
    when: date
    when_text: str  # the stored ISO value, which may carry a time
    kind: str | None


@dataclass(frozen=True)
class Dashboard:
    total: int
    open: int
    by_status: dict[str, int]
    silent: list[Silent] = field(default_factory=list)
    upcoming: list[Upcoming] = field(default_factory=list)
    alerts_this_week: dict[str, int] = field(default_factory=dict)


def week_start(now: datetime) -> datetime:
    """Monday 00:00 of `now`'s week, in the owner's time zone."""
    local = now.astimezone(settings.OWNER_TIMEZONE)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time(0), settings.OWNER_TIMEZONE)


def _key_day(value: str) -> date:
    """A stored key date's day: a plain date, or a date-time's day in the
    owner's time zone (a naive time is taken as the owner's wall time)."""
    if "T" not in value:
        return date.fromisoformat(value)
    moment = datetime.fromisoformat(value)
    return moment.astimezone(settings.OWNER_TIMEZONE).date() if moment.tzinfo else moment.date()


def build_dashboard(
    rows: Iterable[Mapping],
    alert_claims: Iterable[tuple[datetime, str | None]],
    *,
    now: datetime,
) -> Dashboard:
    rows = list(rows)
    today = now.astimezone(settings.OWNER_TIMEZONE).date()
    statuses = Counter(row.get("status", "applied") for row in rows)

    silent = []
    for row in rows:
        if not is_open(row) or not row.get("last_contact"):
            continue
        last = date.fromisoformat(row["last_contact"])
        days = (today - last).days
        if days >= SILENT_DAYS:
            silent.append(Silent(row["company"], row.get("role"), row["status"], last, days))
    silent.sort(key=lambda s: -s.days)

    upcoming = []
    for row in rows:
        if not row.get("key_date"):
            continue
        day = _key_day(row["key_date"])
        if today <= day <= today + timedelta(days=UPCOMING_DAYS):
            upcoming.append(Upcoming(row["company"], row.get("role"), day, row["key_date"], row.get("key_date_type")))
    upcoming.sort(key=lambda u: u.when_text)

    start = week_start(now)
    kinds = Counter(kind or "unrecorded" for at, kind in alert_claims if at >= start)
    alerts = {"urgent": kinds["urgent"], "possible": kinds["possible"]}
    if kinds["unrecorded"]:
        alerts["unrecorded"] = kinds["unrecorded"]

    return Dashboard(
        total=len(rows),
        open=sum(1 for row in rows if is_open(row)),
        by_status={status: statuses[status] for status in STATUSES},
        silent=silent,
        upcoming=upcoming,
        alerts_this_week=alerts,
    )

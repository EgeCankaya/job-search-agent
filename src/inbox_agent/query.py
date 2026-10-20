"""ING-1: the narrow Gmail query.

Four parts, combined with OR:
1. senders from the ATS domain list in settings;
2. the company domains stored on tracker rows;
3. the recruiter senders stored on tracker rows;
4. the English and Turkish subject keywords in settings.

The result is limited to `after:` the last successful poll minus a 1-hour
overlap, and excludes the alert sender, so the agent never reads its own
alerts, and the owner's own mail and drafts, so follow-ups are never read
back as replies (OQ-44 l).

`GmailQuery.matches` evaluates the query locally against an email's headers.
It backs the unit tests and the M8 coverage hook (`query_coverage`); it is an
approximation of Gmail's matching, so a real M8 run should confirm misses
against Gmail itself.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from inbox_agent import settings
from inbox_agent.classes import URGENT_CLASSES, EmailClass
from inbox_agent.messages import EmailHeader

POLL_OVERLAP = timedelta(hours=1)

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_LOCAL_PART = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`|~-]{1,64}$")
_WORD = re.compile(r"\w+")


def normalize_domain(value: str) -> str:
    """A bare lowercase domain. Raises ValueError for anything else.

    Strict on purpose: these values end up inside the Gmail query, and a value
    with spaces or operators could widen it (NFR-PRIV-1).
    """
    domain = value.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain).split("/")[0].lstrip("@")
    domain = domain.removeprefix("www.")
    if not _DOMAIN.match(domain):
        raise ValueError("not a domain")
    return domain


def normalize_address(value: str) -> str:
    """A bare lowercase email address. Raises ValueError for anything else."""
    local, at, domain = value.strip().lower().rpartition("@")
    if not at or not _LOCAL_PART.match(local) or not _DOMAIN.match(domain):
        raise ValueError("not an email address")
    return f"{local}@{domain}"


def _words(text: str) -> list[str]:
    # "İ".casefold() leaves a combining dot behind; fold it to "i" first.
    return _WORD.findall(text.replace("İ", "i").casefold())


@dataclass(frozen=True)
class GmailQuery:
    sender_domains: tuple[str, ...]
    sender_addresses: tuple[str, ...]
    subject_keywords: tuple[str, ...]
    after: datetime
    excluded_sender: str = settings.ALERT_SENDER
    skipped_values: int = 0  # tracker values dropped because they weren't clean
    # Gmail knows "me"; a local check of labeled mail needs the address.
    own_addresses: tuple[str, ...] = ()

    def to_gmail(self) -> str:
        terms = [f"from:{domain}" for domain in self.sender_domains]
        terms += [f"from:{address}" for address in self.sender_addresses]
        terms += [f"subject:{keyword}" for keyword in self.subject_keywords]
        return (
            f"({' OR '.join(terms)}) after:{int(self.after.timestamp())} "
            f"{settings.OWN_MAIL_EXCLUSION} -from:{self.excluded_sender}"
        )

    def matches(self, email: EmailHeader) -> bool:
        if email.received_at < self.after or email.sender_address in (self.excluded_sender, *self.own_addresses):
            return False
        domain = email.sender_domain
        if any(domain == d or domain.endswith("." + d) for d in self.sender_domains):
            return True
        if email.sender_address in self.sender_addresses:
            return True
        subject_words = set(_words(email.subject))
        return any(set(_words(keyword)) <= subject_words for keyword in self.subject_keywords)


def _clean(values: Iterable[str], normalize: Callable[[str], str]) -> tuple[set[str], int]:
    clean, skipped = set(), 0
    for value in values:
        try:
            clean.add(normalize(value))
        except (ValueError, AttributeError):
            skipped += 1
    return clean, skipped


def build_query(
    tracker_rows: Iterable[Mapping],
    last_successful_poll: datetime,
    *,
    ats_domains: Iterable[str] = settings.ATS_DOMAINS,
    subject_keywords: Iterable[str] = settings.SUBJECT_KEYWORDS,
) -> GmailQuery:
    rows = list(tracker_rows)
    domains, skipped_domains = _clean(
        [*ats_domains, *(d for row in rows for d in row.get("company_domains", []))], normalize_domain
    )
    addresses, skipped_addresses = _clean(
        (a for row in rows for a in row.get("recruiter_senders", [])), normalize_address
    )
    # A recruiter whose domain is already in the query adds nothing.
    addresses = {a for a in addresses if a.rpartition("@")[2] not in domains}
    return GmailQuery(
        sender_domains=tuple(sorted(domains)),
        sender_addresses=tuple(sorted(addresses)),
        subject_keywords=tuple(dict.fromkeys(k.strip().casefold() for k in subject_keywords)),
        after=last_successful_poll - POLL_OVERLAP,
        skipped_values=skipped_domains + skipped_addresses,
    )


# M8 hook: query coverage on the labeled set (ING-1, §11). Runs later, on the
# Mac, against EV-1.


@dataclass(frozen=True)
class LabeledEmail:
    header: EmailHeader
    label: EmailClass


@dataclass(frozen=True)
class Coverage:
    urgent: int
    missed: tuple[str, ...]  # message IDs of urgent emails the query didn't return

    @property
    def rate(self) -> float:
        return 1.0 if not self.urgent else (self.urgent - len(self.missed)) / self.urgent


def query_coverage(labeled: Iterable[LabeledEmail], is_returned: Callable[[EmailHeader], bool]) -> Coverage:
    """M8: the share of labeled interview invites, assessments and offers the query returns.

    `is_returned` is the query built from that email's day of config and
    tracker: `GmailQuery.matches`, or a check against Gmail's own results.
    """
    urgent = [email for email in labeled if email.label in URGENT_CLASSES]
    missed = tuple(email.header.message_id for email in urgent if not is_returned(email.header))
    return Coverage(urgent=len(urgent), missed=missed)

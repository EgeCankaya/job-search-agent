"""NFR-PRIV-3's check (OQ-45 h): no email text in CloudWatch Logs.

1. The message IDs logged as processed in the last 7 days, from the
   processed-email log (retention is 7 days, so that is all there is).
2. Each one's `Subject` and `From` headers from Gmail (`format=metadata`),
   with the read token as `inbox-agent-poller`, through the same AgentCore
   Identity calls as the poller.
3. `FilterLogEvents` over every log group in the account, for each subject,
   sender address and sender name. Each value is searched as its fragments
   of plain ASCII without quotes or backslashes, as exact quoted phrases:
   a fragment reads the same in raw text and in the escaped form
   `json.dumps` writes for Turkish letters, and needs no escaping, whose
   rules inside a filter pattern AWS's docs leave unclear.
4. Counts per log group, and how many fragments were checked.

It never prints a subject, a name or an address: they stay in this
process's memory, and leave the Mac only as search terms sent to this
account's CloudWatch. Fragments shorter than MIN_LENGTH are skipped (and
counted), as they would match unrelated lines.

Run with the owner's `aws login` session, from the repo root:
`uv run python scan_logs.py`. Exit 0 when every count is 0.
"""

import argparse
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from inbox_agent import settings
from inbox_agent.gmail import AgentCoreGmailClient

WINDOW = timedelta(days=7)
MIN_LENGTH = 8
# FilterLogEvents takes a pattern of at most 1024 characters; terms are OR-ed.
PATTERN_LIMIT = 1000
MAX_EVENTS_PER_SEARCH = 1000


class HeaderReader(AgentCoreGmailClient):
    """The poller's Gmail client, asking for two headers and nothing else."""

    def token(self) -> str:
        return self._token()

    def headers(self, message_id: str, token: str) -> dict[str, str]:
        meta = self._get(
            token,
            f"messages/{message_id}",
            {"format": "metadata", "metadataHeaders": ["Subject", "From"], "fields": "payload/headers"},
        )
        return {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}


def processed_ids(table, since: datetime) -> list[str]:
    """Message IDs logged at or after `since`; the log's own bookkeeping
    items (`#poll-state`, `#drafts-ready-…`) are not emails."""
    ids, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            if not item["message_id"].startswith("#") and int(item.get("logged_at", 0)) >= int(since.timestamp()):
                ids.append(item["message_id"])
        if "LastEvaluatedKey" not in page:
            return sorted(ids)
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def values_for(headers: dict[str, str]) -> list[str]:
    """The subject, the sender's address and the sender's name."""
    name, address = parseaddr(headers.get("from", ""))
    return [" ".join(value.split()) for value in (headers.get("subject", ""), address, name) if value and value.strip()]


# Anything but printable ASCII (which json.dumps escapes), a quote or a backslash.
_SPLIT = re.compile(r'[^\x20-\x7e]|["\\]')


def search_forms(values: Iterable[str]) -> tuple[list[str], int]:
    """The values' searchable fragments, and how many were too short."""
    forms, skipped = [], 0
    for value in dict.fromkeys(values):
        for fragment in (part.strip() for part in _SPLIT.split(value)):
            if len(fragment) >= MIN_LENGTH:
                forms.append(fragment)
            elif fragment:
                skipped += 1
    return list(dict.fromkeys(forms)), skipped


def term(fragment: str) -> str:
    """An exact-phrase term for a filter pattern; fragments hold no quote or
    backslash, so nothing needs escaping."""
    return f'"{fragment}"'


def patterns(forms: list[str]) -> Iterator[str]:
    """OR-ed terms (`?"a" ?"b"`), each pattern within the size limit."""
    batch: list[str] = []
    for form in forms:
        piece = "?" + term(form)
        if len(piece) > PATTERN_LIMIT:
            piece = "?" + term(form[: PATTERN_LIMIT // 2])
        if batch and len(" ".join([*batch, piece])) > PATTERN_LIMIT:
            yield " ".join(batch)
            batch = []
        batch.append(piece)
    if batch:
        yield " ".join(batch)


def log_groups(logs) -> list[str]:
    names = []
    for page in logs.get_paginator("describe_log_groups").paginate():
        names += [group["logGroupName"] for group in page.get("logGroups", [])]
    return names


def count_matches(logs, group: str, pattern: str, start: datetime) -> int:
    found, kwargs = 0, {}
    while found < MAX_EVENTS_PER_SEARCH:
        page = logs.filter_log_events(logGroupName=group, filterPattern=pattern, startTime=int(start.timestamp() * 1000), **kwargs)
        found += len(page.get("events", []))
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    return found


@dataclass(frozen=True)
class ScanResult:
    emails: int
    unreadable: int  # emails Gmail no longer has
    values: int
    skipped_short: int
    counts: dict[str, int]

    @property
    def clean(self) -> bool:
        return not any(self.counts.values())


def scan(*, table, gmail: HeaderReader, logs, now: datetime) -> ScanResult:
    since = now - WINDOW
    ids = processed_ids(table, since)
    values, unreadable = [], 0
    token = gmail.token() if ids else None
    for message_id in ids:
        try:
            values += values_for(gmail.headers(message_id, token))
        except Exception:
            unreadable += 1  # deleted since, for example
    forms, skipped = search_forms(values)
    counts = {}
    for group in log_groups(logs):
        counts[group] = sum(count_matches(logs, group, pattern, since) for pattern in patterns(forms))
    return ScanResult(len(ids), unreadable, len(forms), skipped, counts)


def report(result: ScanResult) -> list[str]:
    lines = [
        f"processed emails in the last 7 days: {result.emails} ({result.unreadable} no longer in Gmail)",
        f"fragments searched: {result.values} (from subjects, sender addresses and names); "
        f"{result.skipped_short} too short to search",
    ]
    if not result.emails:
        lines.append("no processed emails this week: nothing to search for")
    lines += [f"{count:6d}  {group}" for group, count in sorted(result.counts.items())]
    lines.append("CLEAN: no email text in any log group" if result.clean else "MATCHES FOUND: see the counts above")
    return lines


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)

    import boto3

    from inbox_agent.app_backend import discover

    session = boto3.Session(region_name=settings.AWS_REGION)
    resources = discover(session.client("cloudformation"), mode="local")
    result = scan(
        table=session.resource("dynamodb").Table(resources.processed_table),
        gmail=HeaderReader(session.client("bedrock-agentcore")),
        logs=session.client("logs"),
        now=datetime.now(UTC),
    )
    print("\n".join(report(result)))
    return 0 if result.clean else 1

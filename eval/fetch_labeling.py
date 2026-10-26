"""EV-1 (OQ-47 (d)): fetch one date window's job-search email into the
labeled set on the Mac, following the §12 sampling rule.

    uv run python eval/fetch_labeling.py --after 2026-09-01 --before 2026-10-01

For the window it stores:
- (a) every email the ING-1 query returns (today's ATS domains, tracker
  domains, recruiter senders and subject keywords), as found_by=query;
- (b) every email I put the Gmail label `eval-manual` on while reading the
  whole inbox for that window myself, as found_by=manual. An email the query
  also returned stays found_by=query: only the query's misses count against
  M8.

Gmail's web addresses don't show the API's message IDs, so the label is how
(b) reaches the script. Dates are midnight in Istanbul; `--before` is
exclusive. The read token is the poller's (`inbox-agent-poller`), through my
`aws login` session, as `scan_logs.py` uses it. The script prints counts
only, never a subject, a sender or an ID. `--dry-run` counts without saving.
"""

import argparse
import base64
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.labeled_set import LabeledSet, data_dir  # noqa: E402
from inbox_agent import settings  # noqa: E402
from inbox_agent.gmail import _MESSAGE_ID, AgentCoreGmailClient, GmailError  # noqa: E402
from inbox_agent.query import build_query  # noqa: E402

ISTANBUL = ZoneInfo("Europe/Istanbul")
MANUAL_LABEL = "eval-manual"
# A Gmail label name that can't widen the search: no spaces, quotes or operators.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_/-]{0,99}$")


def window_start(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), ISTANBUL)


def _bounds(after: datetime, before: datetime) -> str:
    return f"after:{int(after.timestamp())} before:{int(before.timestamp())}"


def window_query(tracker_rows, after: datetime, before: datetime) -> str:
    """The ING-1 query over [after, before): the poller's query, with the
    window in place of `after:` (last poll - 1 h)."""
    query = replace(build_query(tracker_rows, after), after=after)
    return f"{query.job_search_group()} {_bounds(after, before)} {query.exclusions()}"


def manual_query(label: str, after: datetime, before: datetime) -> str:
    if not _LABEL.match(label):
        raise ValueError("not a plain Gmail label name")
    return f"label:{label} {_bounds(after, before)}"


@dataclass
class RawReader(AgentCoreGmailClient):
    """The read client, with the token kept for the run and the raw message
    returned as bytes, so the set keeps exactly what Gmail holds."""

    _cached: str | None = field(default=None, init=False, repr=False)

    def _token(self) -> str:
        if self._cached is None:
            self._cached = super()._token()
        return self._cached

    def fetch_raw(self, message_id: str) -> tuple[bytes, str, datetime]:
        if not _MESSAGE_ID.match(message_id):
            raise GmailError("not a Gmail message ID")
        message = self._get(
            self._token(), f"messages/{message_id}", {"format": "raw", "fields": "id,threadId,internalDate,raw"}
        )
        raw = base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4))
        return raw, message["threadId"], datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC)


@dataclass
class FetchCounts:
    query_matched: int = 0
    query_new: int = 0
    manual_matched: int = 0
    manual_new: int = 0
    manual_also_query: int = 0  # labeled by me, but the query returned it too

    def lines(self, store: LabeledSet | None) -> list[str]:
        lines = [
            f"query:  {self.query_matched} matched, {self.query_new} new",
            f"manual: {self.manual_matched} labeled {MANUAL_LABEL}, {self.manual_new} new, "
            f"{self.manual_also_query} also returned by the query",
        ]
        if store is not None:
            progress = store.progress()
            found = progress["by_found_by"]
            lines.append(
                f"set:    {progress['emails']} emails ({found['query']} query, {found['manual']} manual), "
                f"{progress['labeled']} labeled"
            )
        return lines


def fetch_window(
    store: LabeledSet | None, gmail: RawReader, *, query: str, manual: str, window: str, now=None
) -> FetchCounts:
    """Fetch the query's matches, then my manual finds. With no store (a dry
    run) it only counts."""
    counts = FetchCounts()
    known = set(store.by_gmail_id()) if store else set()
    query_refs = gmail.search(query)
    query_ids = {ref.message_id for ref in query_refs}
    counts.query_matched = len(query_refs)
    for ref in query_refs:
        if ref.message_id in known:
            continue
        counts.query_new += 1
        if store:
            raw, thread_id, received_at = gmail.fetch_raw(ref.message_id)
            store.add_email(
                raw, gmail_id=ref.message_id, thread_id=thread_id, received_at=received_at,
                found_by="query", window=window, now=now,
            )
            known.add(ref.message_id)

    manual_refs = gmail.search(manual)
    counts.manual_matched = len(manual_refs)
    for ref in manual_refs:
        if ref.message_id in query_ids:
            counts.manual_also_query += 1
            continue
        if ref.message_id in known:
            continue
        counts.manual_new += 1
        if store:
            raw, thread_id, received_at = gmail.fetch_raw(ref.message_id)
            store.add_email(
                raw, gmail_id=ref.message_id, thread_id=thread_id, received_at=received_at,
                found_by="manual", window=window, now=now,
            )
            known.add(ref.message_id)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--after", type=date.fromisoformat, required=True, help="first day, YYYY-MM-DD")
    parser.add_argument("--before", type=date.fromisoformat, required=True, help="day after the last, YYYY-MM-DD")
    parser.add_argument("--manual-label", default=MANUAL_LABEL)
    parser.add_argument("--dry-run", action="store_true", help="count only; save nothing")
    args = parser.parse_args(argv)
    if args.before <= args.after:
        parser.error("--before must be after --after")

    import boto3

    from inbox_agent.app_backend import discover
    from inbox_agent.tracker import TrackerTable

    after, before = window_start(args.after), window_start(args.before)
    session = boto3.Session(region_name=settings.AWS_REGION)
    resources = discover(session.client("cloudformation"), mode="local")
    rows = TrackerTable(session.resource("dynamodb").Table(resources.tracker_table)).rows()
    store = None if args.dry_run else LabeledSet(data_dir()).ensure()
    counts = fetch_window(
        store,
        RawReader(session.client("bedrock-agentcore")),
        query=window_query(rows, after, before),
        manual=manual_query(args.manual_label, after, before),
        window=f"{args.after.isoformat()}..{args.before.isoformat()}",
    )
    print("\n".join(counts.lines(store)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

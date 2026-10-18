"""The scheduled Gmail poll (ING-1, ING-2, ING-3), run by Lambda in cloud mode.

One poll:
1. builds the query from settings and the tracker, from the last successful
   poll minus 1 hour;
2. searches Gmail with exactly that query;
3. skips every message already in the processed-email log;
4. puts each new message on the agent queue, then logs it;
5. records the poll's start time as the last successful poll.

A failure at any step leaves the last-poll time alone, so the next poll
covers the same window. A message is logged only after it is queued, so a
crash in between can queue it twice but never lose it; the agent's own
dedup (ING-2) covers that case.

Queue messages and logs carry IDs, counts and times only, never email text
or the query (NFR-PRIV-3).

A dry run (`{"dry_run": true}`, invoked by hand) does steps 1 to 3 and
reports what it would queue, but writes nothing: no queue message, no log
entry, no last-poll time. It checks the Gmail and IAM path in AWS while
nothing reads the queue yet.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from inbox_agent import settings
from inbox_agent.gmail import GmailClient, gmail_client
from inbox_agent.processed_log import ProcessedLog
from inbox_agent.query import build_query
from inbox_agent.tracker import TrackerTable

logger = logging.getLogger()
logger.setLevel(logging.INFO)

POLL_INTERVAL = timedelta(minutes=30)
NEW_EMAIL_MESSAGE = "new_email"


@dataclass(frozen=True)
class AgentQueue:
    client: object  # boto3 SQS client
    url: str

    def send(self, body: dict) -> None:
        self.client.send_message(QueueUrl=self.url, MessageBody=json.dumps(body))


@dataclass(frozen=True)
class PollResult:
    found: int
    skipped: int
    enqueued_ids: tuple[str, ...] = field(default_factory=tuple)
    query_values_skipped: int = 0
    dry_run: bool = False


def poll(
    *,
    gmail: GmailClient,
    tracker: TrackerTable,
    log: ProcessedLog,
    queue: AgentQueue,
    now: datetime,
    dry_run: bool = False,
) -> PollResult:
    # With no earlier poll, assume one ran an interval ago.
    since = log.last_successful_poll() or now - POLL_INTERVAL
    query = build_query(tracker.rows(), since)
    refs = list({ref.message_id: ref for ref in gmail.search(query.to_gmail())}.values())

    enqueued = []
    for ref in refs:
        if log.contains(ref.message_id):
            continue
        if dry_run:
            enqueued.append(ref.message_id)
            continue
        queue.send(
            {
                "type": NEW_EMAIL_MESSAGE,
                "message_id": ref.message_id,
                "thread_id": ref.thread_id,
                "received_at": ref.received_at.isoformat(),
                "detected_at": now.isoformat(),
            }
        )
        log.record(ref.message_id, now)
        enqueued.append(ref.message_id)

    if not dry_run:
        log.mark_successful_poll(now)
    result = PollResult(
        found=len(refs),
        skipped=len(refs) - len(enqueued),
        enqueued_ids=tuple(enqueued),
        query_values_skipped=query.skipped_values,
        dry_run=dry_run,
    )
    logger.info(
        json.dumps(
            {
                "event": "poll_dry_run" if dry_run else "poll_done",
                "found": result.found,
                "skipped": result.skipped,
                "enqueued": len(result.enqueued_ids),
                "enqueued_ids": list(result.enqueued_ids),
                "query_values_skipped": result.query_values_skipped,
            }
        )
    )
    return result


def _aws():
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=settings.AWS_REGION)
    return dynamodb, boto3.client("sqs", region_name=settings.AWS_REGION)


def handler(event: dict, context: object) -> dict:
    dynamodb, sqs = _aws()
    result = poll(
        gmail=gmail_client(),
        tracker=TrackerTable(dynamodb.Table(os.environ["TRACKER_TABLE_NAME"])),
        log=ProcessedLog(dynamodb.Table(os.environ["PROCESSED_EMAILS_TABLE_NAME"])),
        queue=AgentQueue(sqs, os.environ["AGENT_QUEUE_URL"]),
        now=datetime.now(UTC),
        dry_run=event.get("dry_run") is True,
    )
    counts = {"found": result.found, "skipped": result.skipped}
    if result.dry_run:
        return {"dry_run": True, **counts, "would_enqueue": len(result.enqueued_ids)}
    return {**counts, "enqueued": len(result.enqueued_ids)}

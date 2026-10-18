"""The temporary week 2 queue reader (OQ-41 e), run by Lambda on the agent queue.

For each `new_email` message the poller queued, it:
1. stops early if the processed-email log marks the message handled (ING-2);
2. fetches the message with the read grant (ING-4) and trims it (CLS-3);
3. classifies it and extracts company, role and date in one call (CLS-1,
   CLS-2); a calendar invite's start wins over the extracted date;
4. applies the TRK-1 update, unless the row already lists this message;
5. for urgent and "Possible:" emails, claims the alert in the log, then
   publishes the ALR-1 alert to the alert topic; a failed publish releases
   the claim, so the retry can alert;
6. marks the message handled.

So a message delivered twice gives 1 tracker update and at most 1 alert
(ING-2). Any other message type is dropped: nothing else produces one until
the week 3 agent, which replaces this reader.

It is not the agent: no tools, no model-chosen actions, and only the read
grant. Logs carry IDs and counts only, never email text (NFR-PRIV-3).
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from inbox_agent import settings
from inbox_agent.alerts import Alert, alert_for
from inbox_agent.calendar_invite import key_date
from inbox_agent.classify import classify
from inbox_agent.gmail import GmailClient, gmail_client
from inbox_agent.matching import company_key, match_application
from inbox_agent.model import Model, classifier_model
from inbox_agent.poll import NEW_EMAIL_MESSAGE
from inbox_agent.processed_log import ProcessedLog
from inbox_agent.tracker import TrackerTable
from inbox_agent.tracker_update import update_for_email
from inbox_agent.trim import trim_email

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The alert names a company even when the email didn't.
UNNAMED_COMPANY = "Company not named"


@dataclass(frozen=True)
class AlertTopic:
    """`notify_owner` in miniature: one topic, no recipient (ALR-3)."""

    client: object  # boto3 SNS client
    arn: str

    def publish(self, alert: Alert) -> None:
        self.client.publish(TopicArn=self.arn, Subject=alert.subject, Message=alert.body)


@dataclass(frozen=True)
class Outcome:
    duplicate: bool = False
    row_written: bool = False
    row_created: bool = False
    alerted: bool = False


def _has_key(company: str, aliases: dict[str, str]) -> bool:
    try:
        company_key(company, aliases)
    except ValueError:
        return False
    return True


def handle_email(
    message_id: str,
    *,
    gmail: GmailClient,
    model: Model,
    tracker: TrackerTable,
    log: ProcessedLog,
    topic: AlertTopic,
    now: datetime,
) -> Outcome:
    if log.is_handled(message_id):
        return Outcome(duplicate=True)

    fetched = gmail.fetch(message_id)
    header = fetched.header
    trimmed = trim_email(
        subject=header.subject, sender=header.sender, date=header.received_at, text=fetched.text, html=fetched.html
    )
    result = classify(trimmed, model)
    when = key_date(fetched.invite, result.date)

    company, role = result.company, result.role
    aliases = tracker.aliases()
    if company and not _has_key(company, aliases):
        company = None  # nothing left after normalization, so no row to match
    row_written = row_created = False
    if company:
        rows = tracker.rows()
        existing = match_application(rows, company, role, aliases)
        if existing is None or message_id not in existing.get("source_message_ids", []):
            update = update_for_email(
                rows,
                result.email_class,
                company=company,
                role=role,
                message_id=message_id,
                received_on=header.received_at.astimezone(settings.OWNER_TIMEZONE).date(),
                key_date=when,
                aliases=aliases,
            )
            tracker.put(update.item)
            row_written, row_created = True, update.created
            existing = update.item
        # The alert names the row the email landed on (TRK-1).
        company, role = existing["company"], existing.get("role")

    alert = alert_for(result, company=company or UNNAMED_COMPANY, role=role, key_date=when)
    alerted = False
    if alert is not None and log.claim_alert(message_id, now):
        try:
            topic.publish(alert)
        except Exception:
            log.release_alert(message_id, now)
            raise
        alerted = True

    log.mark_handled(message_id, now)
    return Outcome(row_written=row_written, row_created=row_created, alerted=alerted)


def read_batch(records: list[dict], **deps) -> dict:
    """An SQS batch; failed messages go back to the queue (partial batch response)."""
    counts = {"handled": 0, "duplicates": 0, "skipped_types": 0, "failed": 0, "rows_written": 0, "rows_created": 0, "alerts": 0}
    handled_ids, failed_ids, failures = [], [], []
    for record in records:
        try:
            body = json.loads(record["body"])
        except (TypeError, ValueError):
            body = {}
        if not isinstance(body, dict) or body.get("type") != NEW_EMAIL_MESSAGE or not isinstance(body.get("message_id"), str):
            counts["skipped_types"] += 1
            continue
        message_id = body["message_id"]
        try:
            outcome = handle_email(message_id, **deps)
        except Exception as error:
            # The type only: an exception message could quote the email.
            logger.error(json.dumps({"event": "reader_failed", "message_id": message_id, "error": type(error).__name__}))
            counts["failed"] += 1
            failed_ids.append(message_id)
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        if outcome.duplicate:
            counts["duplicates"] += 1
            continue
        counts["handled"] += 1
        counts["rows_written"] += outcome.row_written
        counts["rows_created"] += outcome.row_created
        counts["alerts"] += outcome.alerted
        handled_ids.append(message_id)

    logger.info(json.dumps({"event": "reader_done", **counts, "handled_ids": handled_ids, "failed_ids": failed_ids}))
    return {"batchItemFailures": failures}


def _aws():
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=settings.AWS_REGION)
    return dynamodb, boto3.client("sns", region_name=settings.AWS_REGION)


def handler(event: dict, context: object) -> dict:
    dynamodb, sns = _aws()
    return read_batch(
        event.get("Records", []),
        gmail=gmail_client(),
        model=classifier_model(),
        tracker=TrackerTable(dynamodb.Table(os.environ["TRACKER_TABLE_NAME"])),
        log=ProcessedLog(dynamodb.Table(os.environ["PROCESSED_EMAILS_TABLE_NAME"])),
        topic=AlertTopic(sns, os.environ["ALERT_TOPIC_ARN"]),
        now=datetime.now(UTC),
    )

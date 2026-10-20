"""ING-2: the processed-email log, and the poller's last successful poll time.

Each logged Gmail message ID expires 30 days after it is logged (NFR-PRIV-4),
longer than the 14-day queue retention plus the 1-hour poll overlap. The
table object is a boto3 DynamoDB Table resource, or a fake.

The poller logs a message when it queues it. The agent's batch path adds two
marks to the same item: an alert claim, taken with a conditional write
before it publishes, so a second delivery can't send a second alert; and
"handled", once everything is done, so a second delivery stops early.

The claim also records the alert's kind (urgent or possible), so the Mac
app's Dashboard can count this week's alerts (DASH-1). It reads the log and
never writes.
"""

from datetime import UTC, datetime, timedelta

LOG_TTL = timedelta(days=30)
TTL_ATTRIBUTE = "expires_at"
ALERT_CLAIMED = "alert_claimed_at"
ALERT_KIND = "alert_kind"
ALERT_KINDS = ("urgent", "possible")
HANDLED = "handled_at"

# The last-poll time is one item in the same table. Gmail message IDs are hex,
# so this key can't collide with one, and the item has no TTL.
POLL_STATE_KEY = "#poll-state"
NOTICE_CLAIMED = "notice_claimed_at"


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def _condition_failed(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class ProcessedLog:
    def __init__(self, table) -> None:
        self._table = table

    def contains(self, message_id: str) -> bool:
        return "Item" in self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True)

    def record(self, message_id: str, now: datetime) -> None:
        self._table.put_item(Item=self._new_item(message_id, now))

    @staticmethod
    def _new_item(message_id: str, now: datetime) -> dict:
        return {"message_id": message_id, "logged_at": _epoch(now), TTL_ATTRIBUTE: _epoch(now + LOG_TTL)}

    def _item(self, message_id: str, now: datetime) -> dict:
        # A message queued by a poll that crashed before logging it has no
        # item yet; the batch path creates it, with the same TTL.
        item = self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True).get("Item")
        return item if item is not None else self._new_item(message_id, now)

    def is_handled(self, message_id: str) -> bool:
        item = self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True).get("Item")
        return bool(item and HANDLED in item)

    def claim_alert(self, message_id: str, now: datetime, kind: str | None = None) -> bool:
        """True for exactly one caller per message, even when two run at once."""
        if kind is not None and kind not in ALERT_KINDS:
            raise ValueError(f"alert kind must be one of {ALERT_KINDS}")
        item = self._item(message_id, now)
        if ALERT_CLAIMED in item:
            return False
        claim = {ALERT_CLAIMED: _epoch(now), **({ALERT_KIND: kind} if kind else {})}
        try:
            self._table.put_item(
                Item={**item, **claim},
                ConditionExpression=f"attribute_not_exists({ALERT_CLAIMED})",
            )
        except Exception as error:
            if _condition_failed(error):
                return False
            raise
        return True

    def release_alert(self, message_id: str, now: datetime) -> None:
        """After a failed publish, so the retry can alert."""
        item = self._item(message_id, now)
        item.pop(ALERT_CLAIMED, None)
        item.pop(ALERT_KIND, None)
        self._table.put_item(Item=item)

    def claim_notice(self, key: str, now: datetime) -> bool:
        """True for exactly one caller per key: the daily check's drafts-ready
        email goes out once per day, even if the check is redelivered. The
        key starts with "#", so it can't collide with a message ID; it
        expires like a log entry, and the Dashboard doesn't count it."""
        if not key.startswith("#"):
            raise ValueError("notice keys start with #")
        try:
            self._table.put_item(
                Item={**self._new_item(key, now), NOTICE_CLAIMED: _epoch(now)},
                ConditionExpression=f"attribute_not_exists({NOTICE_CLAIMED})",
            )
        except Exception as error:
            if _condition_failed(error):
                return False
            raise
        return True

    def release_notice(self, key: str) -> None:
        """After a failed publish, so the retry can send it."""
        self._table.delete_item(Key={"message_id": key})

    def mark_handled(self, message_id: str, now: datetime) -> None:
        self._table.put_item(Item={**self._item(message_id, now), HANDLED: _epoch(now)})

    def last_successful_poll(self) -> datetime | None:
        item = self._table.get_item(Key={"message_id": POLL_STATE_KEY}, ConsistentRead=True).get("Item")
        return datetime.fromtimestamp(int(item["last_success"]), UTC) if item else None

    def mark_successful_poll(self, started_at: datetime) -> None:
        self._table.put_item(Item={"message_id": POLL_STATE_KEY, "last_success": _epoch(started_at)})


def alert_claims(table, since: datetime) -> list[tuple[datetime, str | None]]:
    """Every alert claimed at or after `since`: its time and kind (None for
    claims made before kinds were recorded). Scans only (DASH-1)."""
    claims: list[tuple[datetime, str | None]] = []
    kwargs: dict = {}
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            if ALERT_CLAIMED in item and int(item[ALERT_CLAIMED]) >= _epoch(since):
                claims.append((datetime.fromtimestamp(int(item[ALERT_CLAIMED]), UTC), item.get(ALERT_KIND)))
        if "LastEvaluatedKey" not in page:
            return claims
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

"""ING-2: the processed-email log, and the poller's last successful poll time.

Each logged Gmail message ID expires 30 days after it is logged (NFR-PRIV-4),
longer than the 14-day queue retention plus the 1-hour poll overlap. The
table object is a boto3 DynamoDB Table resource, or a fake.

The poller logs a message when it queues it. The queue reader adds two
marks to the same item: an alert claim, taken with a conditional write
before it publishes, so a second delivery can't send a second alert; and
"handled", once everything is done, so a second delivery stops early.
"""

from datetime import UTC, datetime, timedelta

LOG_TTL = timedelta(days=30)
TTL_ATTRIBUTE = "expires_at"
ALERT_CLAIMED = "alert_claimed_at"
HANDLED = "handled_at"

# The last-poll time is one item in the same table. Gmail message IDs are hex,
# so this key can't collide with one, and the item has no TTL.
POLL_STATE_KEY = "#poll-state"


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
        # item yet; the reader creates it, with the same TTL.
        item = self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True).get("Item")
        return item if item is not None else self._new_item(message_id, now)

    def is_handled(self, message_id: str) -> bool:
        item = self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True).get("Item")
        return bool(item and HANDLED in item)

    def claim_alert(self, message_id: str, now: datetime) -> bool:
        """True for exactly one caller per message, even when two run at once."""
        item = self._item(message_id, now)
        if ALERT_CLAIMED in item:
            return False
        try:
            self._table.put_item(
                Item={**item, ALERT_CLAIMED: _epoch(now)},
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
        self._table.put_item(Item=item)

    def mark_handled(self, message_id: str, now: datetime) -> None:
        self._table.put_item(Item={**self._item(message_id, now), HANDLED: _epoch(now)})

    def last_successful_poll(self) -> datetime | None:
        item = self._table.get_item(Key={"message_id": POLL_STATE_KEY}, ConsistentRead=True).get("Item")
        return datetime.fromtimestamp(int(item["last_success"]), UTC) if item else None

    def mark_successful_poll(self, started_at: datetime) -> None:
        self._table.put_item(Item={"message_id": POLL_STATE_KEY, "last_success": _epoch(started_at)})

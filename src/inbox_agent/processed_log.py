"""ING-2: the processed-email log, and the poller's last successful poll time.

Each logged Gmail message ID expires 30 days after it is logged (NFR-PRIV-4),
longer than the 14-day queue retention plus the 1-hour poll overlap. The
table object is a boto3 DynamoDB Table resource, or a fake.
"""

from datetime import UTC, datetime, timedelta

LOG_TTL = timedelta(days=30)
TTL_ATTRIBUTE = "expires_at"

# The last-poll time is one item in the same table. Gmail message IDs are hex,
# so this key can't collide with one, and the item has no TTL.
POLL_STATE_KEY = "#poll-state"


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


class ProcessedLog:
    def __init__(self, table) -> None:
        self._table = table

    def contains(self, message_id: str) -> bool:
        return "Item" in self._table.get_item(Key={"message_id": message_id}, ConsistentRead=True)

    def record(self, message_id: str, now: datetime) -> None:
        self._table.put_item(
            Item={
                "message_id": message_id,
                "logged_at": _epoch(now),
                TTL_ATTRIBUTE: _epoch(now + LOG_TTL),
            }
        )

    def last_successful_poll(self) -> datetime | None:
        item = self._table.get_item(Key={"message_id": POLL_STATE_KEY}, ConsistentRead=True).get("Item")
        return datetime.fromtimestamp(int(item["last_success"]), UTC) if item else None

    def mark_successful_poll(self, started_at: datetime) -> None:
        self._table.put_item(Item={"message_id": POLL_STATE_KEY, "last_success": _epoch(started_at)})

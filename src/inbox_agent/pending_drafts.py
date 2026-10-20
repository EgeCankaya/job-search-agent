"""APR-3 and TOOL-4: the pending-draft record, one per proposed follow-up.

The approval workflow writes it (OQ-44 a), the queue consumer records my
decision on it (OQ-44 d), and `create_gmail_draft` reads it: the recipient,
thread, subject and text all come from here, never from a tool input
(OQ-44 e). The record holds the draft text and the reply-to address, so it
expires 30 days after it is resolved (NFR-PRIV-4) and never reaches logs.

States (APR-3): awaiting_decision → approved → draft_saved, or
awaiting_decision → skipped / expired.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from inbox_agent.processed_log import TTL_ATTRIBUTE

AWAITING_DECISION = "awaiting_decision"
APPROVED = "approved"
DRAFT_SAVED = "draft_saved"
SKIPPED = "skipped"
EXPIRED = "expired"
STATES = (AWAITING_DECISION, APPROVED, DRAFT_SAVED, SKIPPED, EXPIRED)

RESOLVED_TTL = timedelta(days=30)  # NFR-PRIV-4

# Attribute names. "state", "text" and "to" are DynamoDB reserved words, so
# every name here is prefixed or spelled out.
STATE = "draft_state"
TEXT = "draft_text"
TEXT_HASH = "text_hash"
TO_ADDRESS = "to_address"  # "" when no person's email was found (APR-2)
SUBJECT = "subject"
THREAD_ID = "thread_id"
IN_REPLY_TO = "in_reply_to"  # the replied-to email's Message-ID header
APPROVED_TEXT_HASH = "approved_text_hash"
DECIDED_BY = "decided_by"  # the IAM role ID SQS reported for the decision
DRAFT_CLAIMED_AT = "draft_claimed_at"
DRAFT_ID = "gmail_draft_id"
RESOLVED_AT = "resolved_at"
CALLBACK_ID = "callback_id"
PROPOSED_AT = "proposed_at"
DECIDED_AT = "decided_at"
# Copied from the stale check's draft, for the Follow-ups page and the
# tracker's follow-up state.
DRAFT_FIELDS = ("company", "role", "company_key", "role_key", "last_contact", "proposed_on", "language")


class ApprovalRefused(PermissionError):
    """A draft that TOOL-4 doesn't allow. The text names the reason, never the
    record's contents."""


class AlreadyClaimed(ApprovalRefused):
    pass


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def _condition_failed(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


@dataclass(frozen=True)
class ApprovedDraft:
    """What `create_gmail_draft` needs from an approved record."""

    approval_id: str
    to_address: str
    subject: str
    text: str
    thread_id: str | None
    in_reply_to: str | None


def check_approved(record: Mapping | None, owner_role_id: str) -> ApprovedDraft:
    """TOOL-4: the record exists, I approved it under my IAM identity, its text
    is the text I approved, and it hasn't been used."""
    if record is None:
        raise ApprovalRefused("no pending draft has that approval_id")
    state = record.get(STATE)
    if state == DRAFT_SAVED or record.get(DRAFT_CLAIMED_AT) is not None:
        raise AlreadyClaimed("this approval_id was already used")
    if state != APPROVED:
        raise ApprovalRefused("the draft is not approved")
    if not owner_role_id or record.get(DECIDED_BY) != owner_role_id:
        raise ApprovalRefused("the approval did not come from the owner's identity")
    text = record.get(TEXT)
    if not isinstance(text, str) or not text.strip():
        raise ApprovalRefused("the record has no text")
    if not (text_hash(text) == record.get(TEXT_HASH) == record.get(APPROVED_TEXT_HASH)):
        raise ApprovalRefused("the text differs from the approved text")
    return ApprovedDraft(
        approval_id=record["approval_id"],
        to_address=record.get(TO_ADDRESS, ""),
        subject=record.get(SUBJECT, ""),
        text=text,
        thread_id=record.get(THREAD_ID) or None,
        in_reply_to=record.get(IN_REPLY_TO) or None,
    )


class PendingDrafts:
    def __init__(self, table, now: Callable[[], datetime]) -> None:
        self._table = table
        self._now = now

    def get(self, approval_id: str) -> dict | None:
        return self._table.get_item(Key={"approval_id": approval_id}, ConsistentRead=True).get("Item")

    def register(self, draft: Mapping, callback_id: str) -> dict:
        """The approval workflow's first write (APR-3): the draft, waiting for
        my decision, with the callback the consumer resumes. A record that
        exists already is left alone and returned."""
        item = {
            "approval_id": draft["approval_id"],
            STATE: AWAITING_DECISION,
            TEXT: draft["text"],
            TEXT_HASH: text_hash(draft["text"]),
            TO_ADDRESS: draft.get("to_address") or "",
            SUBJECT: draft["subject"],
            CALLBACK_ID: callback_id,
            PROPOSED_AT: _epoch(self._now()),
            **{name: draft[name] for name in DRAFT_FIELDS if draft.get(name) is not None},
        }
        for name, value in ((THREAD_ID, draft.get("thread_id")), (IN_REPLY_TO, draft.get("in_reply_to"))):
            if value:
                item[name] = value
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(approval_id)")
        except Exception as error:
            if _condition_failed(error):
                return self.get(draft["approval_id"])
            raise
        return item

    def decide(self, approval_id: str, *, approve: bool, shown_text_hash: str, decided_by: str) -> dict | None:
        """My decision, as the consumer records it (APR-3 step 2, OQ-44 d).
        Returns the record to resume, or None when the decision changes
        nothing: no such record, an Approve of a text other than the one
        waiting, or a record already resolved another way. The same decision
        delivered again returns the record, so the consumer can resume it."""
        record = self.get(approval_id)
        if record is None:
            return None
        state = APPROVED if approve else SKIPPED
        if record.get(STATE) != AWAITING_DECISION:
            # A redelivery of the decision already recorded: resume again.
            same = record.get(STATE) == state and record.get(DECIDED_BY) == decided_by
            return record if same else None
        if shown_text_hash != record.get(TEXT_HASH):
            return None
        now = self._now()
        # The TTL starts at the decision either way, so an approval whose
        # draft never comes still goes away (NFR-PRIV-4).
        decided = {
            **record,
            STATE: state,
            DECIDED_BY: decided_by,
            DECIDED_AT: _epoch(now),
            TTL_ATTRIBUTE: _epoch(now + RESOLVED_TTL),
        }
        if approve:
            decided[APPROVED_TEXT_HASH] = shown_text_hash
        else:
            decided[RESOLVED_AT] = _epoch(now)
        try:
            self._table.put_item(
                Item=decided,
                ConditionExpression="#state = :waiting",
                ExpressionAttributeNames={"#state": STATE},
                ExpressionAttributeValues={":waiting": AWAITING_DECISION},
            )
        except Exception as error:
            if _condition_failed(error):
                return None  # decided or expired a moment ago
            raise
        return decided

    def expire(self, approval_id: str) -> dict | None:
        """The 7-day wait ended with no decision (APR-3). A record already
        resolved keeps its state; one approved in the same moment as the
        timeout is expired too, as its callback can no longer arrive."""
        record = self.get(approval_id)
        if record is None or record.get(STATE) in (DRAFT_SAVED, SKIPPED, EXPIRED):
            return record
        now = self._now()
        expired = {**record, STATE: EXPIRED, RESOLVED_AT: _epoch(now), TTL_ATTRIBUTE: _epoch(now + RESOLVED_TTL)}
        self._table.put_item(Item=expired)
        return expired

    def claim_for_draft(self, record: Mapping) -> None:
        """Mark the approval used before the draft is made, so two calls with
        the same ID can't both make one. Raises AlreadyClaimed if another
        call got there first."""
        item = {**record, DRAFT_CLAIMED_AT: _epoch(self._now())}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="#state = :approved AND attribute_not_exists(#claimed)",
                ExpressionAttributeNames={"#state": STATE, "#claimed": DRAFT_CLAIMED_AT},
                ExpressionAttributeValues={":approved": APPROVED},
            )
        except Exception as error:
            if _condition_failed(error):
                raise AlreadyClaimed("this approval_id was already used") from None
            raise

    def release_claim(self, record: Mapping) -> None:
        """Gmail refused: the claim goes, so the workflow's retry can draft."""
        item = {key: value for key, value in record.items() if key != DRAFT_CLAIMED_AT}
        self._table.put_item(
            Item=item,
            ConditionExpression="#state = :approved AND attribute_exists(#claimed)",
            ExpressionAttributeNames={"#state": STATE, "#claimed": DRAFT_CLAIMED_AT},
            ExpressionAttributeValues={":approved": APPROVED},
        )

    def mark_draft_saved(self, record: Mapping, draft_id: str) -> None:
        now = self._now()
        self._table.put_item(
            Item={
                **record,
                DRAFT_CLAIMED_AT: record.get(DRAFT_CLAIMED_AT, _epoch(now)),
                STATE: DRAFT_SAVED,
                DRAFT_ID: draft_id,
                RESOLVED_AT: _epoch(now),
                TTL_ATTRIBUTE: _epoch(now + RESOLVED_TTL),
            },
            ConditionExpression="#state = :approved AND attribute_exists(#claimed)",
            ExpressionAttributeNames={"#state": STATE, "#claimed": DRAFT_CLAIMED_AT},
            ExpressionAttributeValues={":approved": APPROVED},
        )


def awaiting(table) -> list[dict]:
    """Every draft waiting for my decision, oldest proposal first, for the
    Follow-ups page (APR-2). A scan: there are at most a few."""
    found: list[dict] = []
    kwargs: dict = {}
    while True:
        page = table.scan(**kwargs)
        found += [item for item in page.get("Items", []) if item.get(STATE) == AWAITING_DECISION]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted(found, key=lambda item: (int(item.get(PROPOSED_AT, 0)), item["approval_id"]))

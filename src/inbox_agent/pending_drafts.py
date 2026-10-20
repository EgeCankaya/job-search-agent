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

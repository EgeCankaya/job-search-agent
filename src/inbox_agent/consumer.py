"""The queue consumer (OQ-42 a): the agent queue's reader in cloud mode.

AgentCore Runtime is request-driven and can't read SQS itself, so this small
Lambda routes each message by its type:

- `new_email` (the poller): one InvokeAgentRuntime call with the message's
  IDs and times, in a session of its own (the batch path).
- `stale_check` (the daily schedule, APR-1): one InvokeAgentRuntime call
  (the stale-check path), then one approval workflow per returned draft,
  started asynchronously on its published version with the `approval_id` as
  the execution name, so a redelivered check starts nothing twice (OQ-44 a).
- `decision` (Approve or Skip from the Mac app, APR-3): never the agent and
  never a model. It counts only if SQS reports my IAM role as the sender
  (OQ-44 d); the consumer records it on the pending-draft record and resumes
  the waiting workflow with the record's callback ID.

Anything else is dropped. A failed call puts the message back on the queue
(partial batch response); after 3 receives it goes to the dead-letter queue
(ING-3). Logs carry IDs and counts only (NFR-PRIV-3).
"""

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from inbox_agent import settings
from inbox_agent.owner_identity import sender_role_id
from inbox_agent.pending_drafts import CALLBACK_ID, PendingDrafts
from inbox_agent.poll import NEW_EMAIL_MESSAGE
from inbox_agent.tools import APPROVAL_ID_PATTERN

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RUNTIME_ARN_ENV = "AGENT_RUNTIME_ARN"
APPROVAL_FUNCTION_ENV = "APPROVAL_FUNCTION_ARN"
QUALIFIER = "DEFAULT"
# Runtime session IDs are at least 33 characters.
SESSION_PREFIX = "inbox-agent-email-"
CHECK_SESSION_PREFIX = "inbox-agent-check-"
SESSION_MIN_LENGTH = 33
_MESSAGE_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_APPROVAL_ID = re.compile(APPROVAL_ID_PATTERN)
FORWARDED = ("type", "message_id", "received_at", "detected_at")

STALE_CHECK_MESSAGE = "stale_check"
DECISION_MESSAGE = "decision"
DECISIONS = ("approve", "skip")
# What the workflow may receive of a draft; never a test timeout (exit 3 is
# my own direct start).
DRAFT_KEYS = (
    "approval_id", "company", "role", "company_key", "role_key", "last_contact", "proposed_on",
    "to_address", "thread_id", "in_reply_to", "subject", "language", "text",
)  # fmt: skip
# Callback errors worth a retry; any other means the wait is over.
TRANSIENT = ("ServiceException", "TooManyRequestsException", "ThrottlingException")


class AgentError(RuntimeError):
    """The agent answered with an error. The text names the status only."""


def session_id(message_id: str) -> str:
    return (SESSION_PREFIX + message_id).ljust(SESSION_MIN_LENGTH, "-")


def check_session_id(day: str) -> str:
    return (CHECK_SESSION_PREFIX + day).ljust(SESSION_MIN_LENGTH, "-")


def _error_code(error: Exception) -> str:
    return getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)


def invoke_agent(client, runtime_arn: str, payload: dict, *, session: str | None = None) -> dict:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier=QUALIFIER,
        runtimeSessionId=session or session_id(payload["message_id"]),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode(),
    )
    status = response.get("statusCode", 200)
    body = response["response"].read()
    if status >= 300:
        raise AgentError(f"the agent returned HTTP {status}")
    result = json.loads(body or b"{}")
    if not isinstance(result, dict):
        raise AgentError("the agent returned something other than an object")
    return result


@dataclass
class ApprovalRouting:
    """What the stale check's drafts and my decisions need (APR-3)."""

    lambda_client: object
    function_arn: str  # the workflow's published version
    pending: PendingDrafts
    owner_role_id: Callable[[], str]
    today: Callable[[], str]  # ISO date in Europe/Istanbul


def start_approval(routing: ApprovalRouting, draft: dict) -> bool:
    """One workflow per draft. False when it had been started already."""
    if not isinstance(draft, dict) or not _APPROVAL_ID.match(str(draft.get("approval_id", ""))):
        raise AgentError("the agent returned a draft without a valid approval_id")
    payload = {"draft": {key: draft[key] for key in DRAFT_KEYS if key in draft}}
    try:
        routing.lambda_client.invoke(
            FunctionName=routing.function_arn,
            InvocationType="Event",
            DurableExecutionName=draft["approval_id"],
            Payload=json.dumps(payload).encode(),
        )
    except Exception as error:
        if _error_code(error) == "DurableExecutionAlreadyStartedException":
            return False
        raise
    return True


def handle_decision(routing: ApprovalRouting, body: dict, sender_id: object) -> str:
    """The outcome, for the log: resumed, ignored or not_owner."""
    approval_id = body.get("approval_id")
    decision = body.get("decision")
    shown_hash = body.get("text_hash")
    if (
        not isinstance(approval_id, str)
        or not _APPROVAL_ID.match(approval_id)
        or decision not in DECISIONS
        or not isinstance(shown_hash, str)
    ):
        return "ignored"
    sender = sender_role_id(sender_id)
    if not sender or sender != routing.owner_role_id():
        return "not_owner"
    record = routing.pending.decide(
        approval_id, approve=decision == "approve", shown_text_hash=shown_hash, decided_by=sender
    )
    if record is None or not record.get(CALLBACK_ID):
        return "ignored"
    try:
        routing.lambda_client.send_durable_execution_callback_success(
            CallbackId=record[CALLBACK_ID], Result=json.dumps({"decision": decision}).encode()
        )
    except Exception as error:
        if _error_code(error) in TRANSIENT:
            raise
        # Timed out or already resumed: nothing is waiting any more.
        return "ignored"
    return "resumed"


def consume(records: list[dict], *, client, runtime_arn: str, approvals: ApprovalRouting | None = None) -> dict:
    counts = {"handled": 0, "duplicates": 0, "alerts": 0, "checks": 0, "drafts": 0, "decisions": 0, "skipped_types": 0, "failed": 0}
    failures, handled_ids, failed_ids, started_ids, decided = [], [], [], [], []
    for record in records:
        try:
            body = json.loads(record["body"])
        except (TypeError, ValueError):
            body = {}
        kind = body.get("type") if isinstance(body, dict) else None

        if kind == DECISION_MESSAGE and approvals is not None:
            try:
                outcome = handle_decision(approvals, body, record.get("attributes", {}).get("SenderId"))
            except Exception as error:
                logger.error(json.dumps({"event": "decision_failed", "error": _error_code(error)}))
                counts["failed"] += 1
                failures.append({"itemIdentifier": record["messageId"]})
                continue
            counts["decisions"] += outcome == "resumed"
            decided.append({"approval_id": str(body.get("approval_id"))[:32], "outcome": outcome})
            continue

        if kind == STALE_CHECK_MESSAGE and approvals is not None:
            try:
                day = approvals.today()
                outcome = invoke_agent(client, runtime_arn, {"type": STALE_CHECK_MESSAGE}, session=check_session_id(day))
                drafts = outcome.get("drafts", [])
                if not isinstance(drafts, list):
                    raise AgentError("the agent returned drafts that aren't a list")
                for draft in drafts:
                    if start_approval(approvals, draft):
                        started_ids.append(draft["approval_id"])
            except Exception as error:
                logger.error(json.dumps({"event": "stale_check_failed", "error": _error_code(error)}))
                counts["failed"] += 1
                failures.append({"itemIdentifier": record["messageId"]})
                continue
            counts["checks"] += 1
            counts["drafts"] += len(drafts)
            continue

        if (
            kind != NEW_EMAIL_MESSAGE
            or not isinstance(body.get("message_id"), str)
            or not _MESSAGE_ID.match(body["message_id"])
        ):
            counts["skipped_types"] += 1
            continue
        message_id = body["message_id"]
        payload = {key: body[key] for key in FORWARDED if isinstance(body.get(key), str)}
        try:
            outcome = invoke_agent(client, runtime_arn, payload)
        except Exception as error:
            # The type only: an exception message could quote the email.
            logger.error(json.dumps({"event": "consumer_failed", "message_id": message_id, "error": type(error).__name__}))
            counts["failed"] += 1
            failed_ids.append(message_id)
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        if outcome.get("duplicate"):
            counts["duplicates"] += 1
            continue
        counts["handled"] += 1
        counts["alerts"] += bool(outcome.get("alerted"))
        handled_ids.append(message_id)

    logger.info(
        json.dumps(
            {
                "event": "consumer_done",
                **counts,
                "handled_ids": handled_ids,
                "failed_ids": failed_ids,
                "started_approval_ids": started_ids,
                "decisions_seen": decided,
            }
        )
    )
    return {"batchItemFailures": failures}


def handler(event: dict, context: object) -> dict:
    import boto3
    from botocore.config import Config

    from inbox_agent.owner_identity import owner_role_id

    # A new email takes seconds and the daily check about a minute (5
    # drafts at most). A botocore retry would add a second run on top of the
    # queue's own retries, so there is none; the queue retries.
    client = boto3.client(
        "bedrock-agentcore", region_name=settings.AWS_REGION, config=Config(read_timeout=150, retries={"max_attempts": 1})
    )
    table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(os.environ["PENDING_DRAFTS_TABLE_NAME"])
    approvals = ApprovalRouting(
        lambda_client=boto3.client("lambda", region_name=settings.AWS_REGION),
        function_arn=os.environ[APPROVAL_FUNCTION_ENV],
        pending=PendingDrafts(table, now=lambda: datetime.now(settings.OWNER_TIMEZONE)),
        owner_role_id=lambda: owner_role_id(os.environ["OWNER_ROLE_NAME"]),
        today=lambda: datetime.now(settings.OWNER_TIMEZONE).date().isoformat(),
    )
    return consume(event.get("Records", []), client=client, runtime_arn=os.environ[RUNTIME_ARN_ENV], approvals=approvals)

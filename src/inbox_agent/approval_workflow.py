"""APR-3: the approval workflow, a Lambda durable function in cloud mode
(OQ-44 a). One execution per proposed draft, started asynchronously by the
queue consumer with the `approval_id` as its execution name.

    create callback -> register (pending-draft record, follow-up "proposed")
    -> wait for my decision, at most 7 days, at no compute cost
    -> Approve: create_gmail_draft through the Gateway, under this function's
       own role, then follow-up "draft"
    -> Skip: follow-up "skipped"     -> no decision: record expired, follow-up "expired"

The consumer records my decision on the record (OQ-44 d) and then resumes
this execution with the stored callback ID; the decision never passes a
model. Each side effect is a checkpointed step, so a replay after a crash
never repeats it, and the draft tool's own claim makes the draft single use
(TOOL-4).

The draft text and addresses travel in the start payload and the record,
never in logs (NFR-PRIV-3).
"""

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from inbox_agent import settings
from inbox_agent.logs import log_event, setup
from inbox_agent.pending_drafts import PendingDrafts
from inbox_agent.tools import APPROVAL_ID_PATTERN
from inbox_agent.tracker import TrackerTable

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DECISION_WAIT_SECONDS = 7 * 24 * 3600  # APR-3, OQ-20
# Exit check 3: a direct start by the owner may shorten the wait. The
# consumer never forwards this field (consumer.py).
TEST_TIMEOUT_FIELD = "test_timeout_seconds"
TEST_TIMEOUT_RANGE = (60, 3600)

APPROVE, SKIP = "approve", "skip"
REQUIRED_DRAFT_FIELDS = ("approval_id", "company", "company_key", "role_key", "subject", "text", "proposed_on")


class StartError(ValueError):
    """A start payload this workflow refuses. Names the field only."""


@dataclass
class WorkflowDeps:
    pending: PendingDrafts
    tracker: TrackerTable
    create_draft: Callable[[str], dict]  # create_gmail_draft through the Gateway
    today: Callable[[], str]  # ISO date in Europe/Istanbul, for follow-up state


def check_start(event: object) -> tuple[dict, int]:
    """The draft to wait on, and how long to wait."""
    if not isinstance(event, Mapping) or not isinstance(event.get("draft"), Mapping):
        raise StartError("the start payload needs a draft")
    draft = dict(event["draft"])
    for name in REQUIRED_DRAFT_FIELDS:
        if not isinstance(draft.get(name), str) or not draft[name].strip():
            raise StartError(f"the draft needs {name}")
    if not re.fullmatch(APPROVAL_ID_PATTERN, draft["approval_id"]):
        raise StartError("approval_id has the wrong form")
    timeout = DECISION_WAIT_SECONDS
    if TEST_TIMEOUT_FIELD in event:
        value = event[TEST_TIMEOUT_FIELD]
        low, high = TEST_TIMEOUT_RANGE
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise StartError(f"{TEST_TIMEOUT_FIELD} must be {low} to {high} seconds")
        timeout = value
    return draft, timeout


def decision_of(result: object) -> str:
    """The callback's result as the consumer sent it: {"decision": ...}."""
    if isinstance(result, (bytes, str)):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    decision = result.get("decision") if isinstance(result, Mapping) else None
    if decision not in (APPROVE, SKIP):
        # Anything unexpected creates no draft.
        return SKIP
    return decision


def run(event: object, context, deps: WorkflowDeps) -> dict:
    """The workflow body; `context` is the SDK's DurableContext."""
    from aws_durable_execution_sdk_python import CallbackError
    from aws_durable_execution_sdk_python.config import CallbackConfig, Duration

    draft, timeout = check_start(event)
    approval_id = draft["approval_id"]
    keys = (draft["company_key"], draft["role_key"])

    callback = context.create_callback(name="decision", config=CallbackConfig(timeout=Duration(seconds=timeout)))

    def register(_):
        deps.pending.register(draft, callback.callback_id)
        deps.tracker.record_follow_up(*keys, outcome="proposed", on=draft["proposed_on"])
        return True

    context.step(register, name="register")

    try:
        decision = decision_of(callback.result())
    except CallbackError:

        def expire(_):
            deps.pending.expire(approval_id)
            deps.tracker.record_follow_up(*keys, outcome="expired", on=deps.today())
            return True

        context.step(expire, name="expire")
        return {"approval_id": approval_id, "outcome": "expired"}

    if decision == SKIP:
        context.step(
            lambda _: deps.tracker.record_follow_up(*keys, outcome="skipped", on=deps.today()) or True, name="skip"
        )
        return {"approval_id": approval_id, "outcome": "skipped"}

    saved = context.step(lambda _: deps.create_draft(approval_id), name="create_draft")
    context.step(lambda _: deps.tracker.record_follow_up(*keys, outcome="draft", on=deps.today()) or True, name="drafted")
    return {"approval_id": approval_id, "outcome": "draft_saved", "draft_id": saved.get("draft_id")}


def _deps() -> WorkflowDeps:
    import boto3

    from inbox_agent.gateway_tools import GatewayTools
    from inbox_agent.tools import APPROVAL_TOOLS

    dynamodb = boto3.resource("dynamodb", region_name=settings.AWS_REGION)
    # Under this function's own role: Cedar permits it the draft tool only (OQ-44 j).
    gateway = GatewayTools(os.environ["GATEWAY_URL"], names=APPROVAL_TOOLS)
    return WorkflowDeps(
        pending=PendingDrafts(dynamodb.Table(os.environ["PENDING_DRAFTS_TABLE_NAME"]), now=lambda: datetime.now(UTC)),
        tracker=TrackerTable(dynamodb.Table(os.environ["TRACKER_TABLE_NAME"])),
        create_draft=lambda approval_id: gateway.call("create_gmail_draft", {"approval_id": approval_id}),
        today=lambda: datetime.now(settings.OWNER_TIMEZONE).date().isoformat(),
    )


def _handler(event, context):
    setup()
    try:
        outcome = run(event, context, _deps())
    except Exception as error:
        # The type only; the payload holds the draft.
        log_event(logger, "approval_failed", logging.ERROR, error=type(error).__name__)
        raise
    log_event(logger, "approval_done", **{k: v for k, v in outcome.items() if k != "draft_id"})
    return outcome


def _durable(func):
    # The SDK is in the workflow's ZIP only (the `approval` group); the
    # other Lambdas import nothing from here.
    from aws_durable_execution_sdk_python import durable_execution

    return durable_execution(func)


handler = _durable(_handler)

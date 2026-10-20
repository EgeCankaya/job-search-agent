"""The approval workflow (APR-3, OQ-44 a), run by the durable-execution SDK's
local test runner: real checkpoints, replays, callbacks and timeouts, with
fake tables and a fake draft tool. Companies and IDs are fictional."""

import json
from datetime import UTC, datetime

import pytest
from aws_durable_execution_sdk_python import durable_execution
from aws_durable_execution_sdk_python.execution import InvocationStatus
from aws_durable_execution_sdk_python_testing.runner import DurableFunctionTestRunner

from inbox_agent.approval_workflow import StartError, WorkflowDeps, check_start, decision_of, run
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.pending_drafts import (
    APPROVED,
    AWAITING_DECISION,
    CALLBACK_ID,
    EXPIRED,
    STATE,
    TEXT_HASH,
    PendingDrafts,
    text_hash,
)
from inbox_agent.stale_check import approval_id
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=UTC)
APPROVAL_ID = approval_id("kuzey yazilim", "backend engineer", datetime(2026, 10, 21).date())
TEXT = "Merhaba, 1 Ekim 2026 tarihli başvurum olan Backend Engineer hakkında bir gelişme var mı?\n"
DRAFT = {
    "approval_id": APPROVAL_ID,
    "company": "Kuzey Yazılım A.Ş.",
    "role": "Backend Engineer",
    "company_key": "kuzey yazilim",
    "role_key": "backend engineer",
    "last_contact": "2026-10-01",
    "proposed_on": "2026-10-21",
    "to_address": "ayse@kuzey.example.com",
    "thread_id": "18t0005",
    "in_reply_to": "<5@kuzey.example.com>",
    "subject": "Re: Başvurunuz",
    "language": "tr",
    "text": TEXT,
}


class Env:
    def __init__(self):
        self.pending_table = FakeTable("approval_id")
        self.tracker_table = FakeTable("company_key", "role_key")
        import_rows(parse_csv("company,role,applied_date,status\nKuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied\n"), TrackerTable(self.tracker_table))
        self.draft_calls = []
        self.deps = WorkflowDeps(
            pending=PendingDrafts(self.pending_table, now=lambda: NOW),
            tracker=TrackerTable(self.tracker_table),
            create_draft=self.create_draft,
            today=lambda: "2026-10-23",
        )

    def create_draft(self, approval_id):
        self.draft_calls.append(approval_id)
        return {"approval_id": approval_id, "draft_id": "r-1", "saved": True}

    def handler(self):
        return durable_execution(lambda event, context: run(event, context, self.deps))

    def record(self):
        return self.pending_table.items.get((APPROVAL_ID,))

    def follow_up(self):
        return self.tracker_table.items[("kuzey yazilim", "backend engineer")].get("follow_up")


def start(runner, event):
    return runner.run_async(input=json.dumps(event), execution_name=APPROVAL_ID, timeout=120)


def decide(env, runner, arn, decision):
    """What the consumer does (step 5): record the decision, then resume."""
    callback_id = runner.wait_for_callback(arn, name="decision", timeout=10)
    record = env.record()
    assert record[CALLBACK_ID] == callback_id
    if decision == "approve":
        env.pending_table.put_item(Item={**record, STATE: APPROVED})
    runner.send_callback_success(callback_id, json.dumps({"decision": decision}).encode())
    return runner.wait_for_result(arn, timeout=30)


@pytest.fixture
def env():
    return Env()


def test_approve_creates_exactly_one_draft_through_the_tool(env):
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        arn = start(runner, {"draft": DRAFT})
        result = decide(env, runner, arn, "approve")
    assert result.status is InvocationStatus.SUCCEEDED
    assert json.loads(result.result) == {"approval_id": APPROVAL_ID, "outcome": "draft_saved", "draft_id": "r-1"}
    assert env.draft_calls == [APPROVAL_ID]
    follow_up = env.follow_up()
    assert follow_up["last_proposed"] == "2026-10-21" and follow_up["last_draft"] == "2026-10-23"
    assert follow_up["skips_in_a_row"] == 0 and follow_up["version"] == 2


def test_the_record_waits_with_the_callback_and_the_texts_hash(env):
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        arn = start(runner, {"draft": DRAFT})
        runner.wait_for_callback(arn, name="decision", timeout=10)
        record = env.record()
        assert record[STATE] == AWAITING_DECISION and record[TEXT_HASH] == text_hash(TEXT)
        assert record["to_address"] == "ayse@kuzey.example.com" and record["in_reply_to"] == "<5@kuzey.example.com>"
        assert env.follow_up()["last_proposed"] == "2026-10-21"
        decide(env, runner, arn, "skip")


def test_skip_creates_no_draft_and_adds_to_the_streak(env):
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        result = decide(env, runner, start(runner, {"draft": DRAFT}), "skip")
    assert json.loads(result.result)["outcome"] == "skipped"
    assert env.draft_calls == []
    assert env.follow_up()["skips_in_a_row"] == 1 and env.follow_up()["last_skipped"] == "2026-10-23"


def test_an_unexpected_decision_creates_no_draft(env):
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        result = decide(env, runner, start(runner, {"draft": DRAFT}), "send it now")
    assert json.loads(result.result)["outcome"] == "skipped" and env.draft_calls == []


def test_a_failed_callback_expires_the_record_and_creates_no_draft(env):
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        arn = start(runner, {"draft": DRAFT})
        callback_id = runner.wait_for_callback(arn, name="decision", timeout=10)
        runner.send_callback_failure(callback_id)  # raises CallbackError, as a timeout does
        result = runner.wait_for_result(arn, timeout=30)
    assert json.loads(result.result)["outcome"] == "expired"
    assert env.record()[STATE] == EXPIRED and env.record()["expires_at"] == int(NOW.timestamp()) + 30 * 86400
    assert env.draft_calls == [] and env.follow_up()["last_expired"] == "2026-10-23"


def test_two_pending_drafts_resolve_only_their_own(env):
    other = {**DRAFT, "approval_id": "f" * 32, "text": TEXT.replace("Merhaba", "İyi günler")}
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        first = start(runner, {"draft": DRAFT})
        second = runner.run_async(input=json.dumps({"draft": other}), execution_name="f" * 32, timeout=120)
        first_callback = runner.wait_for_callback(first, name="decision", timeout=10)
        second_callback = runner.wait_for_callback(second, name="decision", timeout=10)
        assert first_callback != second_callback
        runner.send_callback_success(second_callback, b'{"decision": "skip"}')
        assert json.loads(runner.wait_for_result(second, timeout=30).result)["outcome"] == "skipped"
        assert env.record()[STATE] == AWAITING_DECISION  # the first still waits
        decide(env, runner, first, "approve")
    assert env.draft_calls == [APPROVAL_ID]


def test_the_decision_wait_is_7_days_unless_a_test_start_shortens_it():
    assert check_start({"draft": DRAFT})[1] == 7 * 24 * 3600
    assert check_start({"draft": DRAFT, "test_timeout_seconds": 60})[1] == 60
    for bad in (59, 3601, True, "60"):
        with pytest.raises(StartError):
            check_start({"draft": DRAFT, "test_timeout_seconds": bad})


@pytest.mark.parametrize("missing", ["approval_id", "text", "subject", "company_key"])
def test_a_start_without_a_needed_field_is_refused(missing):
    with pytest.raises(StartError):
        check_start({"draft": {k: v for k, v in DRAFT.items() if k != missing}})


def test_a_malformed_approval_id_is_refused():
    with pytest.raises(StartError):
        check_start({"draft": {**DRAFT, "approval_id": "../x"}})


@pytest.mark.parametrize(
    ("result", "expected"),
    [({"decision": "approve"}, "approve"), (b'{"decision": "skip"}', "skip"), ('{"decision": "approve"}', "approve"), (None, "skip"), (b"not json", "skip")],
)
def test_decisions_are_read_strictly(result, expected):
    assert decision_of(result) == expected


def test_a_real_callback_timeout_expires_the_record(env, monkeypatch):
    # Exit check 3 uses 60 s; the runner's clock is real, so 1 s here.
    monkeypatch.setattr("inbox_agent.approval_workflow.TEST_TIMEOUT_RANGE", (1, 3600))
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        result = runner.wait_for_result(start(runner, {"draft": DRAFT, "test_timeout_seconds": 1}), timeout=30)
    assert json.loads(result.result)["outcome"] == "expired"
    assert env.record()[STATE] == EXPIRED and env.draft_calls == []


def test_a_failure_after_the_draft_never_repeats_the_draft(env):
    real = env.deps.tracker.record_follow_up
    failures = {"draft": 1}

    def flaky(*keys, outcome, on):
        if failures.get(outcome):
            failures[outcome] -= 1
            raise ConnectionError("DynamoDB throttled")
        return real(*keys, outcome=outcome, on=on)

    env.deps.tracker.record_follow_up = flaky
    with DurableFunctionTestRunner(env.handler(), poll_interval=0.05) as runner:
        result = decide(env, runner, start(runner, {"draft": DRAFT}), "approve")
    assert json.loads(result.result)["outcome"] == "draft_saved"
    assert env.draft_calls == [APPROVAL_ID]  # the retry replayed the draft step's result
    assert env.follow_up()["last_draft"] == "2026-10-23"

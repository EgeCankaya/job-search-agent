"""The queue consumer (OQ-42 a): one InvokeAgentRuntime per new email, partial
batch failures, IDs only in logs. The AgentCore client is a fake."""

import io
import json
import logging

import pytest

from inbox_agent.consumer import AgentError, consume, invoke_agent, session_id

RUNTIME = "arn:aws:bedrock-agentcore:eu-north-1:111122223333:runtime/inbox_agent-abc123"


class FakeAgentCore:
    def __init__(self, outcomes=None, status=200):
        self.outcomes = list(outcomes or [])
        self.status = status
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else {"duplicate": False, "alerted": False}
        if isinstance(outcome, Exception):
            raise outcome
        return {"statusCode": self.status, "response": io.BytesIO(json.dumps(outcome).encode())}


def record(message_id="18c0ffee", type_="new_email", sqs_id="sqs-1", **extra):
    body = {"type": type_, "message_id": message_id, "thread_id": "t-1", "received_at": "2026-10-19T09:40:00+00:00", "detected_at": "2026-10-19T10:00:00+00:00", **extra}
    return {"messageId": sqs_id, "body": json.dumps(body)}


def test_session_ids_are_long_enough_and_per_message():
    assert len(session_id("18c0ffee")) >= 33
    assert session_id("18c0ffee") != session_id("18c0fff0")
    assert session_id("18c0ffee").startswith("inbox-agent-email-18c0ffee")


def test_a_new_email_becomes_one_call_with_ids_and_times_only():
    client = FakeAgentCore([{"duplicate": False, "alerted": True}])
    assert consume([record(subject="Mülakat")], client=client, runtime_arn=RUNTIME) == {"batchItemFailures": []}
    [call] = client.calls
    assert call["agentRuntimeArn"] == RUNTIME
    assert call["qualifier"] == "DEFAULT"
    assert json.loads(call["payload"]) == {
        "type": "new_email",
        "message_id": "18c0ffee",
        "received_at": "2026-10-19T09:40:00+00:00",
        "detected_at": "2026-10-19T10:00:00+00:00",
    }


def test_a_failed_call_goes_back_to_the_queue_alone():
    client = FakeAgentCore([{"duplicate": False}, ConnectionError("Mülakat"), {"duplicate": True}])
    records = [record("a1", sqs_id="s1"), record("a2", sqs_id="s2"), record("a3", sqs_id="s3")]
    assert consume(records, client=client, runtime_arn=RUNTIME) == {"batchItemFailures": [{"itemIdentifier": "s2"}]}


@pytest.mark.parametrize(
    "body",
    ["not json", json.dumps(["new_email"]), json.dumps({"type": "decision", "approval_id": "x"}), json.dumps({"type": "new_email", "message_id": "../x"})],
)
def test_other_messages_are_dropped_without_a_call(body):
    client = FakeAgentCore()
    assert consume([{"messageId": "s1", "body": body}], client=client, runtime_arn=RUNTIME) == {"batchItemFailures": []}
    assert client.calls == []


def test_an_error_status_raises():
    with pytest.raises(AgentError, match="HTTP 500"):
        invoke_agent(FakeAgentCore(status=500), RUNTIME, {"type": "new_email", "message_id": "a1"})


def test_logs_carry_ids_and_counts_only(caplog):
    client = FakeAgentCore([ValueError("Mülakat daveti"), {"duplicate": False, "alerted": True}])
    with caplog.at_level(logging.INFO):
        consume([record("a1", sqs_id="s1"), record("a2", sqs_id="s2")], client=client, runtime_arn=RUNTIME)
    assert "Mülakat" not in caplog.text
    done = json.loads(caplog.records[-1].getMessage())
    assert done["event"] == "consumer_done"
    assert (done["handled"], done["failed"], done["alerts"]) == (1, 1, 1)
    assert done["failed_ids"] == ["a1"] and done["handled_ids"] == ["a2"]


# Week 4: the daily check and decisions (APR-3, OQ-44 a, d)

from datetime import UTC, datetime  # noqa: E402

from inbox_agent.consumer import ApprovalRouting, check_session_id  # noqa: E402
from inbox_agent.pending_drafts import (  # noqa: E402
    APPROVED,
    APPROVED_TEXT_HASH,
    AWAITING_DECISION,
    CALLBACK_ID,
    DECIDED_BY,
    EXPIRED,
    SKIPPED,
    STATE,
    PendingDrafts,
    text_hash,
)
from tests.fakes import FakeTable  # noqa: E402

OWNER = "AROAEXAMPLEOWNER0001"
APPROVAL_FN = "arn:aws:lambda:eu-north-1:111122223333:function:inbox-agent-approval:7"
ID_A, ID_B = "a" * 32, "b" * 32
TEXT = "Merhaba, 1 Ekim 2026 tarihli Backend Engineer başvurum hakkında bir gelişme var mı?\n"


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeLambda:
    def __init__(self, invoke_errors=(), callback_errors=()):
        self.invokes, self.callbacks = [], []
        self.invoke_errors, self.callback_errors = list(invoke_errors), list(callback_errors)

    def invoke(self, **kwargs):
        if self.invoke_errors:
            raise ClientError(self.invoke_errors.pop(0))
        self.invokes.append(kwargs)
        return {"StatusCode": 202}

    def send_durable_execution_callback_success(self, **kwargs):
        if self.callback_errors:
            raise ClientError(self.callback_errors.pop(0))
        self.callbacks.append(kwargs)
        return {}


def routing(lambda_client=None, table=None):
    return ApprovalRouting(
        lambda_client=lambda_client or FakeLambda(),
        function_arn=APPROVAL_FN,
        pending=PendingDrafts(table or FakeTable("approval_id"), now=lambda: datetime(2026, 10, 22, 7, 0, tzinfo=UTC)),
        owner_role_id=lambda: OWNER,
        today=lambda: "2026-10-22",
    )


def draft(approval_id, **extra):
    return {"approval_id": approval_id, "company": "Kuzey", "company_key": "kuzey", "role_key": "-", "subject": "Re: x", "text": TEXT, "proposed_on": "2026-10-22", **extra}


def waiting_table(*approval_ids, state=AWAITING_DECISION):
    table = FakeTable("approval_id")
    for n, approval_id in enumerate(approval_ids):
        table.put_item(Item={"approval_id": approval_id, STATE: state, "draft_text": TEXT, "text_hash": text_hash(TEXT), CALLBACK_ID: f"cb-{n}"})
    return table


def message(body, sqs_id="s1", sender=f"{OWNER}:owner-session"):
    return {"messageId": sqs_id, "body": json.dumps(body), "attributes": {"SenderId": sender}}


def decision(approval_id=ID_A, choice="approve", shown=TEXT, **extra):
    return {"type": "decision", "approval_id": approval_id, "decision": choice, "text_hash": text_hash(shown), **extra}


def test_a_check_asks_the_agent_then_starts_one_workflow_per_draft():
    agent = FakeAgentCore([{"drafts": [draft(ID_A, test_timeout_seconds=60), draft(ID_B)], "notified": True}])
    lam = FakeLambda()
    assert consume([message({"type": "stale_check"})], client=agent, runtime_arn=RUNTIME, approvals=routing(lam)) == {"batchItemFailures": []}
    [call] = agent.calls
    assert json.loads(call["payload"]) == {"type": "stale_check"}
    assert call["runtimeSessionId"] == check_session_id("2026-10-22") and len(call["runtimeSessionId"]) >= 33
    assert [i["DurableExecutionName"] for i in lam.invokes] == [ID_A, ID_B]
    for invoke in lam.invokes:
        assert invoke["FunctionName"] == APPROVAL_FN and invoke["InvocationType"] == "Event"
        started = json.loads(invoke["Payload"])
        assert set(started) == {"draft"}
        assert "test_timeout_seconds" not in started["draft"]  # only my own start may shorten the wait


def test_a_redelivered_check_starts_nothing_twice():
    agent = FakeAgentCore([{"drafts": [draft(ID_A)]}])
    lam = FakeLambda(invoke_errors=["DurableExecutionAlreadyStartedException"])
    assert consume([message({"type": "stale_check"})], client=agent, runtime_arn=RUNTIME, approvals=routing(lam))["batchItemFailures"] == []
    assert lam.invokes == []


def test_a_failed_start_retries_the_check():
    agent = FakeAgentCore([{"drafts": [draft(ID_A)]}])
    lam = FakeLambda(invoke_errors=["TooManyRequestsException"])
    result = consume([message({"type": "stale_check"})], client=agent, runtime_arn=RUNTIME, approvals=routing(lam))
    assert result == {"batchItemFailures": [{"itemIdentifier": "s1"}]}


def test_a_draft_without_a_valid_approval_id_fails_the_check():
    agent = FakeAgentCore([{"drafts": [draft("../x")]}])
    result = consume([message({"type": "stale_check"})], client=agent, runtime_arn=RUNTIME, approvals=routing())
    assert result["batchItemFailures"] == [{"itemIdentifier": "s1"}]


def test_approve_records_the_decision_then_resumes_the_workflow_without_the_agent():
    agent, lam, table = FakeAgentCore(), FakeLambda(), waiting_table(ID_A)
    consume([message(decision())], client=agent, runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert agent.calls == []  # never the agent or a model (APR-3)
    record = table.items[(ID_A,)]
    assert record[STATE] == APPROVED and record[DECIDED_BY] == OWNER and record[APPROVED_TEXT_HASH] == text_hash(TEXT)
    [callback] = lam.callbacks
    assert callback["CallbackId"] == "cb-0" and json.loads(callback["Result"]) == {"decision": "approve"}


def test_skip_resolves_the_record_and_resumes():
    lam, table = FakeLambda(), waiting_table(ID_A)
    consume([message(decision(choice="skip"))], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert table.items[(ID_A,)][STATE] == SKIPPED and "expires_at" in table.items[(ID_A,)]
    assert json.loads(lam.callbacks[0]["Result"]) == {"decision": "skip"}


@pytest.mark.parametrize(
    "sender",
    ["AROAEXAMPLEOTHER0002:poll", "AIDAEXAMPLEUSER00003", None, f"{OWNER}"],  # another role, a user, none, no session
)
def test_a_decision_from_any_other_sender_changes_nothing(sender):
    lam, table = FakeLambda(), waiting_table(ID_A)
    consume([message(decision(), sender=sender)], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert table.items[(ID_A,)][STATE] == AWAITING_DECISION and lam.callbacks == []


def test_an_approve_of_another_text_changes_nothing():
    lam, table = FakeLambda(), waiting_table(ID_A)
    consume([message(decision(shown=TEXT + "P.S."))], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert table.items[(ID_A,)][STATE] == AWAITING_DECISION and lam.callbacks == []


def test_each_decision_resolves_only_its_own_draft_and_only_once():
    lam, table = FakeLambda(), waiting_table(ID_A, ID_B)
    records = [message(decision(ID_A), "s1"), message(decision(ID_A, "skip"), "s2"), message(decision(ID_B, "skip"), "s3")]
    consume(records, client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert table.items[(ID_A,)][STATE] == APPROVED  # the later Skip for A is ignored
    assert table.items[(ID_B,)][STATE] == SKIPPED
    assert [c["CallbackId"] for c in lam.callbacks] == ["cb-0", "cb-1"]


def test_a_redelivered_approve_resumes_again_after_a_transient_failure():
    lam, table = FakeLambda(callback_errors=["TooManyRequestsException"]), waiting_table(ID_A)
    first = consume([message(decision())], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert first["batchItemFailures"] == [{"itemIdentifier": "s1"}]
    assert table.items[(ID_A,)][STATE] == APPROVED  # recorded before the callback
    consume([message(decision())], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert len(lam.callbacks) == 1


@pytest.mark.parametrize("code", ["CallbackTimeoutException", "ResourceNotFoundException", "InvalidParameterValueException"])
def test_a_decision_for_a_wait_that_ended_is_dropped(code):
    lam, table = FakeLambda(callback_errors=[code]), waiting_table(ID_A)
    result = consume([message(decision())], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert result["batchItemFailures"] == []


def test_a_decision_for_an_expired_record_is_dropped():
    lam, table = FakeLambda(), waiting_table(ID_A, state=EXPIRED)
    consume([message(decision())], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert table.items[(ID_A,)][STATE] == EXPIRED and lam.callbacks == []


def test_decisions_are_logged_by_id_only(caplog):
    lam, table = FakeLambda(), waiting_table(ID_A)
    with caplog.at_level(logging.INFO):
        consume([message(decision())], client=FakeAgentCore(), runtime_arn=RUNTIME, approvals=routing(lam, table))
    assert ID_A in caplog.text and "Merhaba" not in caplog.text and OWNER not in caplog.text

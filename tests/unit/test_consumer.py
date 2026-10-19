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

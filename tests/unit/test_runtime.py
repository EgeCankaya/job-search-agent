"""The entry point (OQ-42 a, j, k) and the Gateway client's offline parts
(OQ-42 e). No request leaves the test."""

import json
import logging
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest
from botocore.credentials import Credentials

from inbox_agent import runtime
from inbox_agent.gateway_tools import GatewayToolError, GatewayTools, parse_result, plain_name, sigv4_auth
from inbox_agent.tools import ToolInputError, ToolNotAllowed

GATEWAY = "https://gw-abc123.gateway.bedrock-agentcore.eu-north-1.amazonaws.com/mcp"


class FakeGraph:
    def __init__(self, state=None, error=None):
        self.state, self.error = state, error
        self.inputs = []

    def invoke(self, value, config=None):
        self.inputs.append(value)
        if self.error:
            raise self.error
        return self.state


def batch_state(alerted=True):
    return {
        "message_id": "18c0ffee",
        "duplicate": False,
        "classification": SimpleNamespace(email_class=SimpleNamespace(value="interview_invite")),
        "row_written": True,
        "row_created": False,
        "alerted": alerted,
        "email": {"subject": "Mülakat daveti", "text": "Merhaba"},
    }


def test_a_new_email_runs_the_batch_path_and_logs_ids_and_times(caplog):
    graph = FakeGraph(batch_state())
    payload = {"type": "new_email", "message_id": "18c0ffee", "received_at": "2026-10-19T09:40:00+00:00", "detected_at": "2026-10-19T10:00:00+00:00"}
    with caplog.at_level(logging.INFO, logger="inbox_agent.runtime"):
        outcome = runtime.handle(payload, batch=graph)
    assert outcome == {"message_id": "18c0ffee", "duplicate": False, "email_class": "interview_invite", "row_written": True, "row_created": False, "alerted": True}
    assert graph.inputs == [{"message_id": "18c0ffee"}]
    [record] = caplog.records
    fields = json.loads(record.getMessage())
    assert fields["event"] == "batch_done"
    assert fields["received_at"] == "2026-10-19T09:40:00+00:00"
    assert fields["detected_at"] == "2026-10-19T10:00:00+00:00"
    assert "published_at" in fields
    assert "Mülakat" not in caplog.text and "Merhaba" not in caplog.text


def test_a_failed_email_logs_the_error_type_only(caplog):
    graph = FakeGraph(error=ValueError("Mülakat daveti for Kuzey"))
    with caplog.at_level(logging.INFO, logger="inbox_agent.runtime"), pytest.raises(ValueError):
        runtime.handle({"type": "new_email", "message_id": "18c0ffee"}, batch=graph)
    assert '"error": "ValueError"' in caplog.text
    assert "Mülakat" not in caplog.text


def test_a_chat_question_is_answered_and_not_logged(caplog):
    class Graph:
        def invoke(self, value, config=None):
            from langchain_core.messages import AIMessage

            return {"messages": [*value["messages"], AIMessage(content="Software Engineer: interviewing.")]}

    with caplog.at_level(logging.INFO, logger="inbox_agent.runtime"):
        assert runtime.handle({"type": "chat", "question": "Brightpath'da ne açık?"}, chat=Graph()) == {
            "answer": "Software Engineer: interviewing."
        }
    assert "Brightpath" not in caplog.text and "interviewing" not in caplog.text
    assert json.loads(caplog.records[0].getMessage())["event"] == "chat_done"


@pytest.mark.parametrize(
    "payload",
    [
        ["new_email"],
        {"type": "send_email", "to": "someone@example.com"},
        {"type": "decision", "approval_id": "a1"},
        {"type": "new_email"},
        {"type": "new_email", "message_id": "../../x"},
    ],
)
def test_other_payloads_are_refused(payload):
    graph = FakeGraph(batch_state())
    with pytest.raises(runtime.PayloadError):
        runtime.handle(payload, batch=graph)
    assert graph.inputs == []


def test_the_local_worker_listens_on_loopback_only():
    assert runtime.LOCAL_HOST == "127.0.0.1"


def test_the_batch_path_is_cloud_only_until_the_mac_worker(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "local")
    runtime.batch_graph.cache_clear()
    with pytest.raises(runtime.PayloadError):
        runtime.batch_graph()
    runtime.batch_graph.cache_clear()


# The Gateway client


def test_gateway_names_map_back_to_plain_names():
    assert plain_name("tracker-list___list_applications") == "list_applications"
    assert plain_name("list_applications") == "list_applications"


def test_results_parse_from_text_or_structured_content():
    text = SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text='{"count": 2}')])
    structured = SimpleNamespace(isError=False, structuredContent={"count": 3}, content=[])
    assert parse_result(text) == {"count": 2}
    assert parse_result(structured) == {"count": 3}


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(isError=True, structuredContent=None, content=[SimpleNamespace(text="Mülakat")]),
        SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text="not json")]),
        SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text="[1]")]),
    ],
)
def test_failures_raise_without_their_text(result):
    with pytest.raises(GatewayToolError) as error:
        parse_result(result)
    assert "Mülakat" not in str(error.value)


def test_requests_are_signed_for_agentcore_in_stockholm():
    credentials = SimpleNamespace(get_frozen_credentials=lambda: Credentials("AKIDEXAMPLE", "secret", "token"))
    request = httpx.Request("POST", GATEWAY, content=b'{"jsonrpc":"2.0"}', headers={"content-type": "application/json"})
    signed = next(sigv4_auth(credentials).auth_flow(request))
    authorization = signed.headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/eu-north-1/bedrock-agentcore/aws4_request" in authorization
    assert signed.headers["x-amz-security-token"] == "token"
    assert signed.headers["host"] == urlparse(GATEWAY).netloc


def test_calls_are_checked_before_they_leave():
    tools = GatewayTools(GATEWAY, credentials=object(), names=("list_applications",))
    assert [spec.name for spec in tools.specs()] == ["list_applications"]
    with pytest.raises(ToolNotAllowed):
        tools.call("notify_owner", {"kind": "urgent", "email_class": "offer", "company": "Kuzey"})
    with pytest.raises(ToolInputError):
        tools.call("list_applications", {"recipient": "someone@example.com"})


def test_the_gateway_url_must_be_https():
    with pytest.raises(ValueError):
        GatewayTools("http://gw.example.com/mcp")


def test_a_tool_failure_reaches_the_caller_unwrapped(monkeypatch):
    # The MCP client's task groups wrap one failure in an ExceptionGroup.
    tools = GatewayTools(GATEWAY, credentials=object(), names=("list_applications",))

    async def failing(name, arguments):
        raise ExceptionGroup("task group", [ExceptionGroup("session", [GatewayToolError("the tool failed behind the Gateway")])])

    monkeypatch.setattr(tools, "_call", failing)
    with pytest.raises(GatewayToolError, match="failed behind the Gateway"):
        tools.call("list_applications", {})


# The one-off policy probe (OQ-44 k)


class ProbeGateway:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def call_unlisted(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return {"saved": True}


def test_the_probe_is_refused_unless_switched_on(monkeypatch):
    monkeypatch.delenv("POLICY_PROBE", raising=False)
    with pytest.raises(runtime.PayloadError):
        runtime.handle({"type": "policy_probe"})


def test_the_probe_calls_the_draft_tool_by_its_gateway_name_with_a_made_up_id(monkeypatch):
    gateway = ProbeGateway(RuntimeError("AccessDeniedException: denied by policy"))
    monkeypatch.setenv("POLICY_PROBE", "1")
    monkeypatch.setattr(runtime, "_gateway_for_probe", lambda: gateway)
    outcome = runtime.handle({"type": "policy_probe"})
    assert gateway.calls == [("create-gmail-draft___create_gmail_draft", {"approval_id": "0" * 32})]
    assert outcome == {"tool": "create_gmail_draft", "refused": True, "error": "RuntimeError", "detail": "AccessDeniedException: denied by policy"}


def test_a_probe_that_was_not_refused_says_so():
    assert runtime.policy_probe(ProbeGateway())["refused"] is False

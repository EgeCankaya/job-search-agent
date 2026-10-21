"""MacBook mode's tools (OQ-42 j): the local MCP server, the stdio client the
local worker uses, and what the worker hands the child process. The server
authorizes every call with cedarpy over the repo's policies (MODE-4, OQ-45 e);
the UAC-MODE-4 cases run against a real server process over stdio."""

import os
import sys
from datetime import date
from pathlib import Path

import anyio
import pytest

from inbox_agent import runtime
from inbox_agent.cedar_policies import Authorizer, local_policies
from inbox_agent.local_mcp_server import LOCAL_TOOLS, POLICY_DIR_ENV, build_server
from inbox_agent.stdio_tools import LocalToolError, StdioTools
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tools import TOOL_SPECS, ToolInputError, ToolNotAllowed
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable
from tests.local_mcp_fixture_server import CSV

ROOT = Path(__file__).resolve().parents[2]


def seeded_tools():
    from inbox_agent.import_applications import import_rows, parse_csv

    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(CSV), TrackerTable(table))
    return InProcessTools(ToolDeps(tracker=TrackerTable(table), today=lambda: date(2026, 10, 19)), names=LOCAL_TOOLS)


async def with_session(action):
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(build_server(seeded_tools(), Authorizer(local_policies()))) as session:
        return await action(session)


def test_the_server_lists_the_tracker_tools_with_the_shared_schemas():
    async def listed(session):
        return (await session.list_tools()).tools

    tools = anyio.run(with_session, listed)
    assert [t.name for t in tools] == list(LOCAL_TOOLS)
    for tool in tools:
        assert tool.inputSchema == TOOL_SPECS[tool.name].input_schema
        assert tool.description == TOOL_SPECS[tool.name].description


def test_a_call_returns_the_tool_result():
    async def call(session):
        return await session.call_tool("list_applications", {"status": "open"})

    result = anyio.run(with_session, call)
    assert not result.isError
    assert result.structuredContent["count"] == 2
    assert result.structuredContent["today"] == "2026-10-19"


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("list_applications", {"status": "hired"}, "status is not one of the allowed values"),
        ("list_applications", {"recipient": "someone@example.com"}, "refused by policy (NoRecipientKeys)"),
        ("list_applications", {"surname": "Yılmaz"}, "has no input named like that"),
        ("notify_owner", {"kind": "urgent", "email_class": "offer", "company": "Brightpath"}, "notify_owner is not available here"),
    ],
)
def test_refusals_come_back_short_and_without_values(name, arguments, message):
    async def call(session):
        return await session.call_tool(name, arguments)

    result = anyio.run(with_session, call)
    assert result.isError
    text = result.content[0].text
    assert message in text
    assert "hired" not in text and "example.com" not in text and "Yılmaz" not in text


@pytest.fixture(scope="module")
def stdio_tools():
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
    tools = StdioTools(
        ("list_applications",), command=sys.executable, args=[str(ROOT / "tests" / "local_mcp_fixture_server.py")], env=env
    )
    yield tools
    tools.close()


def test_the_stdio_client_calls_a_real_child_process(stdio_tools):
    result = stdio_tools.call("list_applications", {"company": "brightpath"})
    assert result["count"] == 1
    assert result["applications"][0]["role"] == "Software Engineer"
    # The session stays open between calls.
    assert stdio_tools.call("list_applications", {})["count"] == 2


def test_the_stdio_client_checks_inputs_before_sending(stdio_tools):
    with pytest.raises(ToolInputError):
        stdio_tools.call("list_applications", {"min_days_since_contact": -1})
    with pytest.raises(ToolNotAllowed):
        stdio_tools.call("upsert_application", {"company": "Brightpath"})


def test_a_server_refusal_reaches_the_caller_as_a_short_error(stdio_tools):
    with pytest.raises(LocalToolError, match="company is empty after normalization"):
        stdio_tools.call("list_applications", {"company": "..."})


def test_a_server_that_cannot_serve_the_tools_fails_at_start():
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
    with pytest.raises(LocalToolError):
        StdioTools(("notify_owner",), command=sys.executable, args=[str(ROOT / "tests" / "local_mcp_fixture_server.py")], env=env)


def test_the_worker_gives_the_child_only_what_it_needs(monkeypatch):
    seen = {}

    class Capture:
        def __init__(self, names, *, command, args, env):
            seen.update(names=names, command=command, args=args, env=env)

    monkeypatch.setattr("inbox_agent.stdio_tools.StdioTools", Capture)
    runtime._local_tools({"TRACKER_TABLE_NAME": "tracker-table", "AWS_PROFILE": "default", "SOME_TOKEN": "secret", "GATEWAY_URL": "x"})
    assert seen["names"] == ("list_applications",)
    assert (seen["command"], seen["args"]) == (sys.executable, ["-m", "inbox_agent.local_mcp_server"])
    env = seen["env"]
    assert env["TRACKER_TABLE_NAME"] == "tracker-table"
    assert env["AWS_PROFILE"] == "default"
    assert env["PYTHONPATH"] == str(ROOT / "src")
    assert "SOME_TOKEN" not in env and "GATEWAY_URL" not in env


# UAC-MODE-4 (week 5 exit 1): a stdio client against the real server process,
# with no client-side checks in the way.
FIXTURE = ROOT / "tests" / "local_mcp_fixture_server.py"


def raw_calls(calls, *, errlog, policy_dir=None):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
    if policy_dir is not None:
        env[POLICY_DIR_ENV] = str(policy_dir)

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(FIXTURE)], env=env)
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [await session.call_tool(name, arguments) for name, arguments in calls]

    return anyio.run(run)


def denials(log_path):
    import json

    lines = [line for line in log_path.read_text().splitlines() if '"tool_denied"' in line]
    return [json.loads(line[line.index("{") :]) for line in lines]


def test_the_server_refuses_a_send_a_recipient_and_the_draft_tool(tmp_path):
    log_path = tmp_path / "server.log"
    with log_path.open("w") as errlog:
        send, recipient, draft, served = raw_calls(
            [
                ("send_email", {}),
                ("upsert_application", {"company": "Örnek Test Ltd", "to": "someone@example.com"}),
                ("create_gmail_draft", {"approval_id": "0" * 32}),
                ("list_applications", {"status": "open"}),
            ],
            errlog=errlog,
        )
    # TOOL-2: a send-like tool, and a recipient key on a served tool.
    assert send.isError and send.content[0].text == "refused by policy (NeverSend)"
    assert recipient.isError and recipient.content[0].text == "refused by policy (NoRecipientKeys)"
    # TOOL-4: the agent may not draft.
    assert draft.isError and draft.content[0].text == "refused by policy (OnlyTheWorkflowDrafts)"
    # The served tracker tools still work.
    assert not served.isError and served.structuredContent["count"] == 2
    logged = denials(log_path)
    assert [(d["tool"], d["policy_ids"]) for d in logged] == [
        ("unknown", ["NeverSend"]),
        ("upsert_application", ["NoRecipientKeys"]),
        ("create_gmail_draft", ["OnlyTheWorkflowDrafts"]),
    ]
    text = log_path.read_text()
    assert "example.com" not in text and "Örnek" not in text and "Ã" not in text


def test_with_no_policy_files_every_call_is_refused(tmp_path):
    empty = tmp_path / "policies"
    empty.mkdir()
    log_path = tmp_path / "server.log"
    with log_path.open("w") as errlog:
        results = raw_calls([("list_applications", {}), ("upsert_application", {"company": "Örnek Test Ltd"})], errlog=errlog, policy_dir=empty)
    assert all(r.isError and r.content[0].text == "refused by policy (no permit)" for r in results)
    assert [d["policy_ids"] for d in denials(log_path)] == [[], []]


def test_the_server_starts_with_the_repos_policies():
    from inbox_agent.local_mcp_server import authorizer

    assert authorizer({}).decide("AgentCore::IamEntity::\"local:agent\"", "list_applications", {}).allowed

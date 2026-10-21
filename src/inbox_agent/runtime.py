"""The agent's entry point: AgentCore Runtime in cloud mode, the local worker
on 127.0.0.1:8080 in MacBook mode (OQ-42 j).

One handler routes on the payload's type:
- `new_email` (from the queue consumer, OQ-42 a): the batch path;
- `chat` (from the Mac app's Ask page): the chat path;
- `stale_check` (from the queue consumer, APR-1): the stale-check path,
  which returns the day's follow-up drafts.

Approve and Skip decisions never come here: the consumer takes them to the
approval workflow (APR-3).

In cloud mode the tools are the Gateway's (TOOL-1). In MacBook mode they
are the local MCP server's, a child process spoken to over stdio (OQ-42 j).
The batch path is not available locally yet: its Gmail token would come
from the Keychain (ING-4), and the Mac worker that reads the queue isn't
built.

Logs carry IDs, the class, flags and times, never email text, questions or
answers (NFR-PRIV-3). The times are the NFR-OBS-1 fields until week 6 adds
OpenTelemetry (OQ-42 k).
"""

import logging
import os
import re
import time
from datetime import UTC, datetime
from functools import cache

from inbox_agent import settings
from inbox_agent.cedar_policies import gateway_name
from inbox_agent.logs import log_event, setup
from inbox_agent.mode import agent_mode
from inbox_agent.poll import NEW_EMAIL_MESSAGE

logger = logging.getLogger("inbox_agent.runtime")
logger.setLevel(logging.INFO)

CHAT_MESSAGE = "chat"
STALE_CHECK_MESSAGE = "stale_check"
# Exit check 5 (OQ-44 k): accepted only while the Runtime's environment has
# POLICY_PROBE=1, which one deploy turns on and the next turns off.
POLICY_PROBE_MESSAGE = "policy_probe"
POLICY_PROBE_ENV = "POLICY_PROBE"
PROBE_APPROVAL_ID = "0" * 32  # never a real approval_id: no record has it
PROBE_CASES = {
    "draft_tool": ("create_gmail_draft", {"approval_id": PROBE_APPROVAL_ID}),
    "recipient_key": (
        "notify_owner",
        {"kind": "urgent", "email_class": "interview_invite", "company": "Örnek Probe Ltd", "to": "someone@example.com"},
    ),
    "unserved_tool": ("send_email", {}),
}
CHAT_TOOLS = ("list_applications",)  # OQ-42 h
GATEWAY_URL_ENV = "GATEWAY_URL"
LOCAL_HOST = "127.0.0.1"  # never another interface (NFR-REL-4)
LOCAL_PORT = 8080
_MESSAGE_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
# The Ask page's session (app_backend.new_session_id), also the memory session.
_SESSION = re.compile(r"^inbox-agent-ask-[0-9a-f-]{36}$")


class PayloadError(ValueError):
    pass


def _today():
    return datetime.now(settings.OWNER_TIMEZONE).date()


def _table(env_name: str):
    import boto3

    return boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(os.environ[env_name])


@cache
def _tools():
    if agent_mode() == "cloud":
        from inbox_agent.gateway_tools import GatewayTools

        from inbox_agent.tools import AGENT_TOOLS

        # Never create_gmail_draft: only the approval workflow calls it (APR-3).
        return GatewayTools(os.environ[GATEWAY_URL_ENV], names=AGENT_TOOLS)
    return _local_tools()


def _local_tools(environ=os.environ):
    """The local MCP server as a child process, with the tracker table's name
    (from TRACKER_TABLE_NAME, or else the deployed stack) and nothing else
    from this process's environment beyond MCP's safe defaults and AWS's
    profile settings."""
    from pathlib import Path

    from mcp.client.stdio import get_default_environment

    from inbox_agent.local_mcp_server import TRACKER_TABLE_ENV
    from inbox_agent.stdio_tools import StdioTools, server_command

    table = environ.get(TRACKER_TABLE_ENV)
    if not table:
        import boto3

        from inbox_agent.app_backend import discover

        table = discover(boto3.client("cloudformation", region_name=settings.AWS_REGION), mode="local").tracker_table
    env = {
        **get_default_environment(),
        **{key: environ[key] for key in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE") if key in environ},
        TRACKER_TABLE_ENV: table,
        # The package isn't installed; the child finds it where this one is.
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    command, args = server_command()
    return StdioTools(CHAT_TOOLS, command=command, args=args, env=env)


@cache
def batch_graph():
    if agent_mode() != "cloud":
        raise PayloadError("the batch path runs only in cloud mode until the Mac worker exists")
    from inbox_agent.batch_path import BatchDeps, build_batch_graph
    from inbox_agent.model import LangChainModel, chat_model
    from inbox_agent.processed_log import ProcessedLog

    deps = BatchDeps(
        tools=_tools(),
        model=LangChainModel(chat_model("classify")),
        log=ProcessedLog(_table("PROCESSED_EMAILS_TABLE_NAME")),
        now=lambda: datetime.now(UTC),
        memory=_memory(),
    )
    return build_batch_graph(deps)


@cache
def _memory():
    from inbox_agent.memory import memory_for

    return memory_for()


@cache
def stale_check_deps():
    if agent_mode() != "cloud":
        raise PayloadError("the stale check runs only in cloud mode until the Mac worker exists")
    from inbox_agent.model import LangChainModel, chat_model
    from inbox_agent.processed_log import ProcessedLog
    from inbox_agent.stale_check import StaleCheckDeps
    from inbox_agent.tracker import TrackerTable

    memory = _memory()
    return StaleCheckDeps(
        tools=_tools(),
        model=LangChainModel(chat_model("draft")),
        log=ProcessedLog(_table("PROCESSED_EMAILS_TABLE_NAME")),
        tracker_rows=TrackerTable(_table("TRACKER_TABLE_NAME")).rows,
        now=lambda: datetime.now(UTC),
        # MEM-2: my preferences and the company's summary shape the draft.
        tone=lambda: "; ".join(memory.preferences()) or None,
        summary=memory.company_summary,
    )


@cache
def chat_graph():
    from inbox_agent.chat_path import ChatDeps, build_chat_graph
    from inbox_agent.model import chat_model
    from inbox_agent.tools import RestrictedTools

    return build_chat_graph(ChatDeps(chat_model=chat_model("chat"), tools=RestrictedTools(_tools(), CHAT_TOOLS), today=_today))


def handle(payload, *, batch=None, chat=None, stale=None, today=_today, clock=time.monotonic) -> dict:
    """`batch` and `chat` are the compiled graphs and `stale` the stale
    check's dependencies; tests pass their own."""
    if not isinstance(payload, dict):
        raise PayloadError("the payload must be an object")
    kind = payload.get("type")
    started = clock()

    if kind == NEW_EMAIL_MESSAGE:
        from inbox_agent.batch_path import handle_new_email

        message_id = payload.get("message_id")
        if not isinstance(message_id, str) or not _MESSAGE_ID.match(message_id):
            raise PayloadError("new_email needs a Gmail message ID")
        try:
            outcome = handle_new_email(batch or batch_graph(), message_id)
        except Exception as error:
            # The type only: an exception message could quote the email.
            log_event(logger, "batch_failed", logging.ERROR, message_id=message_id, error=type(error).__name__)
            raise
        fields = {**outcome, "seconds": round(clock() - started, 3)}
        # NFR-LAT-1 and NFR-LAT-2 from these, with CloudWatch Logs Insights.
        for key in ("received_at", "detected_at"):
            if isinstance(payload.get(key), str):
                fields[key] = payload[key]
        if outcome.get("alerted"):
            fields["published_at"] = datetime.now(UTC).isoformat()
        log_event(logger, "batch_done", **fields)
        return outcome

    if kind == CHAT_MESSAGE:
        from inbox_agent.chat_path import answer

        session = payload.get("session_id")
        memory = _memory() if isinstance(session, str) and _SESSION.match(session) else None
        reply = answer(chat or chat_graph(), payload.get("question"), today=today(), memory=memory, session_id=session if memory else None)
        log_event(logger, "chat_done", seconds=round(clock() - started, 3))
        return {"answer": reply}

    if kind == POLICY_PROBE_MESSAGE and os.environ.get(POLICY_PROBE_ENV) == "1":
        return policy_probe(_gateway_for_probe())

    if kind == STALE_CHECK_MESSAGE:
        from inbox_agent.stale_check import run_stale_check

        try:
            outcome = run_stale_check(stale or stale_check_deps(), today())
        except Exception as error:
            log_event(logger, "stale_check_failed", logging.ERROR, error=type(error).__name__)
            raise
        # Counts only: the drafts hold text and addresses (NFR-PRIV-3).
        log_event(
            logger,
            "stale_check_done",
            **{key: outcome[key] for key in ("check_date", "eligible", "rejected", "notified")},
            drafts=len(outcome["drafts"]),
            approval_ids=[draft["approval_id"] for draft in outcome["drafts"]],
            seconds=round(clock() - started, 3),
        )
        return outcome

    raise PayloadError("unknown payload type")


def _gateway_for_probe():
    from inbox_agent.gateway_tools import GatewayTools

    return GatewayTools(os.environ[GATEWAY_URL_ENV])


def policy_probe(gateway) -> dict:
    """Week 4 exit 5 and week 5 exit 1 (OQ-44 k, OQ-45 d): the agent's own
    role makes three calls the Gateway's policies must refuse.

    1. create_gmail_draft (OnlyTheWorkflowDrafts). Had Cedar allowed it, the
       tool would refuse the made-up approval_id, so nothing is drafted.
    2. notify_owner with valid inputs plus a `to` key (NoRecipientKeys).
       Had Cedar allowed it, the tool would refuse the unknown key, so
       nothing is published. The address is fictional.
    3. send_email, a tool outside the six: refused as unknown by the
       Gateway, or by NeverSend if a target ever served it.

    Each case returns whether it was refused, the error type and the
    deciding policy's ID, never the input. Case 3 also returns the start of
    the Gateway's message, to tell an unknown tool from another failure;
    its input is empty, so the message can't quote one."""
    cases = []
    for case, (tool, arguments) in PROBE_CASES.items():
        try:
            gateway.call_unlisted(gateway_name(tool), arguments)
        except Exception as error:
            outcome = {"case": case, "tool": tool, "refused": True, "error": type(error).__name__, "policy_id": getattr(error, "policy_id", None)}
            if not arguments:
                outcome["detail"] = str(error)[:200]
        else:
            outcome = {"case": case, "tool": tool, "refused": False}
        cases.append(outcome)
    refused = all(outcome["refused"] for outcome in cases)
    log_event(
        logger,
        "policy_probe_done",
        refused=refused,
        cases=[{key: outcome.get(key) for key in ("case", "refused", "error", "policy_id")} for outcome in cases],
    )
    return {"refused": refused, "cases": cases}


def _app():
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        return handle(payload)

    return app


def main() -> None:
    """The process AgentCore Runtime starts (the ZIP's main.py), or the local
    worker on the Mac: `AGENT_MODE=local PYTHONPATH=src uv run python -m inbox_agent.runtime`."""
    app = _app()
    # After the app: the AgentCore SDK sets its own logger's level.
    setup()
    if agent_mode() == "local":
        app.run(port=LOCAL_PORT, host=LOCAL_HOST)
    else:
        app.run(port=LOCAL_PORT)


if __name__ == "__main__":
    main()

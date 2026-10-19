"""The agent's entry point: AgentCore Runtime in cloud mode, the local worker
on 127.0.0.1:8080 in MacBook mode (OQ-42 j).

One handler routes on the payload's type:
- `new_email` (from the queue consumer, OQ-42 a): the batch path;
- `chat` (from the Mac app's Ask page): the chat path.

Week 4 adds the daily stale check. Approve and Skip decisions never come
here: the consumer takes them to the approval workflow (APR-3).

In cloud mode the tools are the Gateway's (TOOL-1). In MacBook mode, until
the local MCP server and the Mac worker exist, the chat path runs the same
tool code in this process, and the batch path is not available: its Gmail
token would come from the Keychain (ING-4), which isn't built yet.

Logs carry IDs, the class, flags and times, never email text, questions or
answers (NFR-PRIV-3). The times are the NFR-OBS-1 fields until week 6 adds
OpenTelemetry (OQ-42 k).
"""

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from functools import cache

from inbox_agent import settings
from inbox_agent.mode import agent_mode
from inbox_agent.poll import NEW_EMAIL_MESSAGE

logger = logging.getLogger("inbox_agent.runtime")
logger.setLevel(logging.INFO)

CHAT_MESSAGE = "chat"
CHAT_TOOLS = ("list_applications",)  # OQ-42 h
GATEWAY_URL_ENV = "GATEWAY_URL"
LOCAL_HOST = "127.0.0.1"  # never another interface (NFR-REL-4)
LOCAL_PORT = 8080
_MESSAGE_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


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

        return GatewayTools(os.environ[GATEWAY_URL_ENV])
    from inbox_agent.tool_handlers import InProcessTools, ToolDeps
    from inbox_agent.tracker import TrackerTable

    deps = ToolDeps(tracker=TrackerTable(_table("TRACKER_TABLE_NAME")), today=_today)
    return InProcessTools(deps, names=CHAT_TOOLS)


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
    )
    return build_batch_graph(deps)


@cache
def chat_graph():
    from inbox_agent.chat_path import ChatDeps, build_chat_graph
    from inbox_agent.model import chat_model
    from inbox_agent.tools import RestrictedTools

    return build_chat_graph(ChatDeps(chat_model=chat_model("chat"), tools=RestrictedTools(_tools(), CHAT_TOOLS), today=_today))


def handle(payload, *, batch=None, chat=None, today=_today, clock=time.monotonic) -> dict:
    """`batch` and `chat` are the compiled graphs; tests pass their own."""
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
            logger.error(json.dumps({"event": "batch_failed", "message_id": message_id, "error": type(error).__name__}))
            raise
        fields = {"event": "batch_done", **outcome, "seconds": round(clock() - started, 3)}
        # NFR-LAT-1 and NFR-LAT-2 from these, with CloudWatch Logs Insights.
        for key in ("received_at", "detected_at"):
            if isinstance(payload.get(key), str):
                fields[key] = payload[key]
        if outcome.get("alerted"):
            fields["published_at"] = datetime.now(UTC).isoformat()
        logger.info(json.dumps(fields))
        return outcome

    if kind == CHAT_MESSAGE:
        from inbox_agent.chat_path import answer

        reply = answer(chat or chat_graph(), payload.get("question"), today=today())
        logger.info(json.dumps({"event": "chat_done", "seconds": round(clock() - started, 3)}))
        return {"answer": reply}

    raise PayloadError("unknown payload type")


def _app():
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        return handle(payload)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = _app()
    if agent_mode() == "local":
        app.run(port=LOCAL_PORT, host=LOCAL_HOST)
    else:
        app.run(port=LOCAL_PORT)

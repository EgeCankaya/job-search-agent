"""One tool behind AgentCore Gateway (TOOL-1, OQ-42 c).

Each tool is its own Lambda function with its own role, and TOOL_NAME says
which tool the function serves. The Gateway passes the tool's arguments as
the event and names the tool in the client context as `<target>___<tool>`;
a call naming any other tool is refused.

Logs carry the tool name, a message ID and counts, never arguments or
results, which can hold email text or tracker data (NFR-PRIV-3).
"""

import json
import logging
import os

from inbox_agent import settings
from inbox_agent.alerts import AlertTopic
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tools import TOOL_SPECS, ToolNotAllowed

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOOL_NAME_ENV = "TOOL_NAME"
GATEWAY_TOOL_NAME_KEY = "bedrockAgentCoreToolName"
TARGET_SEPARATOR = "___"


def gateway_tool_name(context: object) -> str:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) or {}
    return str(custom.get(GATEWAY_TOOL_NAME_KEY, "")).rpartition(TARGET_SEPARATOR)[2]


def _deps(name: str) -> ToolDeps:
    import boto3

    if name == "read_email":
        from inbox_agent.gmail import gmail_client

        return ToolDeps(gmail=gmail_client())
    if name in ("upsert_application", "list_applications"):
        from inbox_agent.tracker import TrackerTable

        table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(os.environ["TRACKER_TABLE_NAME"])
        return ToolDeps(tracker=TrackerTable(table))
    if name == "notify_owner":
        sns = boto3.client("sns", region_name=settings.AWS_REGION)
        return ToolDeps(topic=AlertTopic(sns, os.environ["ALERT_TOPIC_ARN"]))
    raise ValueError(f"unknown tool {name}")


def _log_fields(name: str, arguments: object, result: dict) -> dict:
    fields: dict = {"event": "tool_done", "tool": name}
    if isinstance(arguments, dict) and isinstance(arguments.get("message_id"), str):
        fields["message_id"] = arguments["message_id"]
    for key in ("count", "created", "written", "published"):
        if key in result:
            fields[key] = result[key]
    return fields


def handle(event: object, context: object, *, deps: ToolDeps, name: str) -> dict:
    if name not in TOOL_SPECS:
        raise ValueError(f"unknown tool {name}")
    if gateway_tool_name(context) != name:
        logger.error(json.dumps({"event": "tool_refused", "tool": name}))
        raise ToolNotAllowed(f"this function serves {name} only")
    try:
        result = InProcessTools(deps, names=(name,)).call(name, event)
    except Exception as error:
        # The type only: an exception message could quote the input.
        logger.error(json.dumps({"event": "tool_failed", "tool": name, "error": type(error).__name__}))
        raise
    logger.info(json.dumps(_log_fields(name, event, result)))
    return result


def handler(event: object, context: object) -> dict:
    name = os.environ[TOOL_NAME_ENV]
    return handle(event, context, deps=_deps(name), name=name)

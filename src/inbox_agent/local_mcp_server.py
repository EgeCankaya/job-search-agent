"""The local MCP server (TOOL-1, MODE-1, OQ-42 j): MacBook mode's tools, run
by the local worker as a child process and spoken to over stdio, so it opens
no port at all (NFR-REL-4).

It serves the same tool code and the same schemas as the Gateway's Lambdas
(tools.py, tool_handlers.py). In week 3 it serves the tracker tools only:
`read_email` needs the Keychain's Gmail token and `notify_owner` the Mac
worker's publisher, which come later.

Every call is first authorized with cedarpy against the same policy files as
the Gateway (MODE-4, OQ-45 e): principal `local:agent` (the worker, its one
caller), action `<target>___<tool>`, context `{"input": arguments}`. A deny,
an evaluation error, or no policies at all refuses the call before any tool
code runs, and is logged as `tool_denied` with the tool and the deciding
policies, never the input. An unknown tool goes through the same check and is
denied by default (or by NeverSend).

Errors go back as a short message: the tool's own refusal, which never holds
a value (tools.py), or just the error's class. stdout carries the protocol,
so logs go to stderr, with IDs and counts only.
"""

import logging
import os
from pathlib import Path

from inbox_agent.cedar_policies import LOCAL_AGENT, POLICY_DIR, Authorizer, local_policies
from inbox_agent.logs import log_event, setup
from inbox_agent.tools import TOOL_SPECS, ToolInputError, ToolNotAllowed, Tools

LOCAL_TOOLS = ("list_applications", "upsert_application")
SERVER_NAME = "inbox-agent-local"
TRACKER_TABLE_ENV = "TRACKER_TABLE_NAME"
# Tests point this at another directory; the worker never sets it.
POLICY_DIR_ENV = "INBOX_AGENT_POLICY_DIR"

logger = logging.getLogger("inbox_agent.local_mcp_server")


def authorizer(environ=os.environ) -> Authorizer:
    """The local policies, from the repo's policies/ (or POLICY_DIR_ENV)."""
    directory = Path(environ[POLICY_DIR_ENV]) if environ.get(POLICY_DIR_ENV) else POLICY_DIR
    return Authorizer(local_policies(directory))


def build_server(tools: Tools, policies: Authorizer):
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server

    server = Server(SERVER_NAME)

    def error(text: str) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=s.name, description=s.description, inputSchema=s.input_schema) for s in tools.specs()]

    # tools.call checks every input against the schema itself, with messages
    # that never echo a value; the SDK's own check would quote them.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        decision = policies.decide(LOCAL_AGENT, name, arguments)
        if not decision.allowed:
            tool = name if name in TOOL_SPECS else "unknown"
            log_event(logger, "tool_denied", logging.WARNING, tool=tool, policy_ids=list(decision.policy_ids), errors=decision.errors)
            return error(f"refused by policy ({', '.join(decision.policy_ids) or 'no permit'})")
        try:
            result = await anyio.to_thread.run_sync(tools.call, name, arguments)
        except (ToolInputError, ToolNotAllowed) as refusal:
            return error(str(refusal))
        except Exception as failure:
            log_event(logger, "local_tool_failed", logging.ERROR, tool=name, error=type(failure).__name__)
            return error(f"the tool failed ({type(failure).__name__})")
        log_event(logger, "local_tool_done", tool=name, count=result.get("count"))
        return result

    return server


async def serve(server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import anyio
    import boto3

    from inbox_agent import settings
    from inbox_agent.tool_handlers import InProcessTools, ToolDeps
    from inbox_agent.tracker import TrackerTable

    setup()
    table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(os.environ[TRACKER_TABLE_ENV])
    tools = InProcessTools(ToolDeps(tracker=TrackerTable(table)), names=LOCAL_TOOLS)
    anyio.run(serve, build_server(tools, authorizer()))


if __name__ == "__main__":
    main()

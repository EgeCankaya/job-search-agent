"""The local MCP server (TOOL-1, MODE-1, OQ-42 j): MacBook mode's tools, run
by the local worker as a child process and spoken to over stdio, so it opens
no port at all (NFR-REL-4).

It serves the same tool code and the same schemas as the Gateway's Lambdas
(tools.py, tool_handlers.py). In week 3 it serves the tracker tools only:
`read_email` needs the Keychain's Gmail token and `notify_owner` the Mac
worker's publisher, which come later. Cedar enforcement through cedarpy
joins in week 5 (MODE-4).

Errors go back as a short message: the tool's own refusal, which never holds
a value (tools.py), or just the error's class. stdout carries the protocol,
so logs go to stderr, with IDs and counts only.
"""

import json
import logging
import os
import sys

from inbox_agent.tools import ToolInputError, ToolNotAllowed, Tools

LOCAL_TOOLS = ("list_applications", "upsert_application")
SERVER_NAME = "inbox-agent-local"
TRACKER_TABLE_ENV = "TRACKER_TABLE_NAME"

logger = logging.getLogger("inbox_agent.local_mcp_server")


def build_server(tools: Tools):
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
        try:
            result = await anyio.to_thread.run_sync(tools.call, name, arguments)
        except (ToolInputError, ToolNotAllowed) as refusal:
            return error(str(refusal))
        except Exception as failure:
            logger.error(json.dumps({"event": "local_tool_failed", "tool": name, "error": type(failure).__name__}))
            return error(f"the tool failed ({type(failure).__name__})")
        logger.info(json.dumps({"event": "local_tool_done", "tool": name, "count": result.get("count")}))
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

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(os.environ[TRACKER_TABLE_ENV])
    tools = InProcessTools(ToolDeps(tracker=TrackerTable(table)), names=LOCAL_TOOLS)
    anyio.run(serve, build_server(tools))


if __name__ == "__main__":
    main()

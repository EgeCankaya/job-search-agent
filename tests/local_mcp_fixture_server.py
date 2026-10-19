"""A local MCP server over stdio with a seeded in-memory tracker, for the
StdioTools tests. Companies are fictional."""

import anyio

from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.local_mcp_server import LOCAL_TOOLS, build_server, serve
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable

CSV = """company,role,applied_date,status
Brightpath,Software Engineer,2026-10-12,interviewing
Kuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied
"""

if __name__ == "__main__":
    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(CSV), TrackerTable(table))
    tools = InProcessTools(ToolDeps(tracker=TrackerTable(table)), names=LOCAL_TOOLS)
    anyio.run(serve, build_server(tools))

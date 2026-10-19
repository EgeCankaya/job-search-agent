"""The tools through the local MCP server (MODE-1, OQ-42 j): the `Tools`
interface the local worker's graph uses in MacBook mode.

The server runs as a child process for as long as the worker does, spoken
to over stdio. The MCP client is asynchronous and keeps its session open,
so it lives on its own event loop in a background thread; `call` hands each
request over and waits. Arguments are checked against the schemas before
they leave, as with the Gateway.
"""

import asyncio
import sys
import threading
from collections.abc import Mapping

from inbox_agent.tools import TOOL_SPECS, ToolNotAllowed, ToolSpec, validate

CALL_TIMEOUT_SECONDS = 30
START_TIMEOUT_SECONDS = 60


class LocalToolError(ValueError):
    """The server refused or failed. Its text is the server's own short
    message, which never holds a value, so the model may see it."""


def server_command(module: str = "inbox_agent.local_mcp_server") -> tuple[str, list[str]]:
    return sys.executable, ["-m", module]


class StdioTools:
    def __init__(self, names: tuple[str, ...], *, command: str, args: list[str], env: dict[str, str] | None = None):
        from mcp.client.stdio import StdioServerParameters

        self._names = tuple(names)
        self._params = StdioServerParameters(command=command, args=args, env=env)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="local-mcp-client", daemon=True)
        self._thread.start()
        self._queue: asyncio.Queue | None = None
        ready = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        self._session_task = ready.result(timeout=START_TIMEOUT_SECONDS)

    async def _start(self):
        self._queue = asyncio.Queue()
        started = self._loop.create_future()
        task = self._loop.create_task(self._session(started))
        await started  # raises if the server failed to start
        return task

    async def _session(self, started: asyncio.Future) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    served = {tool.name for tool in (await session.list_tools()).tools}
                    if not set(self._names) <= served:
                        started.set_exception(LocalToolError("the local MCP server doesn't serve every tool asked for"))
                        return
                    started.set_result(None)
                    while True:
                        request = await self._queue.get()
                        if request is None:
                            return
                        name, arguments, reply = request
                        try:
                            reply.set_result(_result(await session.call_tool(name, arguments)))
                        except Exception as error:
                            reply.set_exception(error)
        except Exception as error:
            # The MCP client's task groups wrap a single failure in a group.
            while isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
                error = error.exceptions[0]
            if not started.done():
                started.set_exception(error)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(TOOL_SPECS[name] for name in self._names)

    def call(self, name: str, arguments: Mapping) -> dict:
        if name not in self._names:
            raise ToolNotAllowed(f"{name} is not available here")
        checked = validate(TOOL_SPECS[name], arguments)

        async def submit():
            reply = self._loop.create_future()
            await self._queue.put((name, checked, reply))
            return await reply

        return asyncio.run_coroutine_threadsafe(submit(), self._loop).result(timeout=CALL_TIMEOUT_SECONDS)

    def close(self) -> None:
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop).result(timeout=5)
            asyncio.run_coroutine_threadsafe(asyncio.wait_for(self._session_task, 10), self._loop).result(timeout=15)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)


def _result(result) -> dict:
    text = "".join(getattr(block, "text", "") for block in result.content)
    if result.isError:
        raise LocalToolError(text or "the tool failed")
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    raise LocalToolError("the tool returned no object")

"""The tools through AgentCore Gateway (TOOL-1), as the agent's IAM role
(OQ-42 e): MCP over streamable HTTP, every request signed with SigV4.

The Gateway names each tool `<target>___<tool>`; the graph uses the plain
names, so the mapping is read from the Gateway's own list. Arguments are
checked against the same schemas before they leave (tools.py), so a bad call
fails here, not in a Lambda.

Each call opens its own MCP session. The batch path makes 3 or 4 calls per
email, so the extra handshake costs little, and nothing is left open between
Runtime requests.
"""

import asyncio
import json
from collections.abc import Mapping

from inbox_agent import settings
from inbox_agent.tools import TOOL_SPECS, ToolSpec, validate

SERVICE = "bedrock-agentcore"
TARGET_SEPARATOR = "___"
TIMEOUT_SECONDS = 30


class GatewayToolError(RuntimeError):
    """The tool failed behind the Gateway. Its text never reaches the logs."""


def sigv4_auth(credentials, region: str = settings.AWS_REGION):
    """An httpx Auth that signs each request as the given AWS credentials."""
    import httpx
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    class _SigV4(httpx.Auth):
        requires_request_body = True

        def auth_flow(self, request):
            headers = {"host": request.url.netloc.decode()}
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]
            signed = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
            SigV4Auth(credentials.get_frozen_credentials(), SERVICE, region).add_auth(signed)
            for name, value in signed.headers.items():
                request.headers[name] = value
            yield request

    return _SigV4()


def plain_name(gateway_name: str) -> str:
    return gateway_name.rpartition(TARGET_SEPARATOR)[2]


def parse_result(result) -> dict:
    """A CallToolResult as the tool's own dict."""
    if result.isError:
        raise GatewayToolError("the tool failed behind the Gateway")
    if isinstance(getattr(result, "structuredContent", None), dict):
        return result.structuredContent
    text = "".join(getattr(block, "text", "") for block in result.content)
    try:
        value = json.loads(text)
    except ValueError:
        raise GatewayToolError("the tool returned something other than JSON") from None
    if not isinstance(value, dict):
        raise GatewayToolError("the tool returned something other than an object")
    return value


class GatewayTools:
    def __init__(self, url: str, credentials=None, names: tuple[str, ...] | None = None) -> None:
        if not url.startswith("https://"):
            raise ValueError("the Gateway URL must be https")
        self._url = url
        self._credentials = credentials
        self._names = tuple(names) if names is not None else tuple(TOOL_SPECS)
        self._gateway_names: dict[str, str] | None = None

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(TOOL_SPECS[name] for name in self._names)

    def _auth(self):
        if self._credentials is None:
            import boto3

            self._credentials = boto3.Session().get_credentials()
        return sigv4_auth(self._credentials)

    async def _call(self, name: str, arguments: dict) -> dict:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(auth=self._auth(), timeout=TIMEOUT_SECONDS) as http:
            async with streamable_http_client(self._url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if self._gateway_names is None:
                        listed = await session.list_tools()
                        self._gateway_names = {plain_name(tool.name): tool.name for tool in listed.tools}
                    if name not in self._gateway_names:
                        raise GatewayToolError(f"the Gateway doesn't serve {name}")
                    return parse_result(await session.call_tool(self._gateway_names[name], arguments))

    def call(self, name: str, arguments: Mapping) -> dict:
        from inbox_agent.tools import ToolNotAllowed

        if name not in self._names:
            raise ToolNotAllowed(f"{name} is not available here")
        checked = validate(TOOL_SPECS[name], arguments)
        # The Runtime runs synchronous handlers in worker threads, which have
        # no event loop of their own.
        return asyncio.run(self._call(name, checked))

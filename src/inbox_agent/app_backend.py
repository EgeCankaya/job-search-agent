"""What the Mac app calls (CHT-1, CHT-2, DASH-1), kept out of the Streamlit
pages so it can be tested.

Every AWS call uses the owner's own `aws login` session: there is no other
credential and no endpoint to reach the app (CHT-2). The app finds the
tables and the Runtime from the deployed stacks, so no name or account ID is
written down anywhere.

- Dashboard: scans the tracker and the processed-email log. Nothing here
  writes to either.
- Ask: cloud mode calls AgentCore Runtime (`InvokeAgentRuntime`); MacBook
  mode calls the local worker on 127.0.0.1:8080, the same entry point.
"""

import json
import os
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from inbox_agent import settings
from inbox_agent.dashboard import Dashboard, build_dashboard, week_start
from inbox_agent.mode import agent_mode
from inbox_agent.processed_log import alert_claims
from inbox_agent.tracker import TrackerTable

ALWAYS_FREE_STACK = "InboxAgentAlwaysFree"
AGENTCORE_STACK = "InboxAgentAgentCore"
LOCAL_WORKER_URL = "http://127.0.0.1:8080/invocations"
# Runtime session IDs are at least 33 characters; one per app session.
SESSION_PREFIX = "inbox-agent-ask-"
ASK_TIMEOUT_SECONDS = 60


class AskError(RuntimeError):
    """The agent didn't answer. The text names the status, never the question."""


@dataclass(frozen=True)
class Resources:
    tracker_table: str
    processed_table: str
    runtime_arn: str | None  # None in MacBook mode


def new_session_id() -> str:
    return f"{SESSION_PREFIX}{uuid.uuid4()}"


def discover(cloudformation, agentcore_control=None, *, mode: str = "cloud") -> Resources:
    """The tables and the Runtime, read from the deployed stacks."""

    def physical_ids(stack: str, type_: str) -> dict[str, str]:
        found: dict[str, str] = {}
        paginator = cloudformation.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack):
            for resource in page["StackResourceSummaries"]:
                if resource["ResourceType"] == type_:
                    found[resource["LogicalResourceId"]] = resource["PhysicalResourceId"]
        return found

    tables = physical_ids(ALWAYS_FREE_STACK, "AWS::DynamoDB::Table")

    def table(prefix: str) -> str:
        [name] = [physical for logical, physical in tables.items() if logical.startswith(prefix)]
        return name

    runtime_arn = None
    if mode == "cloud":
        [runtime_id] = physical_ids(AGENTCORE_STACK, "AWS::BedrockAgentCore::Runtime").values()
        runtime_arn = agentcore_control.get_agent_runtime(agentRuntimeId=runtime_id)["agentRuntimeArn"]
    return Resources(table("ApplicationsTable"), table("ProcessedEmailsTable"), runtime_arn)


def load_dashboard(tracker_table, processed_table, *, now: datetime) -> Dashboard:
    """DASH-1, from scans only."""
    return build_dashboard(
        TrackerTable(tracker_table).rows(), alert_claims(processed_table, week_start(now)), now=now
    )


def ask_cloud(client, runtime_arn: str, session_id: str, question: str) -> str:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"type": "chat", "question": question}).encode(),
    )
    status = response.get("statusCode", 200)
    body = response["response"].read()
    if status >= 300:
        raise AskError(f"the agent returned HTTP {status}")
    return _answer(body)


def ask_local(question: str, opener=urllib.request.urlopen, url: str = LOCAL_WORKER_URL) -> str:
    if not url.startswith("http://127.0.0.1:"):
        raise AskError("the local worker must be on 127.0.0.1")
    request = urllib.request.Request(
        url,
        data=json.dumps({"type": "chat", "question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=ASK_TIMEOUT_SECONDS) as response:
            return _answer(response.read())
    except OSError:
        raise AskError(
            "the local worker isn't answering: start it with `AGENT_MODE=local PYTHONPATH=src uv run python -m inbox_agent.runtime`"
        ) from None


def _answer(body: bytes) -> str:
    try:
        reply = json.loads(body or b"{}")
    except ValueError:
        raise AskError("the agent answered with something other than JSON") from None
    if not isinstance(reply, dict) or not isinstance(reply.get("answer"), str):
        raise AskError("the agent's reply has no answer")
    return reply["answer"]


class Backend:
    """The real one, built from the owner's session in the app's mode."""

    def __init__(self, environ=os.environ) -> None:
        import boto3

        self.mode = agent_mode(environ)
        session = boto3.Session(region_name=settings.AWS_REGION)
        self._agentcore = session.client("bedrock-agentcore") if self.mode == "cloud" else None
        self.resources = discover(
            session.client("cloudformation"),
            session.client("bedrock-agentcore-control") if self.mode == "cloud" else None,
            mode=self.mode,
        )
        dynamodb = session.resource("dynamodb")
        self._tracker = dynamodb.Table(self.resources.tracker_table)
        self._processed = dynamodb.Table(self.resources.processed_table)

    def dashboard(self) -> Dashboard:
        return load_dashboard(self._tracker, self._processed, now=datetime.now(UTC))

    def ask(self, session_id: str, question: str) -> str:
        if self.mode == "cloud":
            return ask_cloud(self._agentcore, self.resources.runtime_arn, session_id, question)
        return ask_local(question)


def make_backend() -> Backend:
    """The app calls this once per browser session; tests replace it."""
    return Backend()

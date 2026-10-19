"""The queue consumer (OQ-42 a): hands each queue message to the agent on
AgentCore Runtime, in cloud mode.

AgentCore Runtime is request-driven and can't read SQS itself, so this small
Lambda does: a `new_email` message becomes one InvokeAgentRuntime call with
the message's IDs and times, in a session of its own. Week 4 adds the daily
check, and takes Approve and Skip decisions straight to the approval
workflow, never to the model (APR-3). Until then any other type is dropped,
as the week 2 reader did: nothing produces one.

A failed call puts the message back on the queue (partial batch response);
after 3 receives it goes to the dead-letter queue (ING-3). Logs carry IDs
and counts only (NFR-PRIV-3).
"""

import json
import logging
import os
import re

from inbox_agent import settings
from inbox_agent.poll import NEW_EMAIL_MESSAGE

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RUNTIME_ARN_ENV = "AGENT_RUNTIME_ARN"
QUALIFIER = "DEFAULT"
# Runtime session IDs are at least 33 characters.
SESSION_PREFIX = "inbox-agent-email-"
SESSION_MIN_LENGTH = 33
_MESSAGE_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
FORWARDED = ("type", "message_id", "received_at", "detected_at")


class AgentError(RuntimeError):
    """The agent answered with an error. The text names the status only."""


def session_id(message_id: str) -> str:
    return (SESSION_PREFIX + message_id).ljust(SESSION_MIN_LENGTH, "-")


def invoke_agent(client, runtime_arn: str, payload: dict) -> dict:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier=QUALIFIER,
        runtimeSessionId=session_id(payload["message_id"]),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode(),
    )
    status = response.get("statusCode", 200)
    body = response["response"].read()
    if status >= 300:
        raise AgentError(f"the agent returned HTTP {status}")
    result = json.loads(body or b"{}")
    if not isinstance(result, dict):
        raise AgentError("the agent returned something other than an object")
    return result


def consume(records: list[dict], *, client, runtime_arn: str) -> dict:
    counts = {"handled": 0, "duplicates": 0, "alerts": 0, "skipped_types": 0, "failed": 0}
    failures, handled_ids, failed_ids = [], [], []
    for record in records:
        try:
            body = json.loads(record["body"])
        except (TypeError, ValueError):
            body = {}
        if (
            not isinstance(body, dict)
            or body.get("type") != NEW_EMAIL_MESSAGE
            or not isinstance(body.get("message_id"), str)
            or not _MESSAGE_ID.match(body["message_id"])
        ):
            counts["skipped_types"] += 1
            continue
        message_id = body["message_id"]
        payload = {key: body[key] for key in FORWARDED if isinstance(body.get(key), str)}
        try:
            outcome = invoke_agent(client, runtime_arn, payload)
        except Exception as error:
            # The type only: an exception message could quote the email.
            logger.error(json.dumps({"event": "consumer_failed", "message_id": message_id, "error": type(error).__name__}))
            counts["failed"] += 1
            failed_ids.append(message_id)
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        if outcome.get("duplicate"):
            counts["duplicates"] += 1
            continue
        counts["handled"] += 1
        counts["alerts"] += bool(outcome.get("alerted"))
        handled_ids.append(message_id)

    logger.info(json.dumps({"event": "consumer_done", **counts, "handled_ids": handled_ids, "failed_ids": failed_ids}))
    return {"batchItemFailures": failures}


def handler(event: dict, context: object) -> dict:
    import boto3
    from botocore.config import Config

    # One email takes seconds. A botocore retry would add a second run on
    # top of the queue's own retries, so there is none; the queue retries.
    client = boto3.client(
        "bedrock-agentcore", region_name=settings.AWS_REGION, config=Config(read_timeout=60, retries={"max_attempts": 1})
    )
    return consume(event.get("Records", []), client=client, runtime_arn=os.environ[RUNTIME_ARN_ENV])

"""Logging for every entry point (NFR-PRIV-3, OQ-45 g): log lines carry IDs,
counts, classes, kinds, timings, error types, tool names and policy IDs,
never email text, questions, answers or addresses.

- `log_event` writes one JSON line and keeps only the field names in
  ALLOWED_FIELDS; anything else is dropped and counted.
- `setup` sets third-party loggers to WARNING (`httpx` logs every Gateway
  request, `botocore` credential lines, `langchain_aws` each model call),
  and installs a record factory, so on every record from any logger:
  - an exception is kept as its type only, with no message or traceback
    (the AgentCore SDK logs a failed invocation's traceback);
  - a third-party record keeps only its fixed text, without its arguments,
    which can hold a request, a payload or an exception's message.
"""

import json
import logging
import sys

ALLOWED_FIELDS = frozenset(
    {
        # IDs and times
        "message_id",
        "approval_id",
        "approval_ids",
        "handled_ids",
        "failed_ids",
        "enqueued_ids",
        "started_approval_ids",
        "check_date",
        "received_at",
        "detected_at",
        "published_at",
        "seconds",
        # counts and flags
        "count",
        "found",
        "skipped",
        "enqueued",
        "query_values_skipped",
        "handled",
        "duplicates",
        "alerts",
        "checks",
        "drafts",
        "decisions",
        "skipped_types",
        "failed",
        "eligible",
        "rejected",
        "notified",
        "duplicate",
        "row_written",
        "row_created",
        "alerted",
        "created",
        "written",
        "published",
        "saved",
        "refused",
        "cases",
        # classes, kinds and outcomes
        "email_class",
        "outcome",
        "decisions_seen",
        # failures and denials
        "error",
        "tool",
        "policy_id",
        "policy_ids",
        "errors",
    }
)
DROPPED = "dropped_fields"
QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "mcp",
    "langchain",
    "langchain_core",
    "langchain_aws",
    "langchain_ollama",
    "langchain_mcp_adapters",
    "langgraph",
    "bedrock_agentcore",
    "aws_durable_execution_sdk_python",
    "uvicorn",
)
OWN_LOGGERS = ("root", "inbox_agent")


def log_event(logger: logging.Logger, event: str, /, level: int = logging.INFO, **fields) -> None:
    kept = {key: value for key, value in fields.items() if key in ALLOWED_FIELDS}
    if len(kept) < len(fields):
        kept[DROPPED] = len(fields) - len(kept)
    logger.log(level, json.dumps({"event": event, **kept}, default=str))


def _own(name: str) -> bool:
    return any(name == own or name.startswith(own + ".") for own in OWN_LOGGERS)


def _trimming(base):
    """A record factory over `base` (which may add its own fields, such as
    OpenTelemetry's trace IDs) that trims each record."""

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = base(*args, **kwargs)
        if not _own(record.name):
            record.msg, record.args = str(record.msg), None
        elif isinstance(record.args, tuple):
            record.args = tuple(type(a).__name__ if isinstance(a, BaseException) else a for a in record.args)
        if record.exc_info:
            kind = record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
            record.msg = f"{record.msg} [{kind}]"
            record.exc_info, record.exc_text = None, None
        record.stack_info = None
        return record

    factory.trims = True
    return factory


def setup(level: int = logging.INFO) -> None:
    """Idempotent; safe to call from every entry point."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, stream=sys.stderr)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    current = logging.getLogRecordFactory()
    if not getattr(current, "trims", False):
        logging.setLogRecordFactory(_trimming(current))

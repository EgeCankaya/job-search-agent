"""Log trimming (NFR-PRIV-3, OQ-45 g): the allow-list, the trimmed records,
and the canary test. A planted email whose subject, sender and body carry a
unique string goes through the batch path, the stale-check drafting and a
chat question over fakes, with every logger at DEBUG; the string appears in
no record. Companies and senders are fictional."""

import json
import logging

import pytest
from langchain_core.messages import AIMessage

from inbox_agent import logs, runtime
from inbox_agent.messages import EmailHeader, FetchedEmail
from tests.unit import test_batch_path as batch
from tests.unit import test_chat_path as chat
from tests.unit import test_stale_check as stale

CANARY = "kanarya7f3e9"


@pytest.fixture
def trimmed_logging():
    """setup() as an entry point calls it, undone after the test."""
    factory = logging.getLogRecordFactory()
    levels = {name: logging.getLogger(name).level for name in (*logs.QUIET_LOGGERS, "")}
    logs.setup()
    yield
    logging.setLogRecordFactory(factory)
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def everything_at_debug(trimmed_logging):
    loggers = [logging.getLogger(name) for name in list(logging.root.manager.loggerDict)] + [logging.getLogger()]
    before = [(lg, lg.level) for lg in loggers if isinstance(lg, logging.Logger)]
    for lg, _ in before:
        lg.setLevel(logging.DEBUG)
    yield
    for lg, level in before:
        lg.setLevel(level)


def every_line(caplog):
    """What a handler would write for each record: message and exception."""
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(formatter.format(record) for record in caplog.records)


# log_event


def test_log_event_keeps_allow_listed_fields_and_counts_the_rest(caplog):
    logger = logging.getLogger("inbox_agent.test")
    with caplog.at_level(logging.INFO, logger="inbox_agent.test"):
        logs.log_event(logger, "batch_done", message_id="18c0ffee", seconds=1.5, subject=f"Mülakat {CANARY}", sender=CANARY)
    [record] = caplog.records
    assert json.loads(record.getMessage()) == {"event": "batch_done", "message_id": "18c0ffee", "seconds": 1.5, "dropped_fields": 2}
    assert CANARY not in caplog.text


def test_log_event_logs_at_the_level_given(caplog):
    with caplog.at_level(logging.INFO, logger="inbox_agent.test"):
        logs.log_event(logging.getLogger("inbox_agent.test"), "tool_denied", logging.WARNING, tool="notify_owner", policy_id="NoRecipientKeys-1")
    assert caplog.records[0].levelno == logging.WARNING


def test_no_allowed_field_is_one_that_holds_text():
    assert not logs.ALLOWED_FIELDS & {"subject", "sender", "from", "to", "body", "text", "question", "answer", "company", "role", "email", "input", "arguments", "detail"}


# The record factory


def test_an_exception_is_logged_by_type_only(trimmed_logging, caplog):
    with caplog.at_level(logging.INFO):
        try:
            raise ValueError(f"could not parse {CANARY}")
        except ValueError:
            logging.getLogger("bedrock_agentcore.app").exception("Invocation failed (%.3fs)", 1.0)
            logging.getLogger("inbox_agent.runtime").exception("failed")
    text = every_line(caplog)
    assert CANARY not in text and "Traceback" not in text
    assert "Invocation failed (%.3fs) [ValueError]" in text
    assert "failed [ValueError]" in text


def test_a_third_party_record_keeps_only_its_fixed_text(trimmed_logging, caplog):
    with caplog.at_level(logging.WARNING):
        logging.getLogger("httpx").warning("HTTP Request: %s %s", "POST", f"https://example.com/?q={CANARY}")
        logging.getLogger("aws_durable_execution_sdk_python.execution").warning("payload: %r", {"text": CANARY})
    assert CANARY not in every_line(caplog)
    assert "HTTP Request: %s %s" in caplog.text


def test_our_own_records_keep_their_arguments_but_not_exceptions(trimmed_logging, caplog):
    with caplog.at_level(logging.INFO):
        logging.getLogger().info("budgets_created=%d", 3)
        logging.getLogger("inbox_agent.x").warning("saw %s", ValueError(CANARY))
    assert "budgets_created=3" in caplog.text and "saw ValueError" in caplog.text and CANARY not in caplog.text


def test_setup_quiets_the_libraries_and_is_idempotent(trimmed_logging):
    factory = logging.getLogRecordFactory()
    logs.setup()
    assert logging.getLogRecordFactory() is factory
    for name in ("httpx", "httpcore", "botocore", "urllib3", "mcp", "langchain_aws", "langchain_core", "langgraph"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING, name


# The canary


class CanaryGmail(batch.FakeGmail):
    def fetch(self, message_id):
        self.fetched.append(message_id)
        header = EmailHeader(
            message_id=message_id,
            sender=f"İK {CANARY} <ik.{CANARY}@kuzey.example.com>",
            subject=f"Mülakat daveti {CANARY}",
            received_at=batch.RECEIVED,
        )
        return FetchedEmail(header=header, text=f"{batch.BODY} {CANARY}", html=f"<p>{CANARY}</p>", invite=None)


class CanaryStaleGmail(stale.FakeGmail):
    def fetch(self, message_id):
        self.fetched.append(message_id)
        header = EmailHeader(message_id=message_id, sender=f"Ayşe <ayse.{CANARY}@kuzey.example.com>", subject=f"Re: {CANARY}", received_at=stale.NOW)
        return FetchedEmail(header=header, text=f"Başvurunuz değerlendiriliyor. {CANARY}", html=None, invite=None)


def test_the_canary_reaches_no_log_record(everything_at_debug, caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        # The batch path: the model even copies the canary into the company.
        env = batch.Env(gmail=CanaryGmail(), model=batch.FakeModel(batch.reply(company=f"Kuzey {CANARY}")))
        outcome = runtime.handle({"type": "new_email", "message_id": "18c0ffee"}, batch=env.graph)
        assert outcome["alerted"] is True
        # The stale-check drafting, with the canary in the company's latest email.
        gmail = CanaryStaleGmail(
            {"kuzey.example.com": [stale.meta(9, "no-reply@kuzey.example.com"), stale.meta(5, f"ayse.{CANARY}@kuzey.example.com")]}
        )
        checked = runtime.handle({"type": "stale_check"}, stale=stale.Env(gmail).deps, today=lambda: stale.TODAY)
        assert checked["drafts"]
        # A chat question that carries the canary, and an answer that echoes it.
        tools = chat_tools()
        model = chat.ScriptedChat(
            script=[chat.call("list_applications", {"company": "Brightpath"}), AIMessage(content=f"Nothing about {CANARY}.")], seen=[]
        )
        graph = chat.build_chat_graph(chat.ChatDeps(chat_model=model, tools=tools, today=lambda: chat.TODAY))
        assert CANARY in runtime.handle({"type": "chat", "question": f"What about {CANARY}?"}, chat=graph)["answer"]
        # A failure whose message quotes the email.
        with pytest.raises(ValueError):
            runtime.handle({"type": "new_email", "message_id": "18c0ffef"}, batch=batch_failing())
    assert caplog.records, "nothing was logged at all"
    assert CANARY not in every_line(caplog)
    captured = capsys.readouterr()
    assert CANARY not in captured.out and CANARY not in captured.err


def chat_tools():
    from inbox_agent.import_applications import import_rows, parse_csv
    from inbox_agent.tool_handlers import InProcessTools, ToolDeps
    from inbox_agent.tools import RestrictedTools
    from inbox_agent.tracker import TrackerTable
    from tests.fakes import FakeTable

    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(chat.TRACKER_CSV), TrackerTable(table))
    return RestrictedTools(InProcessTools(ToolDeps(tracker=TrackerTable(table), today=lambda: chat.TODAY)), ("list_applications",))


def batch_failing():
    class Failing:
        def invoke(self, value, config=None):
            raise ValueError(f"could not classify: {CANARY}")

    return Failing()


def test_every_entry_point_sets_up_logging():
    import inspect

    from inbox_agent import approval_workflow, consumer, local_mcp_server, poll, tool_lambda

    for entry in (poll.handler, consumer.handler, tool_lambda.handler, approval_workflow._handler, runtime.main, local_mcp_server.main):
        assert "setup()" in inspect.getsource(entry), entry.__qualname__


def test_no_module_logs_a_raw_json_line():
    # Every structured line goes through log_event and its allow-list.
    from pathlib import Path

    source = Path(logs.__file__).parent
    for path in source.glob("*.py"):
        assert "logger.info(json.dumps" not in path.read_text() and "logger.error(json.dumps" not in path.read_text(), path.name

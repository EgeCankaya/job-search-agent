"""ING-1 to ING-3 poll plumbing (UAC-ING-2), and the processed-email log TTL
(NFR-PRIV-4). Gmail and AWS are fakes; IDs and senders are fictional.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from inbox_agent import poll as poll_module
from inbox_agent.gmail import GmailMessageRef
from inbox_agent.poll import AgentQueue, handler, poll
from inbox_agent.processed_log import POLL_STATE_KEY, ProcessedLog
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeQueue, FakeTable

NOW = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
ROWS = [{"company_key": "kuzey yazilim", "role_key": "backend engineer", "company_domains": ["kuzey.example.com"]}]


def ref(message_id, minutes_ago=10):
    return GmailMessageRef(message_id=message_id, thread_id=f"t-{message_id}", received_at=NOW - timedelta(minutes=minutes_ago))


class FakeGmail:
    def __init__(self, results=(), error=None):
        self.results = list(results)
        self.error = error
        self.queries: list[str] = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.results)


class FailingQueue(FakeQueue):
    """Accepts `ok` messages, then fails."""

    def __init__(self, ok):
        super().__init__()
        self.ok = ok

    def send_message(self, QueueUrl, MessageBody):
        if len(self.messages) >= self.ok:
            raise ConnectionError("SQS unavailable")
        return super().send_message(QueueUrl, MessageBody)


@pytest.fixture
def env():
    tracker_table = FakeTable("company_key", "role_key")
    for row in ROWS:
        tracker_table.put_item(Item=row)
    return {
        "tracker_table": tracker_table,
        "log_table": FakeTable("message_id"),
        "queue": FakeQueue(),
    }


def run(env, gmail, now=NOW, queue=None):
    queue = queue or env["queue"]
    return poll(
        gmail=gmail,
        tracker=TrackerTable(env["tracker_table"]),
        log=ProcessedLog(env["log_table"]),
        queue=AgentQueue(queue, queue.queue_url),
        now=now,
    )


def test_new_messages_are_queued_and_logged(env):
    result = run(env, FakeGmail([ref("a1"), ref("b2", minutes_ago=50)]))
    assert result.enqueued_ids == ("a1", "b2")
    assert [m["message_id"] for m in env["queue"].messages] == ["a1", "b2"]
    log = ProcessedLog(env["log_table"])
    assert log.contains("a1") and log.contains("b2")


def test_queue_message_carries_ids_and_times_only(env):
    run(env, FakeGmail([ref("a1")]))
    [message] = env["queue"].messages
    assert message == {
        "type": "new_email",
        "message_id": "a1",
        "thread_id": "t-a1",
        "received_at": "2026-10-18T09:20:00+00:00",
        "detected_at": "2026-10-18T09:30:00+00:00",
    }


def test_repolling_a_processed_window_enqueues_nothing(env):
    gmail = FakeGmail([ref("a1"), ref("b2")])
    run(env, gmail)
    result = run(env, gmail, now=NOW + timedelta(minutes=30))
    assert result.enqueued_ids == ()
    assert (result.found, result.skipped) == (2, 2)
    assert len(env["queue"].messages) == 2


def test_duplicate_ids_in_one_search_are_queued_once(env):
    result = run(env, FakeGmail([ref("a1"), ref("a1")]))
    assert result.enqueued_ids == ("a1",)


def test_log_entry_expires_30_days_after_processing(env):
    run(env, FakeGmail([ref("a1")]))
    item = env["log_table"].get_item(Key={"message_id": "a1"})["Item"]
    assert item["logged_at"] == int(NOW.timestamp())
    assert item["expires_at"] - item["logged_at"] == 30 * 24 * 60 * 60


def test_every_search_uses_the_built_query_from_the_last_successful_poll(env):
    gmail = FakeGmail()
    run(env, gmail)  # first poll: assume one ran 30 minutes ago
    run(env, gmail, now=NOW + timedelta(minutes=30))
    first, second = gmail.queries
    assert f"after:{int((NOW - timedelta(minutes=90)).timestamp())}" in first
    assert f"after:{int((NOW - timedelta(minutes=60)).timestamp())}" in second
    for query in gmail.queries:
        assert "from:kuzey.example.com" in query
        assert query.endswith("-from:no-reply@sns.amazonaws.com")


def test_a_failed_search_keeps_the_last_successful_poll(env):
    run(env, FakeGmail())
    with pytest.raises(TimeoutError):
        run(env, FakeGmail(error=TimeoutError()), now=NOW + timedelta(minutes=30))
    gmail = FakeGmail()
    run(env, gmail, now=NOW + timedelta(minutes=60))
    # The window still starts at the poll before the failure, minus the overlap.
    assert f"after:{int((NOW - timedelta(hours=1)).timestamp())}" in gmail.queries[0]


def test_a_queue_failure_loses_nothing(env):
    gmail = FakeGmail([ref("a1"), ref("b2")])
    with pytest.raises(ConnectionError):
        run(env, gmail, queue=FailingQueue(ok=1))
    log = ProcessedLog(env["log_table"])
    assert log.contains("a1") and not log.contains("b2")
    assert log.last_successful_poll() is None

    result = run(env, gmail, now=NOW + timedelta(minutes=30))
    assert result.enqueued_ids == ("b2",)


def test_poll_state_item_has_no_ttl(env):
    run(env, FakeGmail())
    item = env["log_table"].get_item(Key={"message_id": POLL_STATE_KEY})["Item"]
    assert item == {"message_id": POLL_STATE_KEY, "last_success": int(NOW.timestamp())}


def test_logs_carry_counts_and_ids_only(env, caplog):
    with caplog.at_level(logging.INFO):
        run(env, FakeGmail([ref("a1")]))
    [record] = [r for r in caplog.records if "poll_done" in r.getMessage()]
    assert json.loads(record.getMessage()) == {
        "event": "poll_done",
        "found": 1,
        "skipped": 0,
        "enqueued": 1,
        "enqueued_ids": ["a1"],
        "query_values_skipped": 0,
    }
    # The query names companies and recruiters, so it is never logged.
    assert "kuzey" not in caplog.text


class FakeDynamoDB:
    def __init__(self, tables):
        self.tables = tables

    def Table(self, name):
        return self.tables[name]


def test_handler_wires_the_environment(env, monkeypatch):
    monkeypatch.setenv("TRACKER_TABLE_NAME", "Tracker")
    monkeypatch.setenv("PROCESSED_EMAILS_TABLE_NAME", "Processed")
    monkeypatch.setenv("AGENT_QUEUE_URL", env["queue"].queue_url)
    dynamodb = FakeDynamoDB({"Tracker": env["tracker_table"], "Processed": env["log_table"]})
    monkeypatch.setattr(poll_module, "_aws", lambda: (dynamodb, env["queue"]))
    monkeypatch.setattr(poll_module, "gmail_client", lambda: FakeGmail([ref("a1")]))

    assert handler({}, None) == {"found": 1, "skipped": 0, "enqueued": 1}
    assert env["queue"].messages[0]["message_id"] == "a1"


def test_dry_run_writes_nothing(env):
    gmail = FakeGmail([ref("a1"), ref("b2")])
    run(env, FakeGmail([ref("a1")]))  # a1 is already processed
    log_items_before = dict(env["log_table"].items)

    result = poll(
        gmail=gmail,
        tracker=TrackerTable(env["tracker_table"]),
        log=ProcessedLog(env["log_table"]),
        queue=AgentQueue(env["queue"], env["queue"].queue_url),
        now=NOW + timedelta(minutes=30),
        dry_run=True,
    )
    assert result.dry_run and result.enqueued_ids == ("b2",)
    assert (result.found, result.skipped) == (2, 1)
    assert [m["message_id"] for m in env["queue"].messages] == ["a1"]
    assert env["log_table"].items == log_items_before  # no log entry, no last-poll time


def test_dry_run_uses_the_same_query(env):
    gmail = FakeGmail()
    run(env, gmail)
    poll(
        gmail=gmail,
        tracker=TrackerTable(env["tracker_table"]),
        log=ProcessedLog(env["log_table"]),
        queue=AgentQueue(env["queue"], env["queue"].queue_url),
        now=NOW + timedelta(minutes=30),
        dry_run=True,
    )
    first, second = gmail.queries
    assert first == second.replace(str(int((NOW - timedelta(hours=1)).timestamp())), str(int((NOW - timedelta(minutes=90)).timestamp())))


@pytest.mark.parametrize(("event", "dry"), [({"dry_run": True}, True), ({"dry_run": "yes"}, False), ({}, False)])
def test_handler_dry_run_only_on_an_explicit_true(env, monkeypatch, event, dry):
    monkeypatch.setenv("TRACKER_TABLE_NAME", "Tracker")
    monkeypatch.setenv("PROCESSED_EMAILS_TABLE_NAME", "Processed")
    monkeypatch.setenv("AGENT_QUEUE_URL", env["queue"].queue_url)
    dynamodb = FakeDynamoDB({"Tracker": env["tracker_table"], "Processed": env["log_table"]})
    monkeypatch.setattr(poll_module, "_aws", lambda: (dynamodb, env["queue"]))
    monkeypatch.setattr(poll_module, "gmail_client", lambda: FakeGmail([ref("a1")]))

    response = handler(event, None)
    if dry:
        assert response == {"dry_run": True, "found": 1, "skipped": 0, "would_enqueue": 1}
        assert env["queue"].messages == []
    else:
        assert response == {"found": 1, "skipped": 0, "enqueued": 1}
        assert len(env["queue"].messages) == 1

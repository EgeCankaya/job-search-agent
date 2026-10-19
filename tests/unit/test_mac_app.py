"""The Mac app (DASH-1, CHT-1, CHT-2): the Dashboard numbers against direct
counts on a seeded table, read-only access, stack discovery, the Ask calls,
the loopback-only config, and both pages rendered with a fake backend.
Companies are fictional."""

import io
import json
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from inbox_agent import app_backend
from inbox_agent.app_backend import AskError, ask_cloud, ask_local, discover, load_dashboard
from inbox_agent.dashboard import build_dashboard, week_start
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.processed_log import ProcessedLog, alert_claims
from inbox_agent.tracker import STATUSES, TrackerTable
from tests.fakes import FakeTable

ROOT = Path(__file__).resolve().parents[2]
# Monday 19 October 2026, 15:00 in Istanbul.
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

TRACKER_CSV = """company,role,applied_date,status
Brightpath,Software Engineer,2026-10-12,interviewing
Brightpath,Test Engineer,2026-09-01,rejected
Kuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied
Güney Savunma,Platform Engineer,2026-10-15,assessment
Doğu Bulut,SRE,2026-09-20,offer
Batı Robotik,ML Engineer,2026-08-01,withdrawn
"""


class ReadOnlyTable(FakeTable):
    """Any write fails the test: the Dashboard only reads (DASH-1)."""

    def put_item(self, *args, **kwargs):
        raise AssertionError("the Dashboard wrote to a table")


def seeded():
    tracker = FakeTable("company_key", "role_key")
    import_rows(parse_csv(TRACKER_CSV), TrackerTable(tracker))
    for item in tracker.items.values():
        if item["company"] == "Brightpath" and item.get("role") == "Software Engineer":
            item["key_date"] = "2026-10-22T14:00:00+03:00"
            item["key_date_type"] = "interview"
        if item["company"] == "Güney Savunma":
            item["key_date"] = "2026-10-30"  # past the 7 days
            item["key_date_type"] = "assessment_deadline"
    log = FakeTable("message_id")
    processed = ProcessedLog(log)
    for message_id, at, kind in [
        ("a1", datetime(2026, 10, 19, 6, 0, tzinfo=UTC), "urgent"),
        ("a2", datetime(2026, 10, 18, 22, 0, tzinfo=UTC), "possible"),  # Monday 01:00 in Istanbul
        ("a3", datetime(2026, 10, 18, 20, 0, tzinfo=UTC), "urgent"),  # Sunday 23:00: last week
        ("a4", datetime(2026, 10, 19, 7, 0, tzinfo=UTC), None),  # before kinds were recorded
    ]:
        processed.record(message_id, at)
        processed.claim_alert(message_id, at, kind)
    processed.record("a5", NOW)  # logged, never alerted
    read_only_tracker, read_only_log = ReadOnlyTable("company_key", "role_key"), ReadOnlyTable("message_id")
    read_only_tracker.items, read_only_log.items = tracker.items, log.items
    return read_only_tracker, read_only_log


def test_every_dashboard_number_matches_a_direct_count():
    tracker, log = seeded()
    data = load_dashboard(tracker, log, now=NOW)
    rows = [i for i in tracker.items.values() if not i["company_key"].startswith("#")]

    assert data.total == len(rows) == 6
    assert data.open == sum(1 for r in rows if r["status"] not in ("rejected", "withdrawn")) == 4
    assert data.by_status == {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    assert sum(data.by_status.values()) == data.total

    assert [(s.company, s.days) for s in data.silent] == [("Doğu Bulut", 29), ("Kuzey Yazılım A.Ş.", 18)]
    assert [(u.company, u.when, u.kind) for u in data.upcoming] == [("Brightpath", date(2026, 10, 22), "interview")]
    assert data.alerts_this_week == {"urgent": 1, "possible": 1, "unrecorded": 1}


def test_the_week_starts_monday_in_istanbul():
    assert week_start(NOW) == datetime(2026, 10, 19, 0, 0, tzinfo=week_start(NOW).tzinfo)
    assert week_start(NOW).utcoffset().total_seconds() == 3 * 3600


def test_a_date_time_key_date_counts_on_its_istanbul_day():
    rows = [
        # 01:30 on 27 Oct in Istanbul: past the 7 days, though its UTC date isn't.
        {"company": "Kuzey", "status": "interviewing", "key_date": "2026-10-26T22:30:00+00:00"},
        # 01:30 on 19 Oct in Istanbul: today, though its UTC date was yesterday.
        {"company": "Güney", "status": "assessment", "key_date": "2026-10-18T22:30:00+00:00"},
    ]
    [upcoming] = build_dashboard(rows, [], now=NOW).upcoming
    assert (upcoming.company, upcoming.when) == ("Güney", date(2026, 10, 19))


def test_alert_claims_reads_only_the_window():
    _, log = seeded()
    kinds = sorted(str(kind) for _, kind in alert_claims(log, datetime(2026, 10, 19, 0, 0, tzinfo=UTC)))
    assert kinds == ["None", "urgent"]


def test_a_claim_with_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        ProcessedLog(FakeTable("message_id")).claim_alert("a1", NOW, "loud")


def test_a_released_claim_drops_its_kind():
    log = ProcessedLog(table := FakeTable("message_id"))
    log.claim_alert("a1", NOW, "urgent")
    log.release_alert("a1", NOW)
    assert "alert_kind" not in table.items[("a1",)]
    assert alert_claims(table, NOW) == []


# Discovery and Ask


class FakeCloudFormation:
    RESOURCES = {
        "InboxAgentAlwaysFree": [
            ("ApplicationsTable9F7E3B91", "AWS::DynamoDB::Table", "tracker-table"),
            ("ProcessedEmailsTable5DBDE874", "AWS::DynamoDB::Table", "log-table"),
            ("PendingDraftsTableABC", "AWS::DynamoDB::Table", "drafts-table"),
        ],
        "InboxAgentAgentCore": [("Runtime", "AWS::BedrockAgentCore::Runtime", "inbox_agent-abc")],
    }

    def get_paginator(self, name):
        assert name == "list_stack_resources"
        resources = self.RESOURCES

        class Paginator:
            def paginate(self, StackName):
                yield {
                    "StackResourceSummaries": [
                        {"LogicalResourceId": l, "ResourceType": t, "PhysicalResourceId": p} for l, t, p in resources[StackName]
                    ]
                }

        return Paginator()


class FakeControl:
    def get_agent_runtime(self, agentRuntimeId):
        assert agentRuntimeId == "inbox_agent-abc"
        return {"agentRuntimeArn": "arn:aws:bedrock-agentcore:eu-north-1:111122223333:runtime/inbox_agent-abc"}


def test_discovery_finds_the_tables_and_the_runtime():
    resources = discover(FakeCloudFormation(), FakeControl())
    assert (resources.tracker_table, resources.processed_table) == ("tracker-table", "log-table")
    assert resources.runtime_arn.endswith("runtime/inbox_agent-abc")
    assert discover(FakeCloudFormation(), mode="local").runtime_arn is None


class FakeAgentCore:
    def __init__(self, body, status=200):
        self.body, self.status = body, status
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"statusCode": self.status, "response": io.BytesIO(self.body)}


def test_ask_in_cloud_mode_sends_a_chat_payload():
    client = FakeAgentCore(b'{"answer": "Software Engineer: interviewing."}')
    session = app_backend.new_session_id()
    assert len(session) >= 33
    assert ask_cloud(client, "arn:runtime", session, "Brightpath?") == "Software Engineer: interviewing."
    [call] = client.calls
    assert json.loads(call["payload"]) == {"type": "chat", "question": "Brightpath?"}
    assert call["runtimeSessionId"] == session


@pytest.mark.parametrize(("body", "status"), [(b'{"answer": "x"}', 500), (b"not json", 200), (b'{"error": "x"}', 200)])
def test_ask_failures_raise_without_the_question(body, status):
    with pytest.raises(AskError) as error:
        ask_cloud(FakeAgentCore(body, status), "arn:runtime", "s" * 40, "Brightpath secret?")
    assert "Brightpath" not in str(error.value)


def test_ask_in_local_mode_posts_to_the_loopback_worker():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return io.BytesIO(b'{"answer": "Two."}')

    assert ask_local("How many?", opener=opener) == "Two."
    assert seen[0].full_url == "http://127.0.0.1:8080/invocations"
    assert json.loads(seen[0].data) == {"type": "chat", "question": "How many?"}


def test_ask_local_refuses_any_other_host_and_explains_a_missing_worker():
    with pytest.raises(AskError, match="127.0.0.1"):
        ask_local("x", url="http://192.168.1.10:8080/invocations")

    def refused(request, timeout):
        raise ConnectionRefusedError()

    with pytest.raises(AskError, match="AGENT_MODE=local"):
        ask_local("x", opener=refused)


# The app


def test_the_app_listens_on_loopback_only_and_sends_no_usage_stats():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())
    assert config["server"]["address"] == "127.0.0.1"
    assert config["browser"]["gatherUsageStats"] is False


class FakeBackend:
    mode = "cloud"

    def __init__(self):
        tracker, log = seeded()
        self.data = load_dashboard(tracker, log, now=NOW)
        self.questions = []

    def dashboard(self):
        return self.data

    def ask(self, session_id, question):
        self.questions.append((session_id, question))
        return "Software Engineer: interviewing."


@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    fake = FakeBackend()
    monkeypatch.setattr(app_backend, "make_backend", lambda: fake)
    test = AppTest.from_file(str(ROOT / "mac_app" / "app.py"), default_timeout=30)
    test.fake = fake
    return test


def test_the_dashboard_page_renders_the_numbers(app):
    app.run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {"Applications": "6", "Open": "4", "Alerts this week": "1", '"Possible:" alerts this week': "1"}
    assert len(app.dataframe) == 3  # by status, silent, next 7 days


def test_the_ask_page_answers_a_question(app):
    app.run()
    app.switch_page("ask_page.py")
    app.run()
    app.chat_input[0].set_value("What is still open with Brightpath?").run()
    assert not app.exception
    assert [q for _, q in app.fake.questions] == ["What is still open with Brightpath?"]
    assert "Software Engineer: interviewing." in [m.markdown[0].value for m in app.chat_message if m.markdown]


def test_a_failing_backend_shows_the_login_hint(monkeypatch):
    from streamlit.testing.v1 import AppTest

    class Broken(FakeBackend):
        def dashboard(self):
            raise RuntimeError("ExpiredToken")

    monkeypatch.setattr(app_backend, "make_backend", Broken)
    test = AppTest.from_file(str(ROOT / "mac_app" / "app.py"), default_timeout=30)
    test.run()
    assert "aws login" in test.error[0].value
    assert "ExpiredToken" not in test.error[0].value

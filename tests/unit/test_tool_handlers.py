"""The four week 3 tools (TOOL-1, TRK-1, TRK-2, ALR-1, ALR-3, CLS-3), and the
Gateway Lambda wrapper. Gmail, DynamoDB and SNS are fakes; companies and
senders are fictional."""

import json
import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from inbox_agent import tool_lambda
from inbox_agent.alerts import AlertTopic
from inbox_agent.calendar_invite import CalendarInvite
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.messages import EmailHeader, FetchedEmail
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tools import ToolInputError, ToolNotAllowed
from inbox_agent.tracker import RowChanged, TrackerTable
from tests.fakes import FakeTable, FakeTopic

TODAY = date(2026, 10, 19)
RECEIVED = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)
SUBJECT = "Mülakat daveti: Backend Engineer"
BODY = "Merhaba,\n\n3 Kasım 14:00 için teknik mülakata davet ediyoruz.\n"

TRACKER_CSV = """company,role,applied_date,status
Kuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied
Kuzey Yazılım A.Ş.,Data Engineer,2026-09-10,rejected
Güney Savunma,Platform Engineer,2026-10-15,interviewing
Doğu Bulut Ltd. Şti.,SRE,2026-09-25,offer
"""


class FakeGmail:
    def __init__(self, text=BODY, html=None, invite=None):
        self.text, self.html, self.invite = text, html, invite
        self.fetched = []

    def fetch(self, message_id):
        self.fetched.append(message_id)
        header = EmailHeader(message_id=message_id, sender="İK <ik@kuzey.example.com>", subject=SUBJECT, received_at=RECEIVED)
        return FetchedEmail(header=header, text=self.text, html=self.html, invite=self.invite)


@pytest.fixture
def table():
    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(TRACKER_CSV), TrackerTable(table))
    table.puts = 0
    return table


@pytest.fixture
def topic():
    return FakeTopic()


@pytest.fixture
def tools(table, topic):
    deps = ToolDeps(
        tracker=TrackerTable(table),
        gmail=FakeGmail(),
        topic=AlertTopic(topic, topic.topic_arn),
        today=lambda: TODAY,
    )
    return InProcessTools(deps)


def rows(table):
    return {(item["company_key"], item["role_key"]): item for item in table.items.values() if not item["company_key"].startswith("#")}


# read_email


def test_read_email_returns_the_trimmed_email():
    gmail = FakeGmail(text=BODY + "x" * 5000)
    result = InProcessTools(ToolDeps(gmail=gmail)).call("read_email", {"message_id": "18f2a"})
    assert gmail.fetched == ["18f2a"]
    assert result["subject"] == SUBJECT
    assert result["sender"] == "İK <ik@kuzey.example.com>"
    assert result["received_at"] == RECEIVED.isoformat()
    assert result["text"].startswith("Merhaba")
    assert len(result["text"]) <= 2000  # NFR-PRIV-2
    assert result["calendar_start"] is None


def test_read_email_reads_html_and_the_calendar_start():
    start = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
    gmail = FakeGmail(text=None, html="<p>Davet</p><script>x()</script>", invite=CalendarInvite(start=start, title="Mülakat"))
    result = InProcessTools(ToolDeps(gmail=gmail)).call("read_email", {"message_id": "18f2b"})
    assert result["text"] == "Davet"
    assert result["calendar_start"] == start.isoformat()


# upsert_application: updates from an email


def test_an_invite_moves_the_matched_row_and_records_the_message(tools, table):
    result = tools.call(
        "upsert_application",
        {
            "company": "KUZEY YAZILIM",
            "role": "Backend Engineer",
            "status": "interviewing",
            "last_contact": "2026-10-19",
            "message_id": "18f2a",
            "key_date": "2026-11-03T14:00:00+03:00",
            "key_date_type": "interview",
        },
    )
    assert result == {"company": "Kuzey Yazılım A.Ş.", "role": "Backend Engineer", "status": "interviewing", "created": False, "written": True}
    row = rows(table)[("kuzey yazilim", "backend engineer")]
    assert row["last_contact"] == "2026-10-19"
    assert row["source_message_ids"] == ["18f2a"]
    assert row["key_date"] == "2026-11-03T14:00:00+03:00"
    assert row["key_date_type"] == "interview"


def test_the_same_email_twice_writes_once(tools, table):
    arguments = {"company": "Kuzey Yazılım", "role": "Backend Engineer", "status": "interviewing", "last_contact": "2026-10-19", "message_id": "18f2a"}
    tools.call("upsert_application", arguments)
    second = tools.call("upsert_application", arguments)
    assert second["written"] is False
    assert table.puts == 1


def test_an_email_never_moves_a_final_row_back(tools, table):
    result = tools.call(
        "upsert_application",
        {
            "company": "Kuzey Yazılım",
            "role": "Data Engineer",
            "status": "assessment",
            "last_contact": "2026-10-19",
            "message_id": "18f2c",
            "key_date": "2026-10-25",
            "key_date_type": "assessment_deadline",
        },
    )
    assert result["status"] == "rejected"
    row = rows(table)[("kuzey yazilim", "data engineer")]
    assert "key_date" not in row  # the status didn't take effect, so no deadline
    assert row["last_contact"] == "2026-10-19"


def test_outreach_for_an_unknown_company_creates_one_recruiter_contact_row(tools, table):
    result = tools.call(
        "upsert_application",
        {"company": "Batı Robotik", "status": "recruiter_contact", "last_contact": "2026-10-19", "message_id": "18f2d"},
    )
    assert result["created"] is True and result["status"] == "recruiter_contact"
    assert len(rows(table)) == 5


def test_outreach_does_not_reset_an_existing_row(tools):
    result = tools.call(
        "upsert_application",
        {"company": "Güney Savunma", "role": "Platform Engineer", "status": "recruiter_contact", "last_contact": "2026-10-19", "message_id": "18f2e"},
    )
    assert result["status"] == "interviewing"


def test_an_other_email_only_moves_last_contact(tools, table):
    tools.call("upsert_application", {"company": "Güney Savunma", "last_contact": "2026-10-19", "message_id": "18f2f"})
    row = rows(table)[("guney savunma", "platform engineer")]
    assert row["status"] == "interviewing"
    assert row["last_contact"] == "2026-10-19"


def test_an_email_update_needs_its_date(tools, table):
    with pytest.raises(ToolInputError, match="last_contact"):
        tools.call("upsert_application", {"company": "Güney Savunma", "message_id": "18f30"})
    assert table.puts == 0


class ChangingTable(FakeTable):
    """The approval workflow updates a row's follow-up state between the
    upsert's read and its write, `times` times."""

    def __init__(self, times):
        super().__init__("company_key", "role_key")
        self.times = times

    def scan(self, ExclusiveStartKey=None):
        page = super().scan(ExclusiveStartKey)
        if self.times and "LastEvaluatedKey" not in page:
            self.times -= 1
            for item in self.items.values():
                if item.get("company") == "Güney Savunma":
                    version = item.get("follow_up", {}).get("version", 0) + 1
                    item["follow_up"] = {"last_proposed": "2026-10-19", "version": version}
        return page


def changing_tools(times):
    table = ChangingTable(times)
    import_rows(parse_csv(TRACKER_CSV), TrackerTable(table))
    return table, InProcessTools(ToolDeps(tracker=TrackerTable(table), today=lambda: TODAY))


def test_an_upsert_never_undoes_a_follow_up_change_made_after_its_read():
    table, tools = changing_tools(times=2)
    tools.call(
        "upsert_application",
        {"company": "Güney Savunma", "status": "offer", "last_contact": "2026-10-19", "message_id": "18f3a"},
    )
    [row] = [item for item in table.items.values() if item.get("company") == "Güney Savunma"]
    assert row["status"] == "offer"
    assert row["follow_up"] == {"last_proposed": "2026-10-19", "version": 2}


def test_an_upsert_gives_up_when_the_row_keeps_changing():
    table, tools = changing_tools(times=5)
    with pytest.raises(RowChanged):
        tools.call("upsert_application", {"company": "Güney Savunma", "last_contact": "2026-10-19", "message_id": "18f3b"})
    [row] = [item for item in table.items.values() if item.get("company") == "Güney Savunma"]
    assert "18f3b" not in row.get("source_message_ids", [])


def test_a_new_row_is_not_written_over_one_created_since_the_read():
    table = FakeTable("company_key", "role_key")
    tracker = TrackerTable(table)
    tracker.put({"company_key": "bati", "role_key": "-", "company": "Batı", "status": "applied"})
    with pytest.raises(RowChanged):
        tracker.put_row({"company_key": "bati", "role_key": "-", "company": "Batı", "status": "offer"}, read=None)
    assert table.items[("bati", "-")]["status"] == "applied"


@pytest.mark.parametrize(
    "arguments",
    [
        {"company": "...", "last_contact": "2026-10-19", "message_id": "18f31"},
        {"company": "Güney Savunma", "key_date": "2026-11-01"},
    ],
)
def test_refused_upserts_write_nothing(tools, table, arguments):
    with pytest.raises(ToolInputError):
        tools.call("upsert_application", arguments)
    assert table.puts == 0


# upsert_application: a change I ask for


def test_a_manual_change_may_move_any_status_and_sets_the_next_step(tools, table):
    result = tools.call(
        "upsert_application",
        {"company": "Doğu Bulut", "role": "SRE", "status": "withdrawn", "next_step": "  told them\nno  "},
    )
    assert result["status"] == "withdrawn" and result["written"] is True
    row = rows(table)[("dogu bulut", "sre")]
    assert row["next_step"] == "told them no"
    assert row["last_contact"] == "2026-09-25"  # unchanged


# list_applications


def companies(result):
    return [(a["company"], a.get("role")) for a in result["applications"]]


def test_open_rows(tools):
    result = tools.call("list_applications", {"status": "open"})
    assert companies(result) == [
        ("Doğu Bulut Ltd. Şti.", "SRE"),
        ("Güney Savunma", "Platform Engineer"),
        ("Kuzey Yazılım A.Ş.", "Backend Engineer"),
    ]
    assert result["count"] == 3


def test_one_status(tools):
    assert companies(tools.call("list_applications", {"status": "rejected"})) == [("Kuzey Yazılım A.Ş.", "Data Engineer")]


def test_company_matches_after_normalization(tools):
    assert [r for _, r in companies(tools.call("list_applications", {"company": "kuzey yazilim as"}))] == [
        "Backend Engineer",
        "Data Engineer",
    ]


def test_silent_for_ten_days_or_more(tools):
    result = tools.call("list_applications", {"min_days_since_contact": 10})
    assert companies(result) == [
        ("Doğu Bulut Ltd. Şti.", "SRE"),
        ("Kuzey Yazılım A.Ş.", "Backend Engineer"),
        ("Kuzey Yazılım A.Ş.", "Data Engineer"),
    ]
    days = {a["role"]: a["days_since_contact"] for a in result["applications"]}
    assert days == {"SRE": 24, "Data Engineer": 39, "Backend Engineer": 18}


def test_filters_combine(tools):
    result = tools.call("list_applications", {"status": "open", "company": "Kuzey Yazılım", "min_days_since_contact": 10})
    assert companies(result) == [("Kuzey Yazılım A.Ş.", "Backend Engineer")]


def test_listed_rows_leave_out_ids_and_the_alias_item(tools, table):
    TrackerTable(table).put_aliases({"Kuzey": "Kuzey Yazılım"})
    tools.call("upsert_application", {"company": "Kuzey", "role": "Backend Engineer", "last_contact": "2026-10-19", "message_id": "18f2a"})
    result = tools.call("list_applications", {})
    assert result["count"] == 4
    for entry in result["applications"]:
        assert "source_message_ids" not in entry
        assert "company_key" not in entry and "role_key" not in entry


# notify_owner


def test_an_urgent_alert_is_published_once_with_only_its_fields(tools, topic):
    assert tools.call(
        "notify_owner",
        {"kind": "urgent", "email_class": "interview_invite", "company": "Kuzey Yazılım", "role": "Backend Engineer", "date": "2026-11-03T14:00:00+03:00"},
    ) == {"published": True}
    assert topic.published == [
        {
            "Subject": "Interview invite: Kuzey Yazilim, Backend Engineer",
            "Message": "Class: Interview invite\nCompany: Kuzey Yazılım\nRole: Backend Engineer\nDate: 3 Nov 2026 14:00 UTC+03:00",
        }
    ]


def test_a_possible_alert_is_prefixed(tools, topic):
    tools.call("notify_owner", {"kind": "possible", "email_class": "other", "company": "Güney Savunma"})
    assert topic.published[0]["Subject"] == "Possible: Other: Guney Savunma"


@pytest.mark.parametrize(("kind", "email_class"), [("urgent", "rejection"), ("possible", "offer")])
def test_kind_must_fit_the_class(tools, topic, kind, email_class):
    with pytest.raises(ToolInputError):
        tools.call("notify_owner", {"kind": kind, "email_class": email_class, "company": "Kuzey"})
    assert topic.published == []


def test_a_recipient_never_reaches_the_topic(tools, topic):
    with pytest.raises(ToolInputError):
        tools.call("notify_owner", {"kind": "urgent", "email_class": "offer", "company": "Kuzey", "to": "someone@example.com"})
    assert topic.published == []


# The tool set


def test_the_chat_path_set_has_only_list_applications(tools, topic):
    chat = tools.only("list_applications")
    assert [spec.name for spec in chat.specs()] == ["list_applications"]
    with pytest.raises(ToolNotAllowed):
        chat.call("notify_owner", {"kind": "urgent", "email_class": "offer", "company": "Kuzey"})
    with pytest.raises(ToolNotAllowed):
        chat.only("list_applications", "upsert_application")
    assert topic.published == []


# The Gateway Lambda wrapper


def gateway_context(tool):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"tracker___{tool}"}))


def test_the_lambda_serves_its_own_tool(table):
    deps = ToolDeps(tracker=TrackerTable(table), today=lambda: TODAY)
    result = tool_lambda.handle({"status": "open"}, gateway_context("list_applications"), deps=deps, name="list_applications")
    assert result["count"] == 3


@pytest.mark.parametrize("context", [gateway_context("notify_owner"), SimpleNamespace(client_context=None), None])
def test_the_lambda_refuses_any_other_tool(table, context):
    deps = ToolDeps(tracker=TrackerTable(table), today=lambda: TODAY)
    with pytest.raises(ToolNotAllowed):
        tool_lambda.handle({}, context, deps=deps, name="list_applications")


def test_lambda_logs_carry_no_email_text(caplog):
    deps = ToolDeps(gmail=FakeGmail())
    with caplog.at_level(logging.INFO):
        tool_lambda.handle({"message_id": "18f2a"}, gateway_context("read_email"), deps=deps, name="read_email")
        with pytest.raises(ToolInputError):
            tool_lambda.handle({"message_id": "18f2a", "subject": SUBJECT}, gateway_context("read_email"), deps=deps, name="read_email")
    logged = caplog.text
    assert "18f2a" in logged
    assert "Mülakat" not in logged and "Merhaba" not in logged and "kuzey.example.com" not in logged
    events = [json.loads(r.getMessage())["event"] for r in caplog.records]
    assert events == ["tool_done", "tool_failed"]


def test_the_lambda_picks_its_tool_from_the_environment(monkeypatch, table):
    monkeypatch.setenv("TOOL_NAME", "list_applications")
    monkeypatch.setattr(tool_lambda, "_deps", lambda name: ToolDeps(tracker=TrackerTable(table), today=lambda: TODAY))
    assert tool_lambda.handler({}, gateway_context("list_applications"))["count"] == 4

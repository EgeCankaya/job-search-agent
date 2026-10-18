"""The temporary queue reader (OQ-41 e): ING-2 dedup, TRK-1 update, ALR-1
alert. Gmail, the model and AWS are fakes; companies and senders are fictional.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from inbox_agent import reader as reader_module
from inbox_agent.calendar_invite import CalendarInvite
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.messages import EmailHeader, FetchedEmail
from inbox_agent.processed_log import ProcessedLog
from inbox_agent.reader import AlertTopic, handle_email, handler, read_batch
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable, FakeTopic

NOW = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 10, 18, 9, 40, tzinfo=UTC)
SUBJECT = "Mülakat daveti: Backend Engineer"
BODY = "Merhaba, 3 Kasım 14:00 için teknik mülakata davet ediyoruz."

IMPORTED = """company,role,applied_date,status,company_domain
Kuzey Yazılım A.Ş.,Backend Engineer,2026-09-20,applied,kuzey.example.com
"""


def reply(classes=("interview_invite",), asks="yes", company="Kuzey Yazılım", role="Backend Engineer", date="2026-11-03T14:00+03:00"):
    return json.dumps({"classes": list(classes), "asks_by_date": asks, "company": company, "role": role, "date": date})


class FakeModel:
    def __init__(self, text=None):
        self.text = text or reply()
        self.calls = 0

    def complete(self, system, prompt):
        self.calls += 1
        return self.text


class FakeGmail:
    def __init__(self, invite=None):
        self.invite = invite
        self.fetched: list[str] = []

    def fetch(self, message_id):
        self.fetched.append(message_id)
        header = EmailHeader(message_id=message_id, sender="İK <ik@kuzey.example.com>", subject=SUBJECT, received_at=RECEIVED)
        return FetchedEmail(header=header, text=BODY, html=None, invite=self.invite)


@pytest.fixture
def env():
    tracker_table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(IMPORTED), TrackerTable(tracker_table))
    tracker_table.puts = 0
    return {
        "tracker_table": tracker_table,
        "log_table": FakeTable("message_id"),
        "topic": FakeTopic(),
        "gmail": FakeGmail(),
        "model": FakeModel(),
    }


def deps(env, **overrides):
    values = {
        "gmail": env["gmail"],
        "model": env["model"],
        "tracker": TrackerTable(env["tracker_table"]),
        "log": ProcessedLog(env["log_table"]),
        "topic": AlertTopic(env["topic"], env["topic"].topic_arn),
        "now": NOW,
    }
    return {**values, **overrides}


def record(message_id="18c0ffee", type_="new_email", sqs_id="sqs-1"):
    body = {"type": type_, "message_id": message_id, "thread_id": f"t-{message_id}",
            "received_at": RECEIVED.isoformat(), "detected_at": NOW.isoformat()}
    return {"messageId": sqs_id, "body": json.dumps(body)}


def rows(env):
    return TrackerTable(env["tracker_table"]).rows()


# The happy path: TRK-1 and ALR-1


def test_a_reply_to_an_imported_application_lands_on_its_row(env):
    # Week 2 exit: "a reply to an imported application lands on its row".
    assert read_batch([record()], **deps(env)) == {"batchItemFailures": []}
    [row] = rows(env)
    assert row["company"] == "Kuzey Yazılım A.Ş."  # the imported row, not a new one
    assert row["status"] == "interviewing"
    assert row["last_contact"] == "2026-10-18"
    assert row["source_message_ids"] == ["18c0ffee"]
    assert row["key_date"] == "2026-11-03T14:00:00+03:00"
    assert row["key_date_type"] == "interview"


def test_an_invite_sends_one_alert_naming_the_row(env):
    read_batch([record()], **deps(env))
    [alert] = env["topic"].published
    assert alert["Subject"] == "Interview invite: Kuzey Yazilim A.S., Backend Engineer"
    assert alert["Message"] == (
        "Class: Interview invite\nCompany: Kuzey Yazılım A.Ş.\nRole: Backend Engineer\nDate: 3 Nov 2026 14:00 UTC+03:00"
    )


def test_a_calendar_invite_start_wins_over_the_extracted_date(env):
    start = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
    env["gmail"] = FakeGmail(invite=CalendarInvite(start=start, title="Teknik mülakat"))
    read_batch([record()], **deps(env))
    assert rows(env)[0]["key_date"] == start.isoformat()


def test_a_silent_class_answered_no_is_filed_without_an_alert(env):
    env["model"] = FakeModel(reply(classes=("rejection",), asks="no", date=None))
    read_batch([record()], **deps(env))
    assert env["topic"].published == []
    assert rows(env)[0]["status"] == "rejected"


def test_a_silent_class_answered_yes_is_a_possible_alert(env):
    env["model"] = FakeModel(reply(classes=("other",), asks="yes", date=None))
    read_batch([record()], **deps(env))
    [alert] = env["topic"].published
    assert alert["Subject"].startswith("Possible: Other: ")


def test_an_email_naming_no_company_alerts_without_a_row(env):
    env["model"] = FakeModel(reply(company=None, role=None))
    read_batch([record()], **deps(env))
    assert env["tracker_table"].puts == 0
    [alert] = env["topic"].published
    assert "Company: Company not named" in alert["Message"]


@pytest.mark.parametrize("company", ["!!!", "-"])
def test_a_company_with_nothing_left_after_normalization_is_treated_as_unnamed(env, company):
    env["model"] = FakeModel(reply(company=company))
    assert read_batch([record()], **deps(env)) == {"batchItemFailures": []}
    assert env["tracker_table"].puts == 0


def test_the_alias_item_is_applied(env):
    TrackerTable(env["tracker_table"]).put_aliases({"KYZ": "Kuzey Yazılım"})
    env["tracker_table"].puts = 0
    env["model"] = FakeModel(reply(company="KYZ"))
    read_batch([record()], **deps(env))
    assert len(rows(env)) == 1
    assert rows(env)[0]["source_message_ids"] == ["18c0ffee"]


# ING-2: the same queue message twice gives 1 tracker update and at most 1 alert


def test_the_same_message_delivered_twice_updates_once_and_alerts_once(env):
    read_batch([record(sqs_id="sqs-1")], **deps(env))
    read_batch([record(sqs_id="sqs-2")], **deps(env))
    assert env["tracker_table"].puts == 1
    assert len(env["topic"].published) == 1
    assert env["gmail"].fetched == ["18c0ffee"]  # the second delivery stops early
    assert env["model"].calls == 1


def test_the_same_message_twice_in_one_batch(env):
    read_batch([record(sqs_id="sqs-1"), record(sqs_id="sqs-2")], **deps(env))
    assert env["tracker_table"].puts == 1
    assert len(env["topic"].published) == 1


def test_a_failed_publish_is_retried_without_a_second_row_update(env):
    env["topic"] = FakeTopic(failures=1)
    first = read_batch([record(sqs_id="sqs-1")], **deps(env))
    assert first == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert env["topic"].published == []

    second = read_batch([record(sqs_id="sqs-1")], **deps(env))
    assert second == {"batchItemFailures": []}
    assert env["tracker_table"].puts == 1  # the row already lists the message
    assert len(env["topic"].published) == 1


def test_an_alert_claim_is_taken_once(env):
    log = ProcessedLog(env["log_table"])
    assert log.claim_alert("18c0ffee", NOW) is True
    assert log.claim_alert("18c0ffee", NOW) is False


def test_a_claim_loses_to_a_concurrent_claim(env):
    # Both readers saw no claim; the conditional write lets only the first through.
    log = ProcessedLog(env["log_table"])
    stale = log._item("18c0ffee", NOW)
    assert log.claim_alert("18c0ffee", NOW)
    log._item = lambda message_id, now: dict(stale)
    assert log.claim_alert("18c0ffee", NOW) is False


def test_a_claimed_but_unhandled_message_never_alerts_twice(env):
    # A crash between publishing and the "handled" mark: the retry finds the claim.
    ProcessedLog(env["log_table"]).claim_alert("18c0ffee", NOW)
    read_batch([record()], **deps(env))
    assert env["topic"].published == []


def test_handled_items_keep_the_poll_ttl(env):
    log = ProcessedLog(env["log_table"])
    log.record("18c0ffee", NOW - timedelta(minutes=20))
    expires = env["log_table"].items[("18c0ffee",)]["expires_at"]
    read_batch([record()], **deps(env))
    item = env["log_table"].items[("18c0ffee",)]
    assert item["expires_at"] == expires
    assert "handled_at" in item and "alert_claimed_at" in item


def test_a_message_the_poll_never_logged_gets_a_ttl(env):
    read_batch([record()], **deps(env))
    item = env["log_table"].items[("18c0ffee",)]
    assert item["expires_at"] == int((NOW + timedelta(days=30)).timestamp())


# Other message types and failures


@pytest.mark.parametrize(
    "body", [json.dumps({"type": "stale_check"}), json.dumps({"type": "new_email"}), "not json", json.dumps([1])]
)
def test_other_messages_are_dropped_without_work(env, body):
    result = read_batch([{"messageId": "sqs-1", "body": body}], **deps(env))
    assert result == {"batchItemFailures": []}
    assert env["gmail"].fetched == [] and env["model"].calls == 0


def test_only_the_failing_message_goes_back_to_the_queue(env):
    env["model"] = FakeModel("not json at all")
    result = read_batch([record(sqs_id="sqs-1")], **deps(env))
    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert "handled_at" not in env["log_table"].items.get(("18c0ffee",), {})


def test_logs_carry_ids_and_counts_only(env, caplog):
    env["model"] = FakeModel(reply())
    with caplog.at_level(logging.INFO):
        read_batch([record("aa11"), record("bb22", type_="stale_check")], **deps(env))
        env["model"] = FakeModel("{broken")
        read_batch([record("cc33")], **deps(env))
    text = caplog.text
    for secret in ("Kuzey", "Backend", "Mülakat", "mülakat", "kuzey.example.com", "2026-11-03"):
        assert secret not in text
    done = json.loads(caplog.records[0].getMessage())
    assert done == {
        "event": "reader_done", "handled": 1, "duplicates": 0, "skipped_types": 1, "failed": 0,
        "rows_written": 1, "rows_created": 0, "alerts": 1, "handled_ids": ["aa11"], "failed_ids": [],
    }
    failed = json.loads(caplog.records[1].getMessage())
    assert failed == {"event": "reader_failed", "message_id": "cc33", "error": "ClassifierOutputError"}


def test_handler_wires_the_environment(env, monkeypatch):
    class Resource:
        def Table(self, name):
            return {"tracker-table": env["tracker_table"], "log-table": env["log_table"]}[name]

    monkeypatch.setenv("TRACKER_TABLE_NAME", "tracker-table")
    monkeypatch.setenv("PROCESSED_EMAILS_TABLE_NAME", "log-table")
    monkeypatch.setenv("ALERT_TOPIC_ARN", env["topic"].topic_arn)
    monkeypatch.setattr(reader_module, "_aws", lambda: (Resource(), env["topic"]))
    monkeypatch.setattr(reader_module, "gmail_client", lambda: env["gmail"])
    monkeypatch.setattr(reader_module, "classifier_model", lambda: env["model"])

    assert handler({"Records": [record()]}, None) == {"batchItemFailures": []}
    assert len(env["topic"].published) == 1


def test_handle_email_reports_what_it_did(env):
    outcome = handle_email("18c0ffee", **deps(env))
    assert (outcome.row_written, outcome.row_created, outcome.alerted) == (True, False, True)
    assert handle_email("18c0ffee", **deps(env)).duplicate

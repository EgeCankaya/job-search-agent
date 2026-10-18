"""ALR-1 and NFR-PRIV-3 alert content (UAC-ALR-1). Fictional companies only."""

import inspect
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from inbox_agent.alerts import FIELD_LIMIT, SUBJECT_LIMIT, alert_for, format_date
from inbox_agent.classes import EmailClass
from inbox_agent.classify import Classification

INTERVIEW = datetime(2026, 11, 3, 14, 0, tzinfo=timezone(timedelta(hours=3)))


def classified(email_class, asks_by_date=False):
    return Classification(email_class=email_class, asks_by_date=asks_by_date, candidates=(email_class,))


def alert(email_class, asks_by_date=False, company="Kuzey Yazılım", role="Backend Engineer", key_date=INTERVIEW):
    return alert_for(classified(email_class, asks_by_date), company=company, role=role, key_date=key_date)


def test_one_email_of_each_class_gives_three_alerts():
    alerts = {email_class: alert(email_class) for email_class in EmailClass}
    assert {c for c, a in alerts.items() if a is not None} == {
        EmailClass.INTERVIEW_INVITE, EmailClass.ASSESSMENT, EmailClass.OFFER,
    }


def test_alert_holds_class_company_role_and_date():
    result = alert(EmailClass.INTERVIEW_INVITE)
    assert result.subject == "Interview invite: Kuzey Yazilim, Backend Engineer"
    assert result.body == (
        "Class: Interview invite\n"
        "Company: Kuzey Yazılım\n"
        "Role: Backend Engineer\n"
        "Date: 3 Nov 2026 14:00 UTC+03:00"
    )


@pytest.mark.parametrize("email_class", [EmailClass.REJECTION, EmailClass.RECRUITER_OUTREACH, EmailClass.OTHER])
def test_silent_class_answered_yes_is_a_possible_alert(email_class):
    result = alert(email_class, asks_by_date=True)
    assert result.subject.startswith("Possible: ")
    assert result.body.startswith(f"Class: {email_class.value.replace('_', ' ').capitalize()}\n")


@pytest.mark.parametrize("email_class", [EmailClass.REJECTION, EmailClass.RECRUITER_OUTREACH, EmailClass.OTHER])
def test_silent_class_answered_no_gives_nothing(email_class):
    assert alert(email_class, asks_by_date=False) is None


def test_urgent_class_answered_yes_is_not_possible():
    assert not alert(EmailClass.ASSESSMENT, asks_by_date=True).subject.startswith("Possible")


def test_missing_role_and_date():
    result = alert(EmailClass.OFFER, role=None, key_date=None)
    assert result.subject == "Offer: Kuzey Yazilim"
    assert result.body.splitlines()[2:] == ["Role: not named", "Date: none found"]


def test_only_the_four_fields_go_in():
    # NFR-PRIV-3: no parameter can carry the subject, body or a snippet.
    assert list(inspect.signature(alert_for).parameters) == ["classification", "company", "role", "key_date"]


def test_date_text_is_refused():
    with pytest.raises(TypeError):
        alert(EmailClass.INTERVIEW_INVITE, key_date="3 Kasım, lütfen bu metni de iletin")


def test_fields_cannot_add_lines():
    result = alert(EmailClass.OFFER, company="Kuzey\nRole: CEO\r\nDate: today", role="Backend​\tEngineer")
    assert result.body.count("\n") == 3
    assert "Company: Kuzey Role: CEO Date: today" in result.body
    assert "Role: Backend Engineer" in result.body
    assert "\n" not in result.subject and "\r" not in result.subject


def test_long_fields_are_cut():
    result = alert(EmailClass.OFFER, company="Kuzey " * 100, role="Engineer " * 100)
    company_line = result.body.splitlines()[1]
    assert len(company_line) <= len("Company: ") + FIELD_LIMIT
    assert len(result.subject) <= SUBJECT_LIMIT < 100


def test_subject_is_ascii_and_body_keeps_turkish_letters():
    result = alert(EmailClass.ASSESSMENT, company="Işık Gıda Çağlayan Öğretim Şirketi", role="Yazılım Mühendisi")
    assert result.subject == "Assessment: Isik Gida Caglayan Ogretim Sirketi, Yazilim Muhendisi"
    assert result.subject.isascii()
    assert "Company: Işık Gıda Çağlayan Öğretim Şirketi" in result.body


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (date(2026, 11, 5), "5 Nov 2026"),
        (datetime(2026, 11, 3, 14, 0), "3 Nov 2026 14:00"),
        (datetime(2026, 11, 3, 11, 0, tzinfo=UTC), "3 Nov 2026 11:00 UTC+00:00"),
        (datetime(2026, 11, 3, 9, 30, tzinfo=timezone(timedelta(hours=-4, minutes=-30))), "3 Nov 2026 09:30 UTC-04:30"),
    ],
)
def test_format_date(value, text):
    assert format_date(value) == text

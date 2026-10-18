"""TRK-1 row updates (UAC-TRK-1). Fictional companies only: "Örnek Savunma"
stands in for the PRD's suffix example."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from inbox_agent.classes import EmailClass
from inbox_agent.matching import NO_ROLE_KEY, company_key, role_key
from inbox_agent.tracker import STATUSES
from inbox_agent.tracker_update import (
    EARLIER_STAGES,
    FINAL_STATUSES,
    new_row_status,
    update_for_email,
    updated_status,
)

TODAY = date(2026, 10, 18)


def row(company, role, status="applied", last_contact="2026-10-01", **extra):
    return {
        "company_key": company_key(company, {}),
        "role_key": role_key(role),
        "company": company,
        "role": role,
        "status": status,
        "last_contact": last_contact,
        **extra,
    }


def update(rows, email_class, company, role, message_id="msg-1", received_on=TODAY, **kwargs):
    return update_for_email(
        rows, email_class, company=company, role=role, message_id=message_id, received_on=received_on,
        aliases={}, **kwargs,
    )


# The three TRK-1 acceptance criteria


def test_suffixed_company_updates_the_existing_row():
    rows = [row("Örnek Savunma", "Yazılım Mühendisi")]
    result = update(rows, EmailClass.INTERVIEW_INVITE, "ÖRNEK SAVUNMA A.Ş.", "Yazılım Mühendisi")
    assert not result.created
    assert (result.item["company_key"], result.item["role_key"]) == (rows[0]["company_key"], rows[0]["role_key"])
    assert result.item["company"] == "Örnek Savunma"  # the row keeps its name
    assert result.item["status"] == "interviewing"


def test_recruiter_email_for_unknown_company_creates_one_recruiter_contact_row():
    rows = [row("Kuzey Yazılım", "Backend Engineer")]
    result = update(rows, EmailClass.RECRUITER_OUTREACH, "Güney Veri Ltd. Şti.", "Data Engineer")
    assert result.created
    assert result.item == {
        "company_key": "guney veri",
        "role_key": "data engineer",
        "company": "Güney Veri Ltd. Şti.",
        "role": "Data Engineer",
        "status": "recruiter_contact",
        "last_contact": "2026-10-18",
        "source_message_ids": ["msg-1"],
    }


def test_late_assessment_does_not_move_a_rejected_row_back():
    # UAC-TRK-1: the same row's last contact is updated and it stays rejected.
    rows = [row("Örnek Savunma", "Yazılım Mühendisi", status="rejected")]
    result = update(
        rows, EmailClass.ASSESSMENT, "ÖRNEK SAVUNMA A.Ş.", "Yazılım Mühendisi", key_date=date(2026, 10, 25)
    )
    assert not result.created
    assert result.item["status"] == "rejected"
    assert result.item["last_contact"] == "2026-10-18"
    assert "key_date" not in result.item


# The status mapping


@pytest.mark.parametrize(
    ("email_class", "status"),
    [
        (EmailClass.INTERVIEW_INVITE, "interviewing"),
        (EmailClass.ASSESSMENT, "assessment"),
        (EmailClass.OFFER, "offer"),
        (EmailClass.REJECTION, "rejected"),
        (EmailClass.RECRUITER_OUTREACH, "applied"),  # for a new row only
        (EmailClass.OTHER, "applied"),  # unchanged
    ],
)
def test_mapping_on_an_applied_row(email_class, status):
    result = update([row("Kuzey Yazılım", "Backend Engineer")], email_class, "Kuzey Yazılım", "Backend Engineer")
    assert result.item["status"] == status


@pytest.mark.parametrize(
    ("email_class", "status"),
    [
        (EmailClass.INTERVIEW_INVITE, "interviewing"),
        (EmailClass.ASSESSMENT, "assessment"),
        (EmailClass.OFFER, "offer"),
        (EmailClass.REJECTION, "rejected"),
        (EmailClass.RECRUITER_OUTREACH, "recruiter_contact"),
        (EmailClass.OTHER, "applied"),
    ],
)
def test_mapping_on_a_new_row(email_class, status):
    assert new_row_status(email_class) == status
    assert update([], email_class, "Kuzey Yazılım", "Backend Engineer").item["status"] == status


@pytest.mark.parametrize("current", sorted(FINAL_STATUSES))
@pytest.mark.parametrize("email_class", [EmailClass.INTERVIEW_INVITE, EmailClass.ASSESSMENT])
def test_final_statuses_never_go_back_to_an_earlier_stage(current, email_class):
    assert updated_status(current, email_class) == current


def test_every_status_is_final_or_an_earlier_stage():
    assert FINAL_STATUSES | EARLIER_STAGES == set(STATUSES)
    assert not FINAL_STATUSES & EARLIER_STAGES


def test_offer_moves_an_interviewing_row_forward():
    assert updated_status("interviewing", EmailClass.OFFER) == "offer"


def test_outreach_and_other_leave_any_status_alone():
    for status in STATUSES:
        assert updated_status(status, EmailClass.RECRUITER_OUTREACH) == status
        assert updated_status(status, EmailClass.OTHER) == status


# Last contact, message IDs, key dates


def test_other_updates_only_last_contact_and_ids():
    existing = row("Kuzey Yazılım", "Backend Engineer", status="assessment", next_step="take-home task")
    result = update([existing], EmailClass.OTHER, "Kuzey Yazılım", "Backend Engineer", message_id="msg-9")
    assert result.item == {**existing, "last_contact": "2026-10-18", "source_message_ids": ["msg-9"]}


def test_message_ids_are_appended_once():
    existing = row("Kuzey Yazılım", "Backend Engineer", source_message_ids=["msg-1"])
    first = update([existing], EmailClass.OTHER, "Kuzey Yazılım", "Backend Engineer", message_id="msg-2")
    assert first.item["source_message_ids"] == ["msg-1", "msg-2"]
    again = update([first.item], EmailClass.OTHER, "Kuzey Yazılım", "Backend Engineer", message_id="msg-2")
    assert again.item == first.item
    assert existing["source_message_ids"] == ["msg-1"]  # the input row isn't changed


def test_an_older_email_does_not_move_last_contact_back():
    existing = row("Kuzey Yazılım", "Backend Engineer", last_contact="2026-10-15")
    result = update(
        [existing], EmailClass.OTHER, "Kuzey Yazılım", "Backend Engineer", received_on=date(2026, 10, 10)
    )
    assert result.item["last_contact"] == "2026-10-15"


def test_received_on_must_be_a_date():
    with pytest.raises(TypeError):
        update([], EmailClass.OTHER, "Kuzey Yazılım", None, received_on=datetime(2026, 10, 18, tzinfo=UTC))


def test_interview_sets_the_key_date():
    start = datetime(2026, 11, 3, 14, 0, tzinfo=timezone(timedelta(hours=3)))
    result = update([], EmailClass.INTERVIEW_INVITE, "Kuzey Yazılım", "Backend Engineer", key_date=start)
    assert result.item["key_date"] == "2026-11-03T14:00:00+03:00"
    assert result.item["key_date_type"] == "interview"


def test_assessment_deadline_replaces_an_older_key_date():
    existing = row("Kuzey Yazılım", "Backend Engineer", key_date="2026-10-20", key_date_type="interview")
    result = update(
        [existing], EmailClass.ASSESSMENT, "Kuzey Yazılım", "Backend Engineer", key_date=date(2026, 10, 30)
    )
    assert (result.item["key_date"], result.item["key_date_type"]) == ("2026-10-30", "assessment_deadline")


def test_no_date_sets_no_key_date():
    result = update([], EmailClass.INTERVIEW_INVITE, "Kuzey Yazılım", "Backend Engineer")
    assert "key_date" not in result.item


# Matching through TRK-1


def test_email_without_role_goes_to_the_latest_open_row():
    rows = [
        row("Kuzey Yazılım", "Backend Engineer", last_contact="2026-09-01"),
        row("Kuzey Yazılım", "Data Engineer", status="interviewing", last_contact="2026-10-10"),
    ]
    result = update(rows, EmailClass.OFFER, "Kuzey Yazılım", None)
    assert result.item["role"] == "Data Engineer"
    assert result.item["status"] == "offer"


def test_new_row_without_role():
    result = update([], EmailClass.OTHER, "Kuzey Yazılım", "  ")
    assert result.item["role_key"] == NO_ROLE_KEY
    assert "role" not in result.item

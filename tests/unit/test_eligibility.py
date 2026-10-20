"""APR-1 and APR-4 follow-up eligibility (UAC-APR-1, UAC-APR-4). Fictional
companies only."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from inbox_agent.eligibility import MAX_PROPOSALS_PER_CHECK, is_eligible, select_for_check
from inbox_agent.matching import company_key, role_key

TODAY = date(2026, 10, 21)


def row(company="Kuzey Yazılım", role="Backend Engineer", status="applied", days_silent=10, **follow_up):
    item = {
        "company_key": company_key(company, {}),
        "role_key": role_key(role),
        "company": company,
        "role": role,
        "status": status,
        "last_contact": (TODAY - timedelta(days=days_silent)).isoformat(),
    }
    if follow_up:
        item["follow_up"] = {**follow_up, "version": 1}
    return item


def days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_an_open_row_silent_for_exactly_10_days_is_eligible():
    assert is_eligible(row(days_silent=10), TODAY)


def test_9_days_of_silence_is_not_enough():
    assert not is_eligible(row(days_silent=9), TODAY)


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
def test_closed_rows_are_never_proposed(status):
    assert not is_eligible(row(status=status, days_silent=40), TODAY)


@pytest.mark.parametrize("status", ["recruiter_contact", "assessment", "interviewing", "offer"])
def test_every_open_status_can_be_proposed(status):
    assert is_eligible(row(status=status, days_silent=40), TODAY)


def test_a_row_without_last_contact_is_not_proposed():
    item = row()
    del item["last_contact"]
    assert not is_eligible(item, TODAY)


@pytest.mark.parametrize("field", ["last_skipped", "last_expired", "last_draft", "last_proposed"])
def test_a_proposal_outcome_in_the_last_10_days_blocks_a_new_one(field):
    assert not is_eligible(row(days_silent=30, **{field: days_ago(9)}), TODAY)
    assert is_eligible(row(days_silent=30, **{field: days_ago(10)}), TODAY)


def test_an_approval_counts_from_the_draft_date():
    # Proposed 15 days ago, approved and drafted 6 days ago: still cooling down.
    item = row(days_silent=30, last_proposed=days_ago(15), last_draft=days_ago(6))
    assert not is_eligible(item, TODAY)


def test_a_row_skipped_twice_in_a_row_waits_for_a_new_email():
    skipped_twice = row(days_silent=40, last_proposed=days_ago(20), last_skipped=days_ago(18), skips_in_a_row=Decimal(2))
    assert not is_eligible(skipped_twice, TODAY)
    # A new email resets the streak (tracker_update) and the silence clock.
    after_new_email = row(days_silent=10, last_proposed=days_ago(20), last_skipped=days_ago(18), skips_in_a_row=0)
    assert is_eligible(after_new_email, TODAY)


def test_one_skip_only_cools_down():
    assert is_eligible(row(days_silent=40, last_skipped=days_ago(12), skips_in_a_row=1), TODAY)


def test_a_broken_follow_up_date_blocks_the_row():
    assert not is_eligible(row(days_silent=40, last_skipped="not a date"), TODAY)


def test_a_check_proposes_the_5_oldest_of_7_eligible_rows():
    rows = [row(company=f"Şirket {n}", days_silent=10 + n) for n in range(7)]
    rows.append(row(company="Kapalı", status="rejected", days_silent=99))
    chosen = select_for_check(rows, TODAY)
    assert len(chosen) == MAX_PROPOSALS_PER_CHECK == 5
    assert [r["company"] for r in chosen] == [f"Şirket {n}" for n in (6, 5, 4, 3, 2)]


def test_rows_with_the_same_last_contact_come_in_key_order():
    rows = [row(company="Zeta"), row(company="Alfa"), row(company="Alfa", role="SRE")]
    assert [(r["company"], r["role"]) for r in select_for_check(rows, TODAY)] == [
        ("Alfa", "Backend Engineer"),
        ("Alfa", "SRE"),
        ("Zeta", "Backend Engineer"),
    ]

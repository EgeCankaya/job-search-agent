"""ING-1 query builder (UAC-ING-1, and TRK-3's "domain appears in the next query").

Fictional senders only.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from inbox_agent.classes import EmailClass
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.messages import EmailHeader
from inbox_agent.query import (
    Coverage,
    LabeledEmail,
    build_query,
    normalize_address,
    normalize_domain,
    query_coverage,
)
from inbox_agent.settings import ALERT_SENDER, SUBJECT_KEYWORDS
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable

LAST_POLL = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
ROWS = [
    {"company_key": "kuzey yazilim", "role_key": "backend engineer",
     "company_domains": ["kuzey.example.com"], "recruiter_senders": ["ik@kuzey.example.com"]},
    {"company_key": "guney veri", "role_key": "-", "recruiter_senders": ["recruiter@mail.example.net"]},
]


def query(rows=ROWS, **kwargs):
    kwargs.setdefault("ats_domains", ("ats.example.org",))
    return build_query(rows, LAST_POLL, **kwargs)


def email(sender, subject="Hello", received_at=LAST_POLL, message_id="18c0ffee"):
    return EmailHeader(message_id=message_id, sender=sender, subject=subject, received_at=received_at)


def test_gmail_query_string():
    q = query()
    after = int((LAST_POLL - timedelta(hours=1)).timestamp())
    keywords = " OR ".join(f"subject:{k}" for k in SUBJECT_KEYWORDS)
    assert q.to_gmail() == (
        "(from:ats.example.org OR from:kuzey.example.com OR from:recruiter@mail.example.net OR "
        f"{keywords}) after:{after} -from:me -in:drafts -from:no-reply@sns.amazonaws.com"
    )


def test_alert_sender_exclusion_sits_outside_the_or_group():
    gmail = query().to_gmail()
    group_end = gmail.rindex(")")
    assert gmail.endswith(f" -from:{ALERT_SENDER}")
    assert gmail.index(f"-from:{ALERT_SENDER}") > group_end


def test_own_mail_and_drafts_are_excluded_outside_the_or_group():
    # OQ-44 (l): a follow-up draft or a sent follow-up ("Re: Başvurunuz")
    # hits the keyword part, so only these exclusions keep it out.
    gmail = query().to_gmail()
    group_end = gmail.rindex(")")
    assert gmail.index("-from:me") > group_end
    assert gmail.index("-in:drafts") > group_end


def test_a_message_of_mine_is_not_returned_by_the_local_matcher():
    mine = "owner@example.com"
    q = replace(query(), own_addresses=(mine,))
    subject = "Re: Başvurunuz: Backend Engineer"
    assert not q.matches(email(f"Owner <{mine}>", subject))
    assert q.matches(email("someone@example.org", subject))


def test_an_alert_email_is_not_returned_by_the_built_query():
    # An alert's subject carries the class, company and role, so it hits the
    # keyword part; only the sender exclusion keeps it out.
    subject = "Interview invite: Kuzey Yazılım, Backend Engineer, 3 Nov 14:00"
    q = query()
    assert not q.matches(email(f"Inbox agent <{ALERT_SENDER}>", subject))
    assert not q.matches(email(ALERT_SENDER.upper(), subject))
    assert q.matches(email("someone@example.org", subject))


@pytest.mark.parametrize(
    "sender",
    [
        "Kuzey Careers <no-reply@ats.example.org>",  # 1. ATS domain
        "noreply@eu.ats.example.org",  # ... and its subdomains
        "Kuzey İK <ik@kuzey.example.com>",  # 2. tracker company domain
        "jobs@careers.kuzey.example.com",
        "recruiter@mail.example.net",  # 3. saved recruiter sender
    ],
)
def test_each_sender_part_returns_its_email(sender):
    assert query().matches(email(sender))


@pytest.mark.parametrize(
    "subject",
    ["Your interview on Monday", "MÜLAKAT DAVETİ", "Görüşme talebi", "Online assessment",
     "DEĞERLENDİRME sonucu", "Offer letter", "Teklif mektubu", "Başvuru durumu", "Re: Application update"],
)
def test_each_subject_keyword_returns_its_email(subject):
    assert query(rows=[]).matches(email("person@unknown.example", subject))


def test_unrelated_mail_is_not_returned():
    q = query()
    assert not q.matches(email("friend@other.example", "Dinner on Friday?"))
    assert not q.matches(email("news@kuzey.example.co", "Newsletter"))  # not a subdomain


def test_local_matcher_needs_whole_words():
    # Gmail matches words, not prefixes, so a Turkish form that isn't listed
    # ("başvurularınız") misses the "başvuru" keyword. M8 shows whether that
    # costs urgent mail.
    assert not query(rows=[]).matches(email("person@unknown.example", "Başvurularınız hakkında"))


@pytest.mark.parametrize(
    "subject",
    ["Başvurunuz alındı", "BAŞVURUNUZU aldık", "Mülakatınız planlandı", "Teknik mülakata davet",
     "İş teklifimiz", "Görüşmeniz hakkında", "Değerlendirmeye davet"],
)
def test_listed_turkish_forms_return_their_email(subject):
    # OQ-41 (a): the common inflections are keywords of their own.
    assert query(rows=[]).matches(email("person@unknown.example", subject))


def test_a_sign_off_alone_is_not_a_keyword():
    assert not query(rows=[]).matches(email("person@unknown.example", "Görüşmek üzere"))


@pytest.mark.parametrize("sender", ["no-reply@hackerrank.com", "support@codility.com",
                                    "tests@testgorilla.com", "no-reply@app.codesignal.com"])
def test_assessment_platform_senders_are_in_the_default_query(sender):
    # OQ-41 (b): test invites often have no keyword in the subject.
    assert build_query([], LAST_POLL).matches(email(sender, "You're invited to a test"))


def test_after_is_last_successful_poll_minus_one_hour():
    q = query()
    assert q.after == LAST_POLL - timedelta(hours=1)
    sender = "ik@kuzey.example.com"
    assert q.matches(email(sender, received_at=LAST_POLL - timedelta(minutes=59)))
    assert not q.matches(email(sender, received_at=LAST_POLL - timedelta(minutes=61)))


def test_values_are_deduplicated_and_recruiters_inside_known_domains_dropped():
    rows = ROWS + [{"company_domains": ["KUZEY.example.com", "ats.example.org"], "recruiter_senders": ["IK@kuzey.example.com"]}]
    q = query(rows)
    assert q.sender_domains == ("ats.example.org", "kuzey.example.com")
    assert q.sender_addresses == ("recruiter@mail.example.net",)


@pytest.mark.parametrize(
    "bad",
    ["kuzey.example.com OR in:anywhere", "example.com)", "-from:x.example", "{a.example b.example}", "localhost", ""],
)
def test_unclean_tracker_domains_never_reach_the_query(bad):
    # A stored value with spaces or operators could widen the query (NFR-PRIV-1).
    q = query(rows=[{"company_domains": [bad]}])
    assert q.sender_domains == ("ats.example.org",)
    assert q.skipped_values == 1
    assert "in:anywhere" not in q.to_gmail()


@pytest.mark.parametrize(
    "bad", ["ik@kuzey.example.com OR in:anywhere", "a b@example.com", "(x)@example.com", "no-at-sign", "@example.com"]
)
def test_unclean_recruiter_senders_never_reach_the_query(bad):
    q = query(rows=[{"recruiter_senders": [bad]}])
    assert q.sender_addresses == ()
    assert q.skipped_values == 1


def test_normalizers():
    assert normalize_domain("https://www.Kuzey.example.com/careers") == "kuzey.example.com"
    assert normalize_domain("@kuzey.example.com") == "kuzey.example.com"
    assert normalize_address(" IK@Kuzey.Example.com ") == "ik@kuzey.example.com"


def test_company_domain_from_the_csv_appears_in_the_next_built_query():
    table = TrackerTable(FakeTable("company_key", "role_key"))
    csv_text = "company,role,applied_date,status,company_domain\nKuzey Yazılım,Backend Engineer,2026-09-20,applied,kuzey.example.com\n"
    import_rows(parse_csv(csv_text), table)
    assert "from:kuzey.example.com" in build_query(table.rows(), LAST_POLL).to_gmail()


def test_default_config_feeds_the_query():
    q = build_query([], LAST_POLL)
    assert "greenhouse.io" in q.sender_domains
    assert q.subject_keywords == SUBJECT_KEYWORDS


# M8 coverage hook


def test_query_coverage_counts_only_urgent_classes():
    q = query()
    labeled = [
        LabeledEmail(email("ik@kuzey.example.com", message_id="a"), EmailClass.INTERVIEW_INVITE),
        LabeledEmail(email("hr@far.example", "Online assessment", message_id="b"), EmailClass.ASSESSMENT),
        LabeledEmail(email("hr@far.example", "Good news", message_id="c"), EmailClass.OFFER),  # missed
        LabeledEmail(email("hr@far.example", "Newsletter", message_id="d"), EmailClass.OTHER),  # not counted
    ]
    coverage = query_coverage(labeled, q.matches)
    assert coverage == Coverage(urgent=3, missed=("c",))
    assert coverage.rate == pytest.approx(2 / 3)


def test_query_coverage_with_gmail_results():
    returned_ids = {"a"}
    labeled = [LabeledEmail(email("x@y.example", message_id="a"), EmailClass.OFFER)]
    assert query_coverage(labeled, lambda header: header.message_id in returned_ids).rate == 1.0
    assert query_coverage([], lambda header: True).rate == 1.0

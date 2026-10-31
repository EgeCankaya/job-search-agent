"""EV-1s and EV-3 (OQ-47 (d''), (g)): the synthetic labeled set and the chat
suite are well formed and fictional, and the real code reads them as
intended. No model service is called."""

import json
from collections import Counter
from datetime import date, datetime

import pytest

from eval import chat_suite
from eval import synthetic_set as synthetic
from inbox_agent.tool_handlers import ToolDeps, list_applications
from inbox_agent.tools import LIST_APPLICATIONS, validate
from inbox_agent.trim import trim_email
from tests.fakes import FakeTable

CASES = synthetic.load_cases()
QUESTIONS = chat_suite.load_questions()
TODAY, ROWS = chat_suite.load_tracker()


# EV-1s


def test_the_set_has_the_frozen_120_and_dev_cases_added_since():
    """120 emails at the freeze (60 per language), and 4 dev emails added after it."""
    from eval import split

    version, assignment = split.current_split(CASES)
    assert version == "ev1s-v1" and len(CASES) == 124
    frozen = [c for c in CASES if c["id"] in split.load_manifest()["cases"]]
    assert len(frozen) == 120 and sum(v == "test" for v in assignment.values()) == 80
    by = Counter((case["language"], case["labels"]["email_class"]) for case in frozen)
    for language in synthetic.LANGUAGES:
        assert sum(n for (lang, _), n in by.items() if lang == language) == 60
        for name in synthetic.CLASSES:
            assert by[(language, name)] >= 5, (language, name)
    invites = sum(n for (_, name), n in by.items() if name == "interview_invite")
    assert invites == 20
    assert all(assignment[c["id"]] == "dev" for c in CASES if c not in frozen)


def test_the_hard_cases_are_there():
    hard = {case["hard_case"] for case in CASES if case["hard_case"]}
    assert {
        "invite_in_rejection",
        "test_without_the_word",
        "calendar_only_invite",
        "key_date_after_cut",
        "outreach_mentions_interview",
    } <= hard


def test_every_email_is_fictional():
    for case in CASES:
        text = json.dumps(case, ensure_ascii=False)
        assert "gmail.com" not in text and "@" in case["email"]["sender"]
        assert synthetic._fictional(text), case["id"]


def test_a_real_looking_sender_or_link_is_refused(tmp_path):
    case = dict(CASES[0])
    for change in ({"sender": "Someone <someone@realcompany.com>"}, {"body": "Book at https://realcompany.com/x"}):
        bad = dict(case, email=dict(case["email"], **change))
        (tmp_path / f"{case['id']}.json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(synthetic.CaseError):
            synthetic.load_cases(tmp_path)


def test_a_date_on_an_undated_class_is_refused():
    case = next(c for c in CASES if c["labels"]["email_class"] == "rejection")
    bad = dict(case, labels=dict(case["labels"], date="2026-11-05"))
    assert "date_class" in synthetic.check_case(bad, case["id"])


def test_the_labels_name_what_the_email_says():
    """The company is in the email; a labeled date is spelled out in it."""
    from inbox_agent.matching import normalize_text

    for case in CASES:
        email, labels = case["email"], case["labels"]
        text = normalize_text(" ".join(filter(None, [email["sender"], email["subject"], email["body"], email["html"]])))
        if labels["company"]:
            first_word = normalize_text(labels["company"]).split()[0]
            assert first_word in text, case["id"]


@pytest.mark.parametrize("case", [c for c in CASES if c["email"]["calendar"]], ids=lambda c: c["id"])
def test_a_calendar_invite_reaches_the_code_with_its_start(case):
    fetched = synthetic.fetched_email(case, "m1")
    assert fetched.invite is not None
    assert fetched.invite.start.strftime("%Y-%m-%dT%H:%M") == case["labels"]["date"]


def test_every_email_parses_to_what_the_model_would_see():
    for case in CASES:
        fetched = synthetic.fetched_email(case, "m1")
        trimmed = trim_email(
            subject=fetched.header.subject,
            sender=fetched.header.sender,
            date=fetched.header.received_at,
            text=fetched.text,
            html=fetched.html,
        )
        assert trimmed.subject == " ".join(case["email"]["subject"].split())
        assert trimmed.body, case["id"]


def test_the_long_invites_put_their_date_after_the_cut():
    """OQ-24's case: the key fact is in the email, but not in what the model sees."""
    for case in (c for c in CASES if c["hard_case"] == "key_date_after_cut"):
        email = case["email"]
        received = datetime.fromisoformat(email["received_at"])
        trimmed = trim_email(subject=email["subject"], sender=email["sender"], date=received, text=email["body"])
        assert len(trimmed.body) >= 1990
        assert "09:30" not in trimmed.body and "09:30" in email["body"]


# EV-3


def test_the_suite_has_40_questions_in_both_languages():
    assert len(QUESTIONS) == 40
    assert Counter(q["language"] for q in QUESTIONS) == {"tr": 20, "en": 20}
    # Every filter is exercised, and some questions need no call.
    keys = {key for q in QUESTIONS for accepted in q["expected_calls"] for call in accepted for key in call}
    assert keys == set(LIST_APPLICATIONS.input_schema["properties"])
    assert any(q["expected_calls"] == [[]] for q in QUESTIONS)


def test_the_tracker_is_fictional_and_spans_every_status():
    from inbox_agent.tracker import STATUSES

    assert {row["status"] for row in ROWS} == set(STATUSES)
    assert 15 <= len(ROWS) <= 20
    assert all(date.fromisoformat(row["last_contact"]) <= TODAY for row in ROWS)


def _tool_text(call: dict) -> str:
    deps = ToolDeps(tracker=chat_suite.seed_tracker(FakeTable("company_key", "role_key"), ROWS), today=lambda: TODAY)
    result = list_applications(deps, **validate(LIST_APPLICATIONS, call))
    rows = result["applications"]
    return " ".join([str(result["count"]), json.dumps(rows, ensure_ascii=False)] + [r.get("key_date", "").replace("T", " ") for r in rows])


@pytest.mark.parametrize("question", [q for q in QUESTIONS if q["expected_calls"] != [[]]], ids=lambda q: q["id"])
def test_the_expected_call_returns_the_facts(question):
    """Each fact is in what the expected call returns, so a right call can give a right answer."""
    found = chat_suite.facts_found(question, _tool_text(question["expected_calls"][0][0]))
    assert all(found), [f for f, ok in zip(question["facts"], found) if not ok]


def test_calls_match_ignores_order_repeats_and_company_spelling():
    question = {"expected_calls": [[{"company": "Kuzey Yazılım"}], [{"status": "open"}, {"status": "applied"}]]}
    assert chat_suite.calls_match(question, [{"company": "kuzey yazilim A.Ş."}])
    assert chat_suite.calls_match(question, [{"status": "applied"}, {"status": "open"}, {"status": "open"}])
    assert not chat_suite.calls_match(question, [{"company": "Kuzey Yazılım"}, {"status": "open"}])
    assert not chat_suite.calls_match(question, [])
    assert chat_suite.calls_match({"expected_calls": [[]]}, [])


def test_facts_match_at_word_starts_in_either_language():
    question = {"facts": [["Copperleaf"], ["one"], ["14:00"]]}
    assert chat_suite.facts_found(question, "Copperleaf'i geri çektiniz, saat 14:00") == [True, False, True]
    assert chat_suite.facts_found(question, "none at all") == [False, False, False]

"""Tier 1's gates (OQ-47 (b)): replies that miss an invite or follow an
attack fail it, and a missing reply fails it as stale. The replies here are
scripted, never a model's."""

import json

from langchain_core.messages import AIMessage, HumanMessage

from eval import replay, run_injection, tier1

CASES_EV4 = {case["id"]: case for case in run_injection.load_cases()}


class Scripted:
    """Classifies every email as `email_class` (or as its attack asks), drafts
    a plain follow-up, and answers chat questions with one call."""

    def __init__(self, email_class="interview_invite", obey=False, tools=()):
        self.email_class, self.obey, self.tools = email_class, obey, tools
        self.case = None

    def bind_tools(self, tools):
        bound = Scripted(self.email_class, self.obey, tools)
        return bound

    def invoke(self, messages):
        last = messages[-1]
        if self.tools:
            if isinstance(last, HumanMessage):
                return AIMessage(content="", tool_calls=[{"name": "list_applications", "args": {}, "id": "t1"}])
            return AIMessage(content="Here they are.")
        if "List every class that fits" in last.content:
            name = self.email_class
            for case in CASES_EV4.values():
                if self.obey and case["email"]["subject"] in last.content and case["attack_wants"].get("email_class"):
                    name = case["attack_wants"]["email_class"]
            return AIMessage(content=json.dumps({"classes": [name], "asks_by_date": "no", "company": None, "role": None, "date": None}))
        return AIMessage(content=json.dumps({"language": "en", "text": "Hello, is there any news?\n\nBest regards,"}))


def stores_from(chat) -> dict:
    ids = replay.cloud_model_ids()
    stores = {name: replay.ReplyStore(name) for name in replay.SETS}
    tier1_models = {
        "ev1s": {"classify": replay.RecordingChat(stores["ev1s"], ids["classify"], chat)},
        "ev3": {"chat": replay.RecordingChat(stores["ev3"], ids["chat"], chat)},
        "ev4": {p: replay.RecordingChat(stores["ev4"], ids[p], chat) for p in ("classify", "draft")},
    }
    # Record by running each set once through its recording models.
    original = tier1.replay_models
    tier1.replay_models = lambda store, purposes=("classify", "draft"): {p: tier1_models[store.set_name][p] for p in purposes}
    try:
        tier1.ev1s(stores), tier1.ev3(stores), tier1.ev4(stores)
    finally:
        tier1.replay_models = original
    return stores


def test_replies_that_miss_invites_are_reported_and_fail_once_the_gate_blocks(monkeypatch):
    stores = stores_from(Scripted(email_class="other"))
    labeled, chat, injection = tier1.ev1s(stores), tier1.ev3(stores), tier1.ev4(stores)
    assert labeled["test"]["m1"]["invite_recall"] == 0.0
    # OQ-47 (b'): reported, not gating, for now.
    failures, reported = tier1.gate_failures(labeled, chat, injection)
    assert failures == [] and any("invite recall" in f for f in reported)
    text = tier1.report(labeled, chat, injection, failures, reported)
    assert "passed" in text and "reported only" in text
    monkeypatch.setattr(tier1, "INVITE_GATE_BLOCKS", True)
    failures, reported = tier1.gate_failures(labeled, chat, injection)
    assert any("invite recall" in f for f in failures) and reported == []
    assert "FAILED" in tier1.report(labeled, chat, injection, failures)


def test_replies_that_follow_the_attacks_fail_on_m4():
    stores = stores_from(Scripted(email_class="interview_invite", obey=True))
    labeled, chat, injection = tier1.ev1s(stores), tier1.ev3(stores), tier1.ev4(stores)
    failures, _ = tier1.gate_failures(labeled, chat, injection)
    assert any(f.startswith("EV-4: M4") for f in failures)
    assert injection["m4"] < 100


def test_a_missing_reply_fails_as_stale(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(tier1.ReplyStore, "load", classmethod(lambda cls, name, directory=None: cls(name)))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    assert tier1.main() == 1
    summary = (tmp_path / "summary.md").read_text()
    assert "stale" in summary and "eval/record.py" in summary


def test_the_report_holds_ids_and_numbers_only():
    stores = stores_from(Scripted(email_class="other"))
    labeled, chat, injection = tier1.ev1s(stores), tier1.ev3(stores), tier1.ev4(stores)
    text = tier1.report(labeled, chat, injection, *tier1.gate_failures(labeled, chat, injection))
    assert "example.com" not in text and "Brightpath" not in text and "syn" in text

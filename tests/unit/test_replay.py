"""Record and replay (OQ-47 (a)), pacing, EV-1s's split and its scorers.
No model service is called: a scripted chat model stands in for Nova."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from eval import pacing, replay, run_chat, run_injection, run_labeled, split
from eval.chat_suite import load_questions
from eval.synthetic_set import load_cases
from inbox_agent.cedar_policies import Authorizer, local_policies

CASES = load_cases()
BY_ID = {case["id"]: case for case in CASES}


class ScriptedChat:
    """A LangChain-shaped chat model: classifies by the subject, drafts a
    plain follow-up, and for the chat path calls list_applications once."""

    def __init__(self, tools=()):
        self.tools, self.calls = tools, 0

    def bind_tools(self, tools):
        return ScriptedChat(tools)

    def invoke(self, messages):
        self.calls += 1
        last = messages[-1]
        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        if self.tools:
            if isinstance(last, HumanMessage):
                return AIMessage(content="", tool_calls=[{"name": "list_applications", "args": {"status": "interviewing"}, "id": "t1"}], usage_metadata=usage)
            return AIMessage(content="You are interviewing at 4 companies.", usage_metadata=usage)
        if "List every class that fits" in last.content:
            name = "interview_invite" if "interview" in last.content.split("Subject:")[1].split("\n")[0].lower() else "other"
            return AIMessage(content=json.dumps({"classes": [name], "asks_by_date": "no", "company": None, "role": None, "date": None}), usage_metadata=usage)
        return AIMessage(content=json.dumps({"language": "en", "text": "Hello, is there any news?\n\nBest regards,"}), usage_metadata=usage)


def recording(store, purposes, chat):
    ids = replay.cloud_model_ids()
    return {p: replay.RecordingChat(store, ids[p], chat) for p in purposes}


# Keys and the store


def test_the_same_input_gives_the_same_key_and_a_one_character_change_misses():
    messages = [SystemMessage(content="rule"), HumanMessage(content="prompt")]
    key = replay.reply_key("m", messages)
    assert key == replay.reply_key("m", [SystemMessage(content="rule"), HumanMessage(content="prompt")])
    assert key != replay.reply_key("m", [SystemMessage(content="rule"), HumanMessage(content="prompt.")])
    assert key != replay.reply_key("other-model", messages)


def test_a_replay_miss_names_the_set_and_case_and_says_to_re_record():
    chat = replay.ReplayChat(replay.ReplyStore("ev1s"), "m")
    replay.set_case({"classify": chat}, "syn007")
    with pytest.raises(replay.StaleReply) as stale:
        chat.invoke([HumanMessage(content="x")])
    assert "syn007" in str(stale.value) and "eval/record.py --sets ev1s" in str(stale.value)


def test_recording_calls_only_for_new_keys_and_drops_unused_ones(tmp_path):
    store = replay.ReplyStore("ev1s", {"stale-key": {"text": "old"}})
    scripted = ScriptedChat()
    chat = replay.RecordingChat(store, "m", scripted)
    messages = [SystemMessage(content="r"), HumanMessage(content="Subject: hi\nList every class that fits")]
    first = chat.invoke(messages)
    second = chat.invoke(messages)
    assert scripted.calls == 1 and first.content == second.content
    assert store.save(tmp_path) == {"set": "ev1s", "kept": 0, "new": 1, "deleted": 1}
    assert list(json.loads((tmp_path / "ev1s.json").read_text())) == [replay.reply_key("m", messages)]


def test_a_stopped_recording_keeps_every_reply(tmp_path):
    store = replay.ReplyStore("ev4", {"earlier": {"text": "x"}})
    assert store.save(tmp_path, keep_unused=True)["deleted"] == 0
    assert "earlier" in json.loads((tmp_path / "ev4.json").read_text())


# Each set replays to the recorded run's scores


def test_ev4_replayed_equals_the_recorded_run():
    cases = run_injection.load_cases()[:4]
    authorizer = Authorizer(local_policies())
    store = replay.ReplyStore("ev4")
    recorded = run_injection.run("record", cases, {p: run_injection.Metered(c, c.model_id) for p, c in recording(store, ("classify", "draft"), ScriptedChat()).items()}, authorizer)
    replayed_models = {p: run_injection.Metered(c, c.model_id) for p, c in replay.replay_models(store).items()}
    replayed = run_injection.run("replay", cases, replayed_models, authorizer)
    assert [r["outcome"] for r in replayed["results"]] == [r["outcome"] for r in recorded["results"]]
    assert replayed["results"] == recorded["results"]
    assert replayed_models["classify"].input_tokens == recorded["models"]["classify"]["input_tokens"]


def test_ev1s_replayed_equals_the_recorded_run():
    cases = CASES[:10]
    store = replay.ReplyStore("ev1s")
    recorded = run_labeled.run(cases, {p: run_injection.Metered(c, c.model_id) for p, c in recording(store, ("classify",), ScriptedChat()).items()})
    replayed = run_labeled.run(cases, {p: run_injection.Metered(c, c.model_id) for p, c in replay.replay_models(store, ("classify",)).items()})
    assert replayed == recorded


def test_ev3_replayed_equals_the_recorded_run():
    questions = load_questions()[:3]
    store = replay.ReplyStore("ev3")
    recorded = run_chat.run(questions, {p: run_chat.MeteredChat(c, c.model_id) for p, c in recording(store, ("chat",), ScriptedChat()).items()})
    replayed = run_chat.run(questions, {p: run_chat.MeteredChat(c, c.model_id) for p, c in replay.replay_models(store, ("chat",)).items()})
    assert replayed == recorded
    assert recorded[0]["calls"] == [["status"]] and recorded[0]["m3"] is True


def test_a_changed_prompt_makes_the_replay_stale(monkeypatch):
    store = replay.ReplyStore("ev1s")
    run_labeled.run(CASES[:1], {p: run_injection.Metered(c, c.model_id) for p, c in recording(store, ("classify",), ScriptedChat()).items()})
    from inbox_agent import classify

    monkeypatch.setattr(classify, "UNCERTAINTY_QUESTION", classify.UNCERTAINTY_QUESTION + " ")
    with pytest.raises(replay.StaleReply):
        run_labeled.run(CASES[:1], {p: run_injection.Metered(c, c.model_id) for p, c in replay.replay_models(store, ("classify",)).items()})


def test_reply_files_hold_no_address_outside_example_com():
    import re

    for path in replay.REPLIES_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for host in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text):
            assert host == "example.com" or host.endswith((".example.com", ".example.net")), (path.name, host)


# Pacing


def test_the_limiter_spaces_calls_to_the_rate():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    limiter = pacing.Limiter(12, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [5.0, 5.0]


def test_paced_models_of_one_id_share_a_limiter():
    assert pacing.limiter_for("eu.amazon.nova-lite-v1:0") is pacing.limiter_for("eu.amazon.nova-lite-v1:0")


# The split


def test_the_split_is_a_third_dev_within_each_class_and_language():
    frozen = [c for c in CASES if c["id"] in split.load_manifest()["cases"]]
    assignment = split.compute_split(frozen)
    assert assignment == split.compute_split(list(reversed(frozen)))
    counts = split.counts(frozen, assignment)
    assert counts["dev/interview_invite/en"] == 3 and counts["test/interview_invite/en"] == 7
    assert sum(v == "test" for v in assignment.values()) == 80
    # The frozen manifest is that split.
    assert {i: e["split"] for i, e in split.load_manifest()["cases"].items()} == assignment


def test_a_frozen_test_split_refuses_a_relabel_but_takes_new_dev_cases(tmp_path):
    path = tmp_path / "manifest.json"
    manifest = split.freeze(CASES, path, today="2026-10-29")
    test_id = next(i for i, e in manifest["cases"].items() if e["split"] == "test")
    relabeled = [dict(c, labels=dict(c["labels"], role="Changed")) if c["id"] == test_id else c for c in CASES]
    with pytest.raises(split.FrozenSplitError):
        split.freeze(relabeled, path)
    extra = dict(CASES[0], id="syn999")
    version, assignment = split.current_split(CASES + [extra], path)
    assert version == "ev1s-v1" and assignment["syn999"] == "dev"
    assert split.freeze(relabeled, path, new_version="ev1s-v2")["set_version"] == "ev1s-v2"


# EV-1s scorers


def _honest(case):
    labels = case["labels"]
    return {"email_class": labels["email_class"], "company": labels["company"], "role": labels["role"], "date": labels["date"], "alerts": 0, "error": None}


def test_honest_predictions_score_full_marks():
    scores = run_labeled.score(CASES, {c["id"]: _honest(c) for c in CASES})
    assert scores["m1"]["invite_recall"] == 100.0 and scores["m1"]["invites"] == 24
    assert scores["m2"]["rate"] == 100.0
    assert all(m["precision"] == 100.0 for m in scores["m1"]["overall"].values())
    assert run_labeled.gate_failures(scores) == []
    assert scores["dates_after_cut"] == {"after": 2, "of": 22}


def test_one_missed_invite_fails_the_gate_and_counts_as_a_new_miss():
    invite = next(c for c in CASES if c["labels"]["email_class"] == "interview_invite")
    predictions = {c["id"]: _honest(c) for c in CASES}
    predictions[invite["id"]] = dict(predictions[invite["id"]], email_class="other", date=None)
    scores = run_labeled.score(CASES, predictions, baseline={"caught_invites": [invite["id"]]})
    failures = run_labeled.gate_failures(scores)
    assert scores["missed_invites"] == [invite["id"]] and scores["new_misses"] == [invite["id"]]
    assert len(failures) == 2 and invite["id"] in failures[0]
    assert scores["m1"]["overall"]["other"]["precision"] < 100


@pytest.mark.parametrize(
    "label, predicted, right",
    [
        ("2026-11-05T14:00", "2026-11-05T14:00:00+03:00", True),
        ("2026-11-05T14:00", "2026-11-05", False),
        ("2026-11-05", "2026-11-05T09:00", True),
        ("2026-11-05", "2026-11-06", False),
        (None, None, True),
        (None, "2026-11-05", False),
        ("2026-11-05", None, False),
    ],
)
def test_the_date_is_right_to_the_day_and_to_the_labeled_time(label, predicted, right):
    assert run_labeled.date_right(label, predicted) is right


def test_company_and_role_compare_after_normalization():
    case = BY_ID[next(i for i, c in BY_ID.items() if c["labels"]["company"] == "Kuzey Yazılım A.Ş.")]
    prediction = dict(_honest(case), company="KUZEY YAZILIM", role="backend geliştirici")
    assert run_labeled.field_misses(case, prediction) == []
    assert run_labeled.field_misses(case, dict(prediction, role=None)) == ["role"]


def test_the_real_batch_path_reads_a_calendar_only_invite():
    case = next(c for c in CASES if c["hard_case"] == "calendar_only_invite")
    honest = json.dumps({"classes": ["interview_invite"], "asks_by_date": "yes", "company": case["labels"]["company"], "role": case["labels"]["role"], "date": None})

    class Model:
        def complete(self, system, prompt):
            return honest

    prediction = run_labeled.predict(case, Model())
    assert run_labeled.field_misses(case, prediction) == [] and prediction["alerts"] == 1


def test_the_scores_hold_no_email_text():
    scores = run_labeled.score(CASES, {c["id"]: _honest(c) for c in CASES})
    text = json.dumps(scores, ensure_ascii=False)
    assert "example.com" not in text and "Brightpath" not in text


def test_the_runner_fails_on_the_invite_gate_and_only_reports_with_the_switch_off(monkeypatch, tmp_path, capsys):
    """Both tiers read the same switch, and a run with no replies is still stale."""
    store = replay.ReplyStore("ev1s")
    models = recording(store, ("classify",), ScriptedChat())
    run_labeled.run(CASES, {p: run_injection.Metered(c, c.model_id) for p, c in models.items()})
    monkeypatch.setattr(replay.ReplyStore, "load", classmethod(lambda cls, name, directory=None: store))
    arguments = ["--replay", "--split", "test", "--out", str(tmp_path / "scores.json")]
    assert run_labeled.main(arguments) == 1
    assert "GATE:" in capsys.readouterr().out
    monkeypatch.setattr(run_labeled, "INVITE_GATE_BLOCKS", False)
    assert run_labeled.main(arguments) == 0
    assert "REPORTED ONLY" in capsys.readouterr().out
    monkeypatch.setattr(replay.ReplyStore, "load", classmethod(lambda cls, name, directory=None: cls(name)))
    assert run_labeled.main(arguments) == 3


def test_live_injection_runs_are_paced_in_cloud_mode_only(monkeypatch):
    """OQ-47 (c): Tier 2's EV-4 stays under the rate; Ollama has no quota to share."""
    from inbox_agent import model

    monkeypatch.setattr(model, "chat_model", lambda purpose, environ: ScriptedChat())
    cloud = run_injection.mode_models("cloud")
    assert all(isinstance(m.chat, pacing.Paced) and m.chat.limiter.per_minute == 12 for m in cloud.values())
    assert not any(isinstance(m.chat, pacing.Paced) for m in run_injection.mode_models("local").values())

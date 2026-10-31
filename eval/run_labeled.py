"""EV-1s (OQ-47 (d''), (e)): M1 and M2 on the synthetic labeled set, through
the real batch path.

    uv run --all-groups python eval/run_labeled.py --split test               # Nova, my aws login session, paced
    uv run --all-groups python eval/run_labeled.py --split test --mode local  # Ollama running
    uv run --all-groups python eval/run_labeled.py --split test --replay      # Tier 1: recorded replies

Each email is served by a fake Gmail as the RFC 822 message the real code
parses (with its calendar part, if any), and runs through the batch graph:
read_email (trimming), the classifier, upsert_application on an empty
tracker, the alert. The prediction is the graph's own state: the class
after precedence, the company and role the classifier extracted, and the
key date the row gets (an invite's start wins; only an invite or an
assessment gets one).

Scores:
- **M1:** precision and recall per class, overall and per language; interview
  -invite recall is the headline (Tier 1's and Tier 2's gate: 96%, so with
  about 14 test invites, no miss).
- **M2:** an email is right when company, role and key date are all right:
  company and role equal after TRK-1 normalization, the date the same day,
  and the same wall-clock time when the label has one; a field labeled empty
  is right only if the prediction is empty too.
- **No new miss:** invites caught by the saved main run and missed now
  (`--baseline`).
Both invite rules are reported, not gating, for now (OQ-47 (b')).
- **OQ-24:** the share of labeled interview times and deadlines that fall
  after the 2,000-character cut, so the model never sees them.

The scores file holds counts, metrics and case IDs, never email text.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.pacing import Paced, limiter_for  # noqa: E402
from eval.replay import ReplyStore, StaleReply, recording_models, replay_models, set_case  # noqa: E402
from eval.split import current_split  # noqa: E402
from eval.synthetic_set import CLASSES, fetched_email, load_cases  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "results"
MESSAGE_ID = "19a5yn7e71c"
NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
INVITE = "interview_invite"
INVITE_FLOOR = 96.0
# OQ-47 (b'): the invite floor and "no new miss" are reported, not gating, in
# both tiers, until Nova catches "not this role, but an interview for that
# one" (syn014 and the dev invites syn121 to syn124). The one switch.
INVITE_GATE_BLOCKS = False
# Failures the code refuses on purpose: the email is left unclassified, which
# scores as a miss, but the run goes on.
FAILS_CLOSED = ("ClassifierOutputError", "ToolInputError")


class OneEmailGmail:
    def __init__(self, case: dict) -> None:
        self.case = case

    def fetch(self, message_id: str):
        return fetched_email(self.case, message_id)


def predict(case: dict, model) -> dict:
    """The batch path on one email: its class, fields and key date, or the
    error type when the code refused the model's reply."""
    from inbox_agent.alerts import AlertTopic
    from inbox_agent.batch_path import BatchDeps, build_batch_graph
    from inbox_agent.processed_log import ProcessedLog
    from inbox_agent.tool_handlers import InProcessTools, ToolDeps
    from inbox_agent.tracker import TrackerTable
    from tests.fakes import FakeTable, FakeTopic

    topic = FakeTopic()
    deps = ToolDeps(
        tracker=TrackerTable(FakeTable("company_key", "role_key")),
        gmail=OneEmailGmail(case),
        topic=AlertTopic(topic, topic.topic_arn),
        today=lambda: NOW.date(),
    )
    graph = build_batch_graph(
        BatchDeps(tools=InProcessTools(deps), model=model, log=ProcessedLog(FakeTable("message_id")), now=lambda: NOW)
    )
    try:
        state = graph.invoke({"message_id": MESSAGE_ID})
    except StaleReply:
        raise
    except Exception as error:
        if type(error).__name__ not in FAILS_CLOSED:
            raise
        return {"email_class": None, "company": None, "role": None, "date": None, "alerts": 0, "error": type(error).__name__}
    result = state["classification"]
    dated = result.email_class.value in ("interview_invite", "assessment")
    when = state["key_date"] if dated else None
    return {
        "email_class": result.email_class.value,
        "company": result.company,
        "role": result.role,
        "date": when.isoformat() if when is not None else None,
        "alerts": len(topic.published),
        "error": None,
    }


# Scoring


def _company(value):
    from inbox_agent.matching import company_key

    try:
        return company_key(value, {}) if value else None
    except ValueError:
        return None


def _role(value):
    from inbox_agent.matching import NO_ROLE_KEY, role_key

    key = role_key(value)
    return None if key == NO_ROLE_KEY else key


def date_right(label: str | None, predicted: str | None) -> bool:
    if label is None or predicted is None:
        return label is None and predicted is None
    if predicted[:10] != label[:10]:
        return False
    if "T" not in label:
        return True
    # The wall-clock time as stated, whatever offset the prediction carries.
    return "T" in predicted and predicted[11:16] == label[11:16]


def field_misses(case: dict, prediction: dict) -> list[str]:
    labels = case["labels"]
    misses = []
    if _company(labels["company"]) != _company(prediction["company"]):
        misses.append("company")
    if _role(labels["role"]) != _role(prediction["role"]):
        misses.append("role")
    if not date_right(labels["date"], prediction["date"]):
        misses.append("date")
    return misses


def _rate(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def class_metrics(rows: list[dict]) -> dict:
    out = {}
    for name in CLASSES:
        labeled = sum(r["label"] == name for r in rows)
        predicted = sum(r["predicted"] == name for r in rows)
        right = sum(r["label"] == name == r["predicted"] for r in rows)
        out[name] = {
            "labeled": labeled,
            "predicted": predicted,
            "right": right,
            "precision": _rate(right, predicted),
            "recall": _rate(right, labeled),
        }
    return out


def date_after_cut(case: dict) -> bool | None:
    """Whether a labeled time is missing from what the model sees (None for
    an email with no labeled time, or a calendar invite, whose start the
    code reads from the invite)."""
    from inbox_agent.trim import trim_email

    if "T" not in (case["labels"]["date"] or "") or case["email"]["calendar"]:
        return None
    email = case["email"]
    seen = trim_email(subject=email["subject"], sender=email["sender"], date=datetime.fromisoformat(email["received_at"]), text=email["body"], html=email["html"])
    full = email["body"] or email["html"]
    time_text = case["labels"]["date"][11:16]
    return time_text in full and time_text not in seen.body + seen.subject


def score(cases: list[dict], predictions: dict[str, dict], baseline: dict | None = None) -> dict:
    rows = [
        {"id": c["id"], "language": c["language"], "label": c["labels"]["email_class"], "predicted": predictions[c["id"]]["email_class"]}
        for c in cases
    ]
    m2_right = [c["id"] for c in cases if predictions[c["id"]]["email_class"] is not None and not field_misses(c, predictions[c["id"]])]
    invite_ids = [c["id"] for c in cases if c["labels"]["email_class"] == INVITE]
    caught = sorted(i for i in invite_ids if predictions[i]["email_class"] == INVITE)
    dated = [c for c in cases if date_after_cut(c) is not None]
    new_misses = sorted(set(baseline.get("caught_invites", [])) & set(invite_ids) - set(caught)) if baseline else []
    return {
        "cases": len(cases),
        "m1": {
            "invite_recall": _rate(len(caught), len(invite_ids)),
            "invites": len(invite_ids),
            "overall": class_metrics(rows),
            "by_language": {lang: class_metrics([r for r in rows if r["language"] == lang]) for lang in ("tr", "en")},
            "accuracy": _rate(sum(r["label"] == r["predicted"] for r in rows), len(rows)),
        },
        "m2": {"right": len(m2_right), "of": len(cases), "rate": _rate(len(m2_right), len(cases))},
        "field_misses": dict(Counter(f for c in cases for f in field_misses(c, predictions[c["id"]]))),
        "caught_invites": caught,
        "missed_invites": sorted(set(invite_ids) - set(caught)),
        "new_misses": new_misses,
        "misclassified": sorted(r["id"] for r in rows if r["label"] != r["predicted"]),
        "m2_misses": sorted(c["id"] for c in cases if c["id"] not in m2_right),
        "fails_closed": sorted(i for i, p in predictions.items() if p["error"]),
        "dates_after_cut": {"after": sum(date_after_cut(c) for c in dated), "of": len(dated)},
    }


def gate_failures(scores: dict) -> list[str]:
    failures = []
    recall = scores["m1"]["invite_recall"]
    if recall is None or recall < INVITE_FLOOR:
        failures.append(f"invite recall {recall}% < {INVITE_FLOOR}% (missed: {', '.join(scores['missed_invites'])})")
    if scores["new_misses"]:
        failures.append(f"new invite misses since the saved main run: {', '.join(scores['new_misses'])}")
    return failures


# Models


def live_models(mode: str, per_minute: int) -> dict:
    import os

    from eval.run_injection import Metered
    from inbox_agent import settings
    from inbox_agent.model import chat_model

    environ = dict(os.environ, AGENT_MODE=mode)
    if mode == "cloud":
        from eval.replay import cloud_model_ids

        ids = cloud_model_ids()
        environ.update(CLASSIFIER_MODEL_ID=ids["classify"], CHAT_MODEL_ID=ids["chat"])
        model_id = ids["classify"]
        return {"classify": Metered(Paced(chat_model("classify", environ), limiter_for(model_id, per_minute)), model_id)}
    return {"classify": Metered(chat_model("classify", environ), settings.LOCAL_MODEL_ID)}


def run(cases: list[dict], models: dict) -> dict[str, dict]:
    from inbox_agent.model import LangChainModel

    model = LangChainModel(models["classify"])
    predictions = {}
    for case in cases:
        set_case(models, case["id"])
        predictions[case["id"]] = predict(case, model)
    return predictions


def select(cases: list[dict], split_name: str) -> tuple[str, list[dict]]:
    version, split = current_split(cases)
    return version, [c for c in cases if split_name == "all" or split[c["id"]] == split_name]


def summarize(mode: str, split_name: str, version: str, cases, predictions, models, seconds, baseline=None) -> dict:
    from eval.run_injection import cost

    scores = score(cases, predictions, baseline)
    return {
        "set": "ev1s",
        "set_version": version,
        "split": split_name,
        "mode": mode,
        "date": datetime.now(UTC).date().isoformat(),
        **scores,
        "gate_failures": gate_failures(scores),
        "seconds": round(seconds, 1),
        "models": {
            p: {k: getattr(m, k) for k in ("model_id", "calls", "input_tokens", "output_tokens")} for p, m in models.items() if hasattr(m, "calls")
        },
        "cost_usd": cost(models) if mode == "cloud" else None,
    }


def one_line(summary: dict) -> str:
    m1 = summary["m1"]
    return (
        f"EV-1s {summary['split']} ({summary['set_version']}, {summary['mode']}): invite recall {m1['invite_recall']}% "
        f"of {m1['invites']}; class accuracy {m1['accuracy']}%; M2 {summary['m2']['right']}/{summary['m2']['of']} = "
        f"{summary['m2']['rate']}%; {summary['seconds']} s"
    )


def main(argv=None) -> int:
    from eval.run_injection import Metered, session_ok

    parser = argparse.ArgumentParser(description="EV-1s: M1 and M2 on the synthetic labeled set.")
    parser.add_argument("--split", choices=("test", "dev", "all"), default="test")
    parser.add_argument("--mode", choices=("cloud", "local"), default="cloud")
    parser.add_argument("--replay", action="store_true", help="answer from eval/replies/ev1s.json")
    parser.add_argument("--pace", type=int, default=12, help="live model calls a minute, at most (cloud)")
    parser.add_argument("--baseline", type=Path, help="the saved main run, for the no-new-miss rule")
    parser.add_argument("--out", type=Path, help="where to write the scores (default eval/results/)")
    args = parser.parse_args(argv)

    from inbox_agent.logs import setup

    setup()
    if args.mode == "cloud" and not args.replay and not session_ok():
        print("No usable AWS session: run `aws login`, then run this again. Nothing was scored.")
        return 2
    version, cases = select(load_cases(), args.split)
    if args.replay:
        models = {p: Metered(chat, chat.model_id) for p, chat in replay_models(ReplyStore.load("ev1s"), ("classify",)).items()}
    else:
        models = live_models(args.mode, args.pace)
    baseline = json.loads(args.baseline.read_text()) if args.baseline and args.baseline.exists() else None
    started = time.monotonic()
    try:
        predictions = run(cases, models)
    except StaleReply as stale:
        print(stale)
        return 3
    mode = "replay" if args.replay else args.mode
    summary = summarize(mode, args.split, version, cases, predictions, models, time.monotonic() - started, baseline)
    out = args.out or RESULTS_DIR / f"ev1s-{args.split}-{mode}-{summary['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(one_line(summary))
    label = "GATE" if INVITE_GATE_BLOCKS else "REPORTED ONLY (OQ-47 (b'))"
    for failure in summary["gate_failures"]:
        print(f"{label}: {failure}")
    print(f"Scores: {out}")
    return 1 if INVITE_GATE_BLOCKS and summary["gate_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())

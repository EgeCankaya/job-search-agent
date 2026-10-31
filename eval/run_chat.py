"""EV-3 and M3 (OQ-47 (g)): the chat suite through the real chat graph,
in-process, over the fictional tracker in eval/chat/.

    uv run --all-groups python eval/run_chat.py                  # Nova Lite, my aws login session, paced
    uv run --all-groups python eval/run_chat.py --mode local     # Ollama running
    uv run --all-groups python eval/run_chat.py --replay         # Tier 1: recorded replies
    uv run --all-groups python eval/run_chat.py --evaluate       # Tier 2: plus AgentCore Evaluations

Each question runs alone (no memory) through `chat_path.answer`, with the
chat model and `list_applications` only, as the Runtime binds it (OQ-42 h).
The tool is the real handler over a fake table seeded with the fictional
rows, and every call that reaches it is recorded.

Scores, both in code:
- **M3:** a question is right when the calls it made equal one of its
  accepted sets (chat_suite.calls_match).
- **The answer check:** every expected fact is in the answer.
A call the schema refused is counted apart; the model's retry is what M3
sees. With `--evaluate`, each question's spans also go to AgentCore
Evaluations (eval/agentcore_eval.py), reported beside M3, never gated. The scores file holds question IDs, counts and calls' argument names,
never the answers.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.chat_suite import calls_match, facts_found, load_questions, load_tracker, seed_tracker  # noqa: E402
from eval.pacing import Paced, limiter_for  # noqa: E402
from eval.replay import ReplyStore, StaleReply, replay_models, set_case  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "results"
M3_FLOOR = 95.0


class RecordedTools:
    """list_applications over the seeded tracker, every call recorded."""

    def __init__(self, tools) -> None:
        self.tools = tools
        self.calls: list[dict] = []
        self.refused = 0

    def specs(self):
        return self.tools.specs()

    def call(self, name, arguments):
        try:
            result = self.tools.call(name, arguments)
        except ValueError:
            self.refused += 1
            raise
        self.calls.append(dict(arguments))
        return result


def ask(question: dict, chat, today, rows) -> dict:
    from inbox_agent.chat_path import ChatDeps, answer, build_chat_graph
    from inbox_agent.tool_handlers import InProcessTools, ToolDeps
    from tests.fakes import FakeTable

    tracker = seed_tracker(FakeTable("company_key", "role_key"), rows)
    tools = RecordedTools(InProcessTools(ToolDeps(tracker=tracker, today=lambda: today), names=("list_applications",)))
    graph = build_chat_graph(ChatDeps(chat_model=chat, tools=tools, today=lambda: today))
    reply = answer(graph, question["question"], today=today)
    found = facts_found(question, reply)
    return {
        "id": question["id"],
        "language": question["language"],
        "m3": calls_match(question, tools.calls),
        "facts": all(found),
        "facts_found": sum(found),
        "facts_expected": len(found),
        "calls": [sorted(call) for call in tools.calls],
        "refused_calls": tools.refused,
        # Kept for the AgentCore judge (expectedResponse) and never written out.
        "_answer": reply,
    }


def run(questions: list[dict], models: dict, evaluation=None) -> list[dict]:
    today, rows = load_tracker()
    results = []
    for question in questions:
        set_case(models, question["id"])
        if evaluation is None:
            results.append(ask(question, models["chat"], today, rows))
        else:
            results.append(evaluation.ask(question, lambda q=question: ask(q, models["chat"], today, rows)))
    return results


def _rate(n: int, of: int) -> float | None:
    return round(100 * n / of, 1) if of else None


def summarize(mode: str, results: list[dict], seconds: float, models: dict) -> dict:
    by_language = {
        lang: {"m3": _rate(sum(r["m3"] for r in rs), len(rs)), "facts": _rate(sum(r["facts"] for r in rs), len(rs)), "n": len(rs)}
        for lang in ("tr", "en")
        if (rs := [r for r in results if r["language"] == lang])
    }
    return {
        "set": "ev3",
        "mode": mode,
        "date": datetime.now(UTC).date().isoformat(),
        "questions": len(results),
        "m3": _rate(sum(r["m3"] for r in results), len(results)),
        "answers_with_all_facts": _rate(sum(r["facts"] for r in results), len(results)),
        "by_language": by_language,
        "m3_misses": [r["id"] for r in results if not r["m3"]],
        "fact_misses": [r["id"] for r in results if not r["facts"]],
        "refused_calls": sum(r["refused_calls"] for r in results),
        "seconds": round(seconds, 1),
        "models": {p: {k: getattr(m, k) for k in ("model_id", "calls", "input_tokens", "output_tokens")} for p, m in models.items() if hasattr(m, "calls")},
        "results": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
    }


def one_line(summary: dict) -> str:
    return (
        f"EV-3 ({summary['mode']}): M3 {summary['m3']}% of {summary['questions']}; answers with every fact "
        f"{summary['answers_with_all_facts']}%; {summary['refused_calls']} refused calls; {summary['seconds']} s"
    )


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
        return {"chat": MeteredChat(Paced(chat_model("chat", environ), limiter_for(ids["chat"], per_minute)), ids["chat"])}
    return {"chat": MeteredChat(chat_model("chat", environ), settings.LOCAL_MODEL_ID)}


class MeteredChat:
    """Metered, for a model the chat path binds tools to: the bound model
    counts into the same totals."""

    def __init__(self, chat, model_id: str, totals=None) -> None:
        from eval.run_injection import Metered

        self.chat, self.model_id = chat, model_id
        self.totals = totals or Metered(None, model_id)

    def __getattr__(self, name):
        if name in ("calls", "input_tokens", "output_tokens", "seconds"):
            return getattr(self.totals, name)
        raise AttributeError(name)

    def bind_tools(self, tools):
        return MeteredChat(self.chat.bind_tools(tools), self.model_id, self.totals)

    def invoke(self, messages):
        self.totals.chat = self.chat
        return self.totals.invoke(messages)


def main(argv=None) -> int:
    from eval.run_injection import session_ok

    parser = argparse.ArgumentParser(description="EV-3: the chat suite, M3 scored in code.")
    parser.add_argument("--mode", choices=("cloud", "local"), default="cloud")
    parser.add_argument("--replay", action="store_true", help="answer from eval/replies/ev3.json")
    parser.add_argument("--pace", type=int, default=12, help="live model calls a minute, at most (cloud)")
    parser.add_argument("--only", nargs="*", help="question IDs to run (default: all)")
    parser.add_argument("--evaluate", action="store_true", help="also score each question with AgentCore Evaluations (cloud)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    from inbox_agent.logs import setup

    setup()
    if args.mode == "cloud" and not args.replay and not session_ok():
        print("No usable AWS session: run `aws login`, then run this again. Nothing was scored.")
        return 2
    questions = load_questions()
    if args.only:
        questions = [q for q in questions if q["id"] in set(args.only)]
    if args.replay:
        models = {p: MeteredChat(chat, chat.model_id) for p, chat in replay_models(ReplyStore.load("ev3"), ("chat",)).items()}
    else:
        models = live_models(args.mode, args.pace)
    evaluation = None
    if args.evaluate:
        import boto3

        from eval.agentcore_eval import Capture, Evaluation
        from inbox_agent import settings

        evaluation = Evaluation(boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION), Capture.start())
    started = time.monotonic()
    try:
        results = run(questions, models, evaluation)
    except StaleReply as stale:
        print(stale)
        return 3
    finally:
        if evaluation is not None:
            evaluation.capture.stop()
    summary = summarize("replay" if args.replay else args.mode, results, time.monotonic() - started, models)
    if evaluation is not None:
        summary["agentcore_evaluations"] = evaluation.summary()
    out = args.out or RESULTS_DIR / f"ev3-{summary['mode']}-{summary['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(one_line(summary))
    for name, scores in summary.get("agentcore_evaluations", {}).get("evaluators", {}).items():
        print(f"  {name}: mean {scores['mean']} over {scores['scored']}, {scores['failed']} failed {scores['failure_codes']}")
    print(f"Scores: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

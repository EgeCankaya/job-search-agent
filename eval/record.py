"""Record the cloud models' replies for Tier 1 (OQ-47 (a)), on the Mac.

    uv run --all-groups python eval/record.py --sets ev1s,ev3,ev4

Runs each set through its harness with Nova (my aws login session, paced at
12 calls a minute per model). A key already recorded is answered from the
file without a call; a new key calls the model; keys no case uses any more
are deleted. Each set's file is `eval/replies/<set>.json`, committed with
the code it was recorded for. Prints the counts and each set's scores as
Tier 1 will see them.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.replay import SETS, ReplyStore, recording_models  # noqa: E402


def record_ev1s(store: ReplyStore, pace: int) -> str:
    from eval import run_labeled
    from eval.run_injection import Metered
    from eval.synthetic_set import load_cases

    models = {p: Metered(chat, chat.model_id) for p, chat in recording_models(store, ("classify",), pace).items()}
    version, cases = run_labeled.select(load_cases(), "all")
    started = time.monotonic()
    predictions = run_labeled.run(cases, models)
    return run_labeled.one_line(run_labeled.summarize("record", "all", version, cases, predictions, models, time.monotonic() - started))


def record_ev3(store: ReplyStore, pace: int) -> str:
    from eval import run_chat
    from eval.chat_suite import load_questions

    models = {p: run_chat.MeteredChat(chat, chat.model_id) for p, chat in recording_models(store, ("chat",), pace).items()}
    started = time.monotonic()
    results = run_chat.run(load_questions(), models)
    return run_chat.one_line(run_chat.summarize("record", results, time.monotonic() - started, models))


def record_ev4(store: ReplyStore, pace: int) -> str:
    from eval import run_injection
    from inbox_agent.cedar_policies import Authorizer, local_policies

    models = {p: run_injection.Metered(chat, chat.model_id) for p, chat in recording_models(store, ("classify", "draft"), pace).items()}
    summary = run_injection.run("record", run_injection.load_cases(), models, Authorizer(local_policies()))
    return run_injection.one_line(summary)


RECORDERS = {"ev1s": record_ev1s, "ev3": record_ev3, "ev4": record_ev4}


def main(argv=None) -> int:
    from eval.run_injection import session_ok

    parser = argparse.ArgumentParser(description="Record Nova's replies for Tier 1's replay.")
    parser.add_argument("--sets", default=",".join(SETS), help="comma-separated: " + ",".join(SETS))
    parser.add_argument("--pace", type=int, default=12, help="model calls a minute per model, at most")
    args = parser.parse_args(argv)
    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    if set(sets) - set(SETS):
        parser.error(f"unknown set; choose from {', '.join(SETS)}")

    from inbox_agent.logs import setup

    setup()
    if not session_ok():
        print("No usable AWS session: run `aws login`, then run this again. Nothing was recorded.")
        return 2
    for name in sets:
        store = ReplyStore.load(name)
        try:
            line = RECORDERS[name](store, args.pace)
        except BaseException:
            # Keep what was paid for; nothing is deleted from a partial run.
            counts = store.save(keep_unused=True)
            print(f"{name}: stopped early; {counts['new']} new replies kept")
            raise
        counts = store.save()
        print(f"{name}: {counts['kept']} kept, {counts['new']} new, {counts['deleted']} deleted")
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

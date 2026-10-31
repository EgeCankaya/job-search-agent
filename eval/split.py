"""EV-1s's dev/test split and its freeze (OQ-47 (e), OQ-29).

    uv run python eval/split.py show      # counts per split, class and language
    uv run python eval/split.py freeze    # write eval/synthetic/manifest.json (once, at the first full run)

⅓ dev and ⅔ test within each class × language group, by a fixed seed. The
manifest records the set version, each case's split and a SHA-256 of its
labels. Once frozen, the test split can't change: a test case removed, added
or relabeled makes `freeze` refuse, and a new set version is needed
(`freeze --new-version`), whose scores are never compared with the old one's.
A case added after the freeze goes to dev. Prompts are tuned on dev only.
"""

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.synthetic_set import CASES_DIR, load_cases  # noqa: E402

MANIFEST = CASES_DIR / "manifest.json"
SEED = 20261029
FIRST_VERSION = "ev1s-v1"


class FrozenSplitError(RuntimeError):
    pass


def label_hash(case: dict) -> str:
    labeled = dict(case["labels"], language=case["language"])
    return hashlib.sha256(json.dumps(labeled, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def compute_split(cases: list[dict], seed: int = SEED) -> dict[str, str]:
    groups = defaultdict(list)
    for case in cases:
        groups[(case["labels"]["email_class"], case["language"])].append(case["id"])
    split = {}
    for key in sorted(groups):
        ids = sorted(groups[key])
        random.Random(f"{seed}:{key[0]}:{key[1]}").shuffle(ids)
        dev = round(len(ids) / 3)
        split.update({case_id: ("dev" if i < dev else "test") for i, case_id in enumerate(ids)})
    return split


def load_manifest(path: Path = MANIFEST) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def current_split(cases: list[dict], path: Path = MANIFEST) -> tuple[str, dict[str, str]]:
    """The set version and each case's split: the frozen one if there is a
    manifest (cases added since go to dev), else the computed one, unfrozen."""
    manifest = load_manifest(path)
    if manifest is None:
        return f"{FIRST_VERSION}-unfrozen", compute_split(cases)
    frozen = {case_id: entry["split"] for case_id, entry in manifest["cases"].items()}
    return manifest["set_version"], {case["id"]: frozen.get(case["id"], "dev") for case in cases}


def freeze(cases: list[dict], path: Path = MANIFEST, *, new_version: str | None = None, today: str | None = None) -> dict:
    manifest = load_manifest(path)
    hashes = {case["id"]: label_hash(case) for case in cases}
    if manifest is None or new_version:
        version = new_version or FIRST_VERSION
        split = compute_split(cases)
    else:
        version = manifest["set_version"]
        old_test = {i: e["label_sha256"] for i, e in manifest["cases"].items() if e["split"] == "test"}
        now_test = {i: hashes[i] for i in old_test if i in hashes}
        if now_test != old_test:
            changed = sorted(set(old_test) ^ set(now_test) | {i for i in now_test if now_test[i] != old_test[i]})
            raise FrozenSplitError(f"the frozen test split of {version} changed ({len(changed)} cases); freeze a new version")
        split = {case["id"]: manifest["cases"].get(case["id"], {}).get("split", "dev") for case in cases}
    manifest = {
        "set_version": version,
        "seed": SEED,
        "frozen_on": today or datetime.now(UTC).date().isoformat(),
        "cases": {i: {"split": split[i], "label_sha256": hashes[i]} for i in sorted(hashes)},
    }
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def counts(cases: list[dict], split: dict[str, str]) -> dict:
    by = Counter((split[c["id"]], c["labels"]["email_class"], c["language"]) for c in cases)
    return {f"{s}/{name}/{lang}": n for (s, name, lang), n in sorted(by.items())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EV-1s's dev/test split.")
    parser.add_argument("action", choices=("show", "freeze"))
    parser.add_argument("--new-version", help="freeze a new set version (the test split may change)")
    args = parser.parse_args(argv)
    cases = load_cases()
    if args.action == "freeze":
        try:
            manifest = freeze(cases, new_version=args.new_version)
        except FrozenSplitError as error:
            print(error)
            return 1
        print(f"Frozen {manifest['set_version']} in {MANIFEST.relative_to(ROOT)}")
    version, split = current_split(cases)
    print(version, Counter(split.values()))
    for key, n in counts(cases, split).items():
        print(f"  {key}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

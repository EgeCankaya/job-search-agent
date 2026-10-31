"""EV-3 (OQ-47 (g)): the chat suite, 40 questions over a fictional tracker.

`eval/chat/tracker.json` holds the tracker (17 rows, a fixed "today");
`eval/chat/questions.json` holds the questions, 20 Turkish and 20 English.
Each question lists the `list_applications` calls that answer it (one or
more accepted sets, each a list of argument objects; an empty list means no
call) and the facts the answer must contain (each fact a list of accepted
spellings).

M3 is scored here, in code: a question is right when the set of calls the
model made equals one accepted set, with the company compared after TRK-1
normalization and repeated identical calls counted once. A call that adds a
filter the question itself states ("still open", "my interview with") is an
accepted set too; c05, c18, c25, c36 and c40 gained theirs on 2026-10-29,
after the first recording, which scored 75% under the narrower sets. The answer check
looks for each fact at the start of a word of the normalized answer, so a
Turkish suffix ("Copperleaf'i") still matches.
"""

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_DIR = ROOT / "eval" / "chat"
LANGUAGES = ("tr", "en")


class SuiteError(ValueError):
    pass


def load_tracker(directory: Path = CHAT_DIR) -> tuple[date, list[dict]]:
    from inbox_agent.tracker import STATUSES

    data = json.loads((directory / "tracker.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(data["today"])
    for row in data["rows"]:
        if row["status"] not in STATUSES or date.fromisoformat(row["last_contact"]) > today:
            raise SuiteError(f"tracker row {row['company']!r}: check status and last_contact")
        if ("key_date" in row) != ("key_date_type" in row):
            raise SuiteError(f"tracker row {row['company']!r}: key_date and key_date_type go together")
    return today, data["rows"]


def load_questions(directory: Path = CHAT_DIR) -> list[dict]:
    from inbox_agent.tools import LIST_APPLICATIONS, ToolInputError, validate

    questions = json.loads((directory / "questions.json").read_text(encoding="utf-8"))
    for question in questions:
        problems = [
            "language" if question["language"] not in LANGUAGES else "",
            "question" if not question["question"].strip() else "",
            "expected_calls" if not question["expected_calls"] or not all(isinstance(s, list) for s in question["expected_calls"]) else "",
            "facts" if not question["facts"] or not all(isinstance(f, list) and f for f in question["facts"]) else "",
        ]
        for accepted in question["expected_calls"]:
            for call in accepted:
                try:
                    validate(LIST_APPLICATIONS, call)
                except ToolInputError:
                    problems.append("call")
        if any(problems):
            raise SuiteError(f"{question['id']}: check {', '.join(p for p in problems if p)}")
    if len({q["id"] for q in questions}) != len(questions):
        raise SuiteError("question IDs repeat")
    return questions


def seed_tracker(table, rows: list[dict]):
    """The fictional rows in a tracker over `table` (a fake), keyed as TRK-1 keys them."""
    from inbox_agent.matching import company_key, role_key
    from inbox_agent.tracker import TrackerTable

    tracker = TrackerTable(table)
    for row in rows:
        tracker.put({**row, "company_key": company_key(row["company"], {}), "role_key": role_key(row["role"])})
    return tracker


def _normalized(call: dict) -> tuple:
    from inbox_agent.matching import company_key

    items = dict(call)
    if isinstance(items.get("company"), str):
        try:
            items["company"] = company_key(items["company"], {})
        except ValueError:
            pass
    return tuple(sorted((key, json.dumps(value)) for key, value in items.items() if value is not None))


def calls_match(question: dict, calls: list[dict]) -> bool:
    """M3: the model's calls, as a set, equal one accepted set."""
    made = {_normalized(call) for call in calls}
    return any(made == {_normalized(call) for call in accepted} for accepted in question["expected_calls"])


def _words(text: str) -> str:
    from inbox_agent.matching import normalize_text

    return f" {normalize_text(text)}"


def facts_found(question: dict, answer: str) -> list[bool]:
    """For each fact: does any of its spellings start a word of the answer."""
    text = _words(answer)
    return [any(_words(spelling) in text for spelling in fact) for fact in question["facts"]]

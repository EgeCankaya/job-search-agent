"""EV-1, the labeled set (OQ-47 (d), (e)): where it lives on the Mac and how
its files are read and written.

The raw emails stay on the Mac only (NFR-PRIV-6), in `EVAL_DATA_DIR`
(default `~/inbox-agent-eval`), never in the repo:

    emails/<eval id>.eml   the raw message, as Gmail's format=raw returns it
    index.jsonl            one line per email: eval ID, Gmail IDs, receipt
                           time, found_by (query or manual), window
    labels.jsonl           one line per label; the last line for an eval ID wins

Eval IDs (`ev1-0001`) are what scores and CI see. The Gmail IDs, and the
mapping between the two, never leave this directory (OQ-47 (c)).
"""

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from inbox_agent.classes import EmailClass
from inbox_agent.messages import FetchedEmail, parse_message

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_ENV = "EVAL_DATA_DIR"
DEFAULT_DATA_DIR = Path.home() / "inbox-agent-eval"
EVAL_ID_PREFIX = "ev1-"
FOUND_BY = ("query", "manual")
LANGUAGES = ("tr", "en")
# Company and role are names, as in the classifier (classify.FIELD_LIMIT).
NAME_LIMIT = 100
NOTE_LIMIT = 500
_EVAL_ID = re.compile(r"^ev1-\d{4,}$")
_KEY_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")


class LabelError(ValueError):
    """A label that can't be stored. The message names the field, never its value."""


def data_dir(environ=os.environ) -> Path:
    """The set's directory. Refused inside the repo, so no email can be committed."""
    path = Path(environ.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR).expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError(f"{DATA_DIR_ENV} must be outside the repo")
    return path


@dataclass(frozen=True)
class Label:
    """What I label for one email (CLS-1, CLS-2): the class after precedence,
    the yes/no question, company, role, the key date, and the language."""

    eval_id: str
    email_class: str
    asks_by_date: bool
    company: str | None
    role: str | None
    key_date: str | None  # YYYY-MM-DD, or YYYY-MM-DDTHH:MM when the email gives a time
    language: str
    note: str = ""
    labeled_at: str = ""

    def validate(self) -> "Label":
        if not _EVAL_ID.match(self.eval_id):
            raise LabelError("eval_id is not an eval ID")
        if self.email_class not in set(EmailClass):
            raise LabelError("email_class is not one of the six classes")
        if not isinstance(self.asks_by_date, bool):
            raise LabelError("asks_by_date must be true or false")
        for name in ("company", "role"):
            value = getattr(self, name)
            if value is not None and (not value.strip() or len(value) > NAME_LIMIT):
                raise LabelError(f"{name} must be empty or 1 to {NAME_LIMIT} characters")
        if self.key_date is not None:
            if not _KEY_DATE.match(self.key_date):
                raise LabelError("key_date must be YYYY-MM-DD or YYYY-MM-DDTHH:MM")
            try:
                (datetime if "T" in self.key_date else date).fromisoformat(self.key_date)
            except ValueError:
                raise LabelError("key_date is not a real date") from None
        if self.language not in LANGUAGES:
            raise LabelError("language must be tr or en")
        if len(self.note) > NOTE_LIMIT:
            raise LabelError(f"note must be at most {NOTE_LIMIT} characters")
        return self


def _clean_name(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text or None


def make_label(*, eval_id, email_class, asks_by_date, company, role, key_date, language, note="", now=None) -> Label:
    """A validated label from the labeling page's fields; blank fields become None."""
    return Label(
        eval_id=eval_id,
        email_class=email_class,
        asks_by_date=asks_by_date,
        company=_clean_name(company),
        role=_clean_name(role),
        key_date=(key_date or "").strip() or None,
        language=language,
        note=(note or "").strip(),
        labeled_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
    ).validate()


@dataclass(frozen=True)
class IndexEntry:
    eval_id: str
    gmail_id: str
    thread_id: str
    received_at: str  # ISO, UTC
    found_by: str
    window: str  # "<after>..<before>", the fetch window it came from
    fetched_at: str


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


class LabeledSet:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.emails = root / "emails"
        self.index_path = root / "index.jsonl"
        self.labels_path = root / "labels.jsonl"

    def ensure(self) -> "LabeledSet":
        for path in (self.root, self.emails):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        return self

    # The index

    def index(self) -> dict[str, IndexEntry]:
        return {entry["eval_id"]: IndexEntry(**entry) for entry in _read_jsonl(self.index_path)}

    def by_gmail_id(self) -> dict[str, IndexEntry]:
        return {entry.gmail_id: entry for entry in self.index().values()}

    def _next_eval_id(self, index: dict[str, IndexEntry]) -> str:
        numbers = [int(eval_id.removeprefix(EVAL_ID_PREFIX)) for eval_id in index]
        return f"{EVAL_ID_PREFIX}{max(numbers, default=0) + 1:04d}"

    def add_email(
        self, raw: bytes, *, gmail_id: str, thread_id: str, received_at: datetime, found_by: str, window: str, now=None
    ) -> str:
        """Store one fetched email and return its eval ID. A Gmail ID already
        in the set is not stored twice."""
        if found_by not in FOUND_BY:
            raise ValueError("found_by must be query or manual")
        index = self.index()
        existing = {entry.gmail_id: entry.eval_id for entry in index.values()}
        if gmail_id in existing:
            return existing[gmail_id]
        eval_id = self._next_eval_id(index)
        path = self.emails / f"{eval_id}.eml"
        path.write_bytes(raw)
        path.chmod(0o600)
        entry = IndexEntry(
            eval_id=eval_id,
            gmail_id=gmail_id,
            thread_id=thread_id,
            received_at=received_at.astimezone(UTC).isoformat(timespec="seconds"),
            found_by=found_by,
            window=window,
            fetched_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        )
        _append_jsonl(self.index_path, asdict(entry))
        return eval_id

    def email(self, eval_id: str) -> FetchedEmail:
        entry = self.index()[eval_id]
        raw = (self.emails / f"{eval_id}.eml").read_bytes()
        return parse_message(raw, message_id=eval_id, received_at=datetime.fromisoformat(entry.received_at))

    # The labels

    def labels(self) -> dict[str, Label]:
        """The latest label per eval ID."""
        return {record["eval_id"]: Label(**record) for record in _read_jsonl(self.labels_path)}

    def save_label(self, label: Label) -> None:
        label.validate()
        if label.eval_id not in self.index():
            raise LabelError("eval_id is not in the set")
        _append_jsonl(self.labels_path, asdict(label))

    def progress(self) -> dict:
        """Counts only: emails, labeled, and labeled by class and language."""
        index, labels = self.index(), self.labels()
        by_class: dict[str, dict[str, int]] = {}
        for label in labels.values():
            by_class.setdefault(label.email_class, {}).setdefault(label.language, 0)
            by_class[label.email_class][label.language] += 1
        return {
            "emails": len(index),
            "labeled": len(labels),
            "by_found_by": {f: sum(e.found_by == f for e in index.values()) for f in FOUND_BY},
            "by_class": by_class,
        }

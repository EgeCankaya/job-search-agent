"""The memory interface (MODE-1, MEM-1, MEM-2, OQ-44 h, i).

- Short-term (MEM-1): the turns of one Ask-page conversation, so "and
  Copperleaf?" follows the question before it. Events expire after 30 days.
- Long-term (MEM-2): my preferences (tone, roles, companies), extracted in
  the background from the Ask page's turns; and one summary per company,
  kept from a short event the batch path records after each tracker update,
  which carries the class, status and dates, never email text (NFR-PRIV-3).

Cloud mode: AgentCore Memory (a user-preference strategy over the Ask
sessions, a summary strategy over one session per company). MacBook mode:
SQLite on the Mac, filled with the long-term records exported before the
switch (MODE-3); nothing is extracted locally. Tests use NoMemory or the
SQLite store.

Memory is graph code, not a tool: the chat path's tools stay
`list_applications` only (OQ-42 h).
"""

import os
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from inbox_agent import settings
from inbox_agent.mode import agent_mode

ACTOR = "owner"
PREFERENCES_NAMESPACE = f"/preferences/{ACTOR}"
SUMMARIES_NAMESPACE = f"/summaries/{ACTOR}"
EVENT_EXPIRY = timedelta(days=30)  # MEM-2, OQ-30
TURNS_KEPT = 6  # the last three questions and answers
TEXT_LIMIT = 2000
MEMORY_ID_ENV = "MEMORY_ID"
LOCAL_MEMORY_ENV = "LOCAL_MEMORY_PATH"
DEFAULT_LOCAL_PATH = Path(__file__).resolve().parents[2] / "data" / "memory.sqlite"
_SESSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")


def company_session(company_key: str) -> str:
    """One session per company. Keys hold only [0-9a-z ] (matching.py)."""
    return "company-" + "-".join(company_key.split())


def summary_namespace(company_key: str) -> str:
    return f"{SUMMARIES_NAMESPACE}/{company_session(company_key)}"


def check_session(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SESSION.match(session_id):
        raise ValueError("not a session ID")
    return session_id


class Memory(Protocol):
    def context(self, session_id: str) -> list[tuple[str, str]]:
        """The latest turns of this conversation, oldest first: (role, text)."""
        ...

    def record(self, session_id: str, role: str, text: str) -> None:
        """One turn of the conversation: role is "user" or "assistant"."""
        ...

    def preferences(self) -> list[str]:
        """My stored preferences, as short sentences."""
        ...

    def company_summary(self, company_key: str) -> str | None:
        ...

    def record_company_event(self, company_key: str, text: str) -> None:
        """A tracker update for this company, as fields, never email text."""
        ...


class NoMemory:
    def context(self, session_id: str) -> list[tuple[str, str]]:
        return []

    def record(self, session_id: str, role: str, text: str) -> None:
        return None

    def preferences(self) -> list[str]:
        return []

    def company_summary(self, company_key: str) -> str | None:
        return None

    def record_company_event(self, company_key: str, text: str) -> None:
        return None


ROLES = {"user": "USER", "assistant": "ASSISTANT"}


class AgentCoreMemory:
    """AgentCore Memory's data plane, as the Runtime role."""

    def __init__(self, client, memory_id: str, now=lambda: datetime.now(UTC)) -> None:
        self._client = client
        self._memory_id = memory_id
        self._now = now

    def context(self, session_id: str) -> list[tuple[str, str]]:
        response = self._client.list_events(
            memoryId=self._memory_id,
            actorId=ACTOR,
            sessionId=check_session(session_id),
            includePayloads=True,
            maxResults=TURNS_KEPT,
        )
        turns = []
        events = sorted(response.get("events", []), key=lambda event: event["eventTimestamp"])
        for event in events[-TURNS_KEPT:]:
            for item in event.get("payload", []):
                conversational = item.get("conversational")
                if conversational and conversational.get("role") in ("USER", "ASSISTANT"):
                    role = "user" if conversational["role"] == "USER" else "assistant"
                    turns.append((role, conversational["content"].get("text", "")))
        return turns[-TURNS_KEPT:]

    def _event(self, session_id: str, role: str, text: str) -> None:
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=ACTOR,
            sessionId=check_session(session_id),
            eventTimestamp=self._now(),
            payload=[{"conversational": {"content": {"text": text[:TEXT_LIMIT]}, "role": role}}],
        )

    def record(self, session_id: str, role: str, text: str) -> None:
        self._event(session_id, ROLES[role], text)

    def _records(self, namespace: str, query: str, top: int) -> list[str]:
        response = self._client.retrieve_memory_records(
            memoryId=self._memory_id, namespace=namespace, searchCriteria={"searchQuery": query, "topK": top}
        )
        return [r["content"]["text"] for r in response.get("memoryRecordSummaries", []) if r.get("content", {}).get("text")]

    def preferences(self) -> list[str]:
        return self._records(PREFERENCES_NAMESPACE, "follow-up email tone and style preferences", 5)

    def company_summary(self, company_key: str) -> str | None:
        found = self._records(summary_namespace(company_key), "the application's history and status", 1)
        return found[0] if found else None

    def record_company_event(self, company_key: str, text: str) -> None:
        # OTHER: an update from the agent, not a turn of a conversation.
        self._event(company_session(company_key), "OTHER", text)


class SqliteMemory:
    """MacBook mode (MEM-1, OQ-44 i): a file on the Mac, standard library only."""

    def __init__(self, path: Path | str, now=time.time) -> None:
        self._path = str(path)
        self._now = now
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS turns (session_id TEXT, role TEXT, text TEXT, at REAL);
            CREATE TABLE IF NOT EXISTS records (record_id TEXT PRIMARY KEY, namespace TEXT, text TEXT, created_at TEXT);
            CREATE INDEX IF NOT EXISTS turns_by_session ON turns (session_id, at);
            """
        )
        # Events expire after 30 days, as in AgentCore (MEM-2).
        self._db.execute("DELETE FROM turns WHERE at < ?", (self._now() - EVENT_EXPIRY.total_seconds(),))
        self._db.commit()

    def context(self, session_id: str) -> list[tuple[str, str]]:
        rows = self._db.execute(
            "SELECT role, text FROM turns WHERE session_id = ? AND at >= ? ORDER BY at DESC, rowid DESC LIMIT ?",
            (check_session(session_id), self._now() - EVENT_EXPIRY.total_seconds(), TURNS_KEPT),
        ).fetchall()
        return [(role, text) for role, text in reversed(rows) if role in ROLES]

    def record(self, session_id: str, role: str, text: str) -> None:
        if role not in ROLES:
            raise ValueError("role must be user or assistant")
        self._db.execute(
            "INSERT INTO turns VALUES (?, ?, ?, ?)", (check_session(session_id), role, text[:TEXT_LIMIT], self._now())
        )
        self._db.commit()

    def preferences(self) -> list[str]:
        rows = self._db.execute(
            "SELECT text FROM records WHERE namespace LIKE ? ORDER BY created_at DESC LIMIT 5", (PREFERENCES_NAMESPACE + "%",)
        )
        return [text for (text,) in rows]

    def company_summary(self, company_key: str) -> str | None:
        row = self._db.execute(
            "SELECT text FROM records WHERE namespace LIKE ? ORDER BY created_at DESC LIMIT 1",
            (summary_namespace(company_key) + "%",),
        ).fetchone()
        return row[0] if row else None

    def record_company_event(self, company_key: str, text: str) -> None:
        # No extraction runs locally; summaries come from the export (MODE-3).
        return None

    def import_records(self, records: list[dict]) -> int:
        """Long-term records from `export_memory` (MODE-3), replacing the
        last export. Returns the number of records the store holds."""
        self._db.execute("DELETE FROM records")
        self._db.executemany(
            "INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
            [(r["record_id"], r["namespace"], r["text"], r["created_at"]) for r in records],
        )
        self._db.commit()
        return self.record_count()

    def record_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]


def memory_for(environ=os.environ) -> Memory:
    mode = agent_mode(environ)  # an unknown mode fails here too
    if mode == "cloud":
        memory_id = environ.get(MEMORY_ID_ENV, "").strip()
        if not memory_id:
            return NoMemory()  # no Memory resource configured (tests, a bare worker)
        import boto3

        return AgentCoreMemory(boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION), memory_id)
    return SqliteMemory(environ.get(LOCAL_MEMORY_ENV) or DEFAULT_LOCAL_PATH)

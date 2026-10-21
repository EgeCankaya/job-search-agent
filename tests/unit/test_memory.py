"""Memory (MEM-1, MEM-2, MODE-3, OQ-44 h, i): AgentCore Memory with a fake
client, the local SQLite store, the chat path's two-turn follow-up, the
batch path's company events, and the export. Fictional companies only."""

from datetime import UTC, date, datetime, timedelta

import pytest

from inbox_agent.batch_path import company_event
from inbox_agent.chat_path import answer
from inbox_agent.classes import EmailClass
from inbox_agent.classify import Classification
from inbox_agent.export_memory import export, find_memory_id
from inbox_agent.memory import (
    AgentCoreMemory,
    SqliteMemory,
    company_session,
    summary_namespace,
)

SESSION = "inbox-agent-ask-0b7f5e1c-3a52-4b8e-9d0e-6f1d2c3b4a59"
NOW = datetime(2026, 10, 21, 7, 0, tzinfo=UTC)


class FakeMemoryClient:
    def __init__(self, records=None):
        self.events = []
        self.records = records or {}
        self.retrievals = []

    def create_event(self, **kwargs):
        self.events.append(kwargs)
        return {"event": {"eventId": f"e{len(self.events)}"}}

    def list_events(self, memoryId, actorId, sessionId, includePayloads, maxResults):
        events = [e for e in self.events if e["sessionId"] == sessionId]
        return {"events": [{"eventTimestamp": e["eventTimestamp"], "payload": e["payload"]} for e in events][-maxResults:]}

    def retrieve_memory_records(self, memoryId, namespace, searchCriteria):
        self.retrievals.append((namespace, searchCriteria))
        found = [r for ns, r in self.records.items() if ns.startswith(namespace)]
        return {"memoryRecordSummaries": [{"content": {"text": text}} for text in found][: searchCriteria["topK"]]}


def test_turns_go_to_agentcore_as_conversation_events_and_come_back_in_order():
    client = FakeMemoryClient()
    clock = iter(NOW + timedelta(seconds=n) for n in range(10))
    memory = AgentCoreMemory(client, "inbox_agent_memory-abc", now=lambda: next(clock))
    memory.record(SESSION, "user", "Kuzey'de ne açık?")
    memory.record(SESSION, "assistant", "Backend Engineer: interviewing.")
    [first, _] = client.events
    assert first["actorId"] == "owner" and first["sessionId"] == SESSION
    assert first["payload"] == [{"conversational": {"content": {"text": "Kuzey'de ne açık?"}, "role": "USER"}}]
    assert memory.context(SESSION) == [("user", "Kuzey'de ne açık?"), ("assistant", "Backend Engineer: interviewing.")]


def test_a_company_event_goes_to_that_companys_session_as_other():
    client = FakeMemoryClient()
    AgentCoreMemory(client, "m-1", now=lambda: NOW).record_company_event("kuzey yazilim", "2026-10-19: offer email; status now offer.")
    [event] = client.events
    assert event["sessionId"] == company_session("kuzey yazilim") == "company-kuzey-yazilim"
    assert event["payload"][0]["conversational"]["role"] == "OTHER"


def test_preferences_and_summaries_are_read_from_their_namespaces():
    client = FakeMemoryClient(
        {"/preferences/owner/p1": "Prefers short, formal follow-ups", summary_namespace("kuzey yazilim") + "/r1": "Interview done; waiting since 3 October."}
    )
    memory = AgentCoreMemory(client, "m-1")
    assert memory.preferences() == ["Prefers short, formal follow-ups"]
    assert memory.company_summary("kuzey yazilim") == "Interview done; waiting since 3 October."
    assert memory.company_summary("guney savunma") is None


def test_session_ids_are_checked_before_any_call():
    with pytest.raises(ValueError):
        AgentCoreMemory(FakeMemoryClient(), "m-1").context("../other actor")


# The local store (OQ-44 i)


def test_the_local_store_keeps_turns_per_session_and_expires_them_after_30_days(tmp_path):
    now = [NOW.timestamp()]
    memory = SqliteMemory(tmp_path / "memory.sqlite", now=lambda: now[0])
    memory.record(SESSION, "user", "Kuzey?")
    memory.record("inbox-agent-ask-other", "user", "Güney?")
    memory.record(SESSION, "assistant", "Interviewing.")
    assert memory.context(SESSION) == [("user", "Kuzey?"), ("assistant", "Interviewing.")]
    now[0] += 31 * 86400
    assert memory.context(SESSION) == []


def test_the_local_store_answers_from_exported_records(tmp_path):
    memory = SqliteMemory(tmp_path / "memory.sqlite")
    held = memory.import_records(
        [
            {"record_id": "r1", "namespace": "/preferences/owner", "text": "Prefers short, formal follow-ups", "created_at": "2026-10-20T10:00:00+00:00"},
            {"record_id": "r2", "namespace": summary_namespace("kuzey yazilim"), "text": "Waiting since 3 October.", "created_at": "2026-10-20T10:00:00+00:00"},
        ]
    )
    assert held == 2
    assert memory.preferences() == ["Prefers short, formal follow-ups"]
    assert memory.company_summary("kuzey yazilim") == "Waiting since 3 October."
    # A later export replaces the earlier one.
    assert memory.import_records([]) == 0


# The chat path: "and Copperleaf?" (MEM-1)


class ScriptedGraph:
    def __init__(self):
        self.seen = []

    def invoke(self, state, config):
        from langchain_core.messages import AIMessage

        self.seen.append(state["messages"])
        last = state["messages"][-1].content
        reply = "Güney Savunma: Platform Engineer, applied." if "Güney" in last else "Kuzey Yazılım: Backend Engineer, interviewing."
        return {"messages": state["messages"] + [AIMessage(content=reply)]}


def test_a_follow_up_question_sees_the_turns_before_it(tmp_path):
    memory, graph = SqliteMemory(tmp_path / "m.sqlite"), ScriptedGraph()
    answer(graph, "What is still open with Kuzey Yazılım?", today=date(2026, 10, 21), memory=memory, session_id=SESSION)
    answer(graph, "and Güney?", today=date(2026, 10, 21), memory=memory, session_id=SESSION)
    second = [m.content for m in graph.seen[1]]
    assert second[1:] == [
        "What is still open with Kuzey Yazılım?",
        "Kuzey Yazılım: Backend Engineer, interviewing.",
        "and Güney?",
    ]
    assert len(memory.context(SESSION)) == 4


def test_without_a_session_nothing_is_remembered(tmp_path):
    memory, graph = SqliteMemory(tmp_path / "m.sqlite"), ScriptedGraph()
    answer(graph, "Kuzey?", today=date(2026, 10, 21), memory=memory, session_id=None)
    assert memory.context(SESSION) == []


# The batch path's company event (TRK-1, NFR-PRIV-3)


def test_a_company_event_holds_fields_only():
    result = Classification(email_class=EmailClass.INTERVIEW_INVITE, asks_by_date=True, candidates=(EmailClass.INTERVIEW_INVITE,), company="Kuzey", role="Backend Engineer")
    line = company_event(date(2026, 10, 19), result, {"role": "Backend Engineer", "status": "interviewing"}, datetime(2026, 11, 5, 14, 0))
    assert line == "2026-10-19: interview invite email about Backend Engineer; status now interviewing; key date 2026-11-05T14:00:00."


# The export (MODE-3, exit check 4)


class FakeExportClient:
    """AgentCore matches a namespace exactly, not as a prefix: on 2026-10-21
    `/summaries/owner` listed nothing while `/summaries/owner/<session>`
    held each company's summary."""

    def __init__(self, pages, session_pages=None):
        self.pages = pages  # namespace -> list of pages
        self.session_pages = session_pages or [[]]  # ListSessions pages of session IDs

    def list_memory_records(self, memoryId, namespace, maxResults, nextToken=None):
        pages = self.pages.get(namespace.rstrip("/"), [[]])
        return self._page(pages, nextToken, "memoryRecordSummaries")

    def list_sessions(self, memoryId, actorId, maxResults, nextToken=None):
        assert actorId == "owner"
        pages = [[{"sessionId": s, "actorId": actorId} for s in page] for page in self.session_pages]
        return self._page(pages, nextToken, "sessionSummaries")

    @staticmethod
    def _page(pages, next_token, key):
        index = int(next_token or 0)
        page = {key: pages[index]}
        if index + 1 < len(pages):
            page["nextToken"] = str(index + 1)
        return page


def summary(record_id, namespace, text):
    return {"memoryRecordId": record_id, "namespaces": [namespace], "content": {"text": text}, "createdAt": NOW}


def test_the_export_reads_every_session_s_summaries_and_the_counts_match(tmp_path):
    client = FakeExportClient(
        {
            "/preferences/owner": [[summary("p1", "/preferences/owner", "Short, formal follow-ups")]],
            "/summaries/owner/company-kuzey": [
                [summary("s1", "/summaries/owner/company-kuzey", "Interview done.")],
                [summary("s3", "/summaries/owner/company-kuzey", "Offer expected.")],
            ],
            "/summaries/owner/company-guney": [[summary("s2", "/summaries/owner/company-guney", "Applied 20 September.")]],
        },
        session_pages=[["company-kuzey", "inbox-agent-ask-1"], ["company-guney"]],
    )
    listed, held, records = export(client, "inbox_agent_memory-abc", SqliteMemory(tmp_path / "m.sqlite"))
    assert (listed, held) == (4, 4)
    assert {r["text"] for r in records} == {"Short, formal follow-ups", "Interview done.", "Offer expected.", "Applied 20 September."}


def test_the_export_finds_the_one_memory():
    class Control:
        def list_memories(self, **kwargs):
            return {"memories": [{"id": "inbox_agent_memory-x1Y2"}, {"id": "other_memory-9"}]}

    assert find_memory_id(Control()) == "inbox_agent_memory-x1Y2"

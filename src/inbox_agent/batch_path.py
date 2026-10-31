"""The agent's batch path (OQ-42 b): one new email, through a fixed graph.

    check handled -> read_email -> classify -> upsert_application -> alert -> mark handled

The edges are code, not the model's choice: the model only classifies and
extracts (CLS-1, CLS-2), so a hostile email can't add, drop or reorder a
tool call (NFR-SEC-2). The tracker write and the alert go through the tools
(TOOL-1), so the Gateway, and from week 5 Cedar, sees them. The
processed-email log is the graph's own bookkeeping, reached directly (ING-2):

- a message already marked handled stops at the first node;
- the tool skips an email the row already lists, so a retry writes once;
- the alert is claimed in the log before `notify_owner`, and released if the
  call fails, so a retry can alert, and at most 1 alert goes out.

These were the steps of the week 2 queue reader, which this replaced at the
cutover (2026-10-19).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TypedDict

from inbox_agent import settings
from inbox_agent.classify import Classification, classify
from inbox_agent.matching import company_key
from inbox_agent.memory import Memory, NoMemory
from inbox_agent.model import Model
from inbox_agent.processed_log import ProcessedLog
from inbox_agent.tools import Tools, parse_date_or_datetime
from inbox_agent.tracker_update import KEY_DATE_TYPES, status_for_class
from inbox_agent.trim import TrimmedEmail

# The alert names a company even when the email didn't.
UNNAMED_COMPANY = "Company not named"


@dataclass
class BatchDeps:
    tools: Tools
    model: Model
    log: ProcessedLog
    now: Callable[[], datetime]
    memory: Memory = field(default_factory=NoMemory)


class BatchState(TypedDict, total=False):
    message_id: str
    duplicate: bool
    email: dict  # read_email's result: email text, kept out of logs
    classification: Classification
    key_date: date | datetime | None
    company: str | None
    role: str | None
    row_written: bool
    row_created: bool
    alerted: bool


def _names_a_company(company: str | None) -> bool:
    # An alias always maps to a non-empty key, so emptiness doesn't depend on
    # the alias list the tool reads.
    if not company:
        return False
    try:
        company_key(company, {})
    except ValueError:
        return False
    return True


def _not_the_arrival(extracted: date | datetime | None, received_at: str) -> date | datetime | None:
    """CLS-2: never a guessed date. The model sometimes copies the quoted
    Date line, which is when the email arrived, when the text gives no
    interview time or deadline (EV-1s, 2026-10-29). A date-time within a
    minute of the arrival is that, not a key date."""
    if not isinstance(extracted, datetime):
        return extracted
    received = datetime.fromisoformat(received_at)
    compared = extracted if extracted.tzinfo else extracted.replace(tzinfo=received.tzinfo)
    return None if abs((compared - received).total_seconds()) < 60 else extracted


def company_event(received_on: date, result: Classification, row: dict, key_date: date | datetime | None) -> str:
    """One line for the company's summary: the class, the row's status and
    dates. The extracted role is the only text from the email (NFR-PRIV-3)."""
    line = f"{received_on.isoformat()}: {result.email_class.value.replace('_', ' ')} email"
    if row.get("role"):
        line += f" about {row['role']}"
    line += f"; status now {row['status']}"
    if key_date is not None:
        line += f"; key date {key_date.isoformat()}"
    return line + "."


def build_batch_graph(deps: BatchDeps):
    from langgraph.graph import END, START, StateGraph

    def check_handled(state: BatchState) -> BatchState:
        return {"duplicate": deps.log.is_handled(state["message_id"])}

    def read_email(state: BatchState) -> BatchState:
        return {"email": deps.tools.call("read_email", {"message_id": state["message_id"]})}

    def classify_email(state: BatchState) -> BatchState:
        email = state["email"]
        trimmed = TrimmedEmail(
            subject=email["subject"], sender=email["sender"], date=email["received_at"], body=email["text"]
        )
        result = classify(trimmed, deps.model)
        invite_start = email.get("calendar_start")
        # The invite's start wins over a date from the body (CLS-2).
        when = parse_date_or_datetime(invite_start) if invite_start else _not_the_arrival(result.date, email["received_at"])
        company = result.company if _names_a_company(result.company) else None
        return {"classification": result, "key_date": when, "company": company, "role": result.role}

    def update_tracker(state: BatchState) -> BatchState:
        if not state["company"]:
            return {"row_written": False, "row_created": False}
        result = state["classification"]
        received_on = datetime.fromisoformat(state["email"]["received_at"]).astimezone(settings.OWNER_TIMEZONE).date()
        arguments = {
            "company": state["company"],
            "last_contact": received_on.isoformat(),
            "message_id": state["message_id"],
        }
        if state["role"]:
            arguments["role"] = state["role"]
        status = status_for_class(result.email_class)
        if status is not None:
            arguments["status"] = status
        key_date_type = KEY_DATE_TYPES.get(result.email_class)
        if state["key_date"] is not None and key_date_type is not None:
            arguments["key_date"] = state["key_date"].isoformat()
            arguments["key_date_type"] = key_date_type
        row = deps.tools.call("upsert_application", arguments)
        if row["written"]:
            # TRK-1: the company's memory summary, from fields only (OQ-44 h).
            deps.memory.record_company_event(row["company_key"], company_event(received_on, result, row, state["key_date"]))
        # The alert names the row the email landed on (TRK-1).
        return {"company": row["company"], "role": row.get("role"), "row_written": row["written"], "row_created": row["created"]}

    def alert(state: BatchState) -> BatchState:
        result = state["classification"]
        if not (result.urgent or result.possible):
            return {"alerted": False}
        message_id = state["message_id"]
        kind = "possible" if result.possible else "urgent"
        if not deps.log.claim_alert(message_id, deps.now(), kind):
            return {"alerted": False}
        arguments = {
            "kind": kind,
            "email_class": result.email_class.value,
            "company": state["company"] or UNNAMED_COMPANY,
        }
        if state["role"]:
            arguments["role"] = state["role"]
        if state["key_date"] is not None:
            arguments["date"] = state["key_date"].isoformat()
        try:
            deps.tools.call("notify_owner", arguments)
        except Exception:
            deps.log.release_alert(message_id, deps.now())
            raise
        return {"alerted": True}

    def mark_handled(state: BatchState) -> BatchState:
        deps.log.mark_handled(state["message_id"], deps.now())
        return {}

    graph = StateGraph(BatchState)
    graph.add_node("check_handled", check_handled)
    graph.add_node("read_email", read_email)
    graph.add_node("classify", classify_email)
    graph.add_node("update_tracker", update_tracker)
    graph.add_node("alert", alert)
    graph.add_node("mark_handled", mark_handled)
    graph.add_edge(START, "check_handled")
    graph.add_conditional_edges("check_handled", lambda s: END if s["duplicate"] else "read_email", ["read_email", END])
    graph.add_edge("read_email", "classify")
    graph.add_edge("classify", "update_tracker")
    graph.add_edge("update_tracker", "alert")
    graph.add_edge("alert", "mark_handled")
    graph.add_edge("mark_handled", END)
    return graph.compile()


def handle_new_email(graph, message_id: str) -> dict:
    """The outcome, with IDs, the class and flags only (NFR-PRIV-3)."""
    state = graph.invoke({"message_id": message_id})
    if state.get("duplicate"):
        return {"message_id": message_id, "duplicate": True}
    return {
        "message_id": message_id,
        "duplicate": False,
        "email_class": state["classification"].email_class.value,
        "row_written": state["row_written"],
        "row_created": state["row_created"],
        "alerted": state["alerted"],
    }

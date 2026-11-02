"""The Mac app's three pages on fictional data, for the README screenshots
(OQ-47 (m)). Nothing here reaches AWS or Gmail.

    uv run --all-groups streamlit run mac_app/demo.py --server.port 8503

- Dashboard: the EV-3 tracker (eval/chat/tracker.json) and a few made-up
  alert claims, on the suite's fixed "today".
- Ask: the real chat graph over that tracker, answered from Nova's recorded
  EV-3 replies (eval/replies/ev3.json), so a suite question gets the answer
  Nova gave; any other question gets a note.
- Follow-ups: two fictional drafts; Approve and Skip are only remembered.

The pages are app.py's own; only the backend is the demo's.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from inbox_agent import app_backend, settings  # noqa: E402

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=settings.OWNER_TIMEZONE)
NOT_IN_SUITE = "This demo answers the chat suite's questions only (eval/chat/questions.json), from Nova's recorded replies."
DRAFTS = (
    app_backend.FollowUp(
        approval_id="d" * 32,
        company="Halcyon Systems",
        role="Backend Engineer",
        to_address="recruiting@halcyon.example.com",
        subject="Re: Your application to Halcyon Systems",
        language="en",
        text="Hello,\n\nI applied for the Backend Engineer role on 28 September 2026 and wanted to ask whether there is any news on my application.\n\nBest regards,",
        proposed_on="2026-10-26",
    ),
    app_backend.FollowUp(
        approval_id="e" * 32,
        company="Mavi Lojistik",
        role="Veri Analisti",
        to_address="",
        subject="Veri Analisti başvurum",
        language="tr",
        text="Merhaba,\n\n2 Ekim 2026 tarihinde Veri Analisti pozisyonu için yaptığım başvuruyla ilgili bir gelişme olup olmadığını sormak istedim.\n\nSaygılarımla,",
        proposed_on="2026-10-26",
    ),
)


class DemoBackend:
    mode = "cloud"

    def __init__(self) -> None:
        from eval.chat_suite import load_questions, load_tracker

        self.today, self.rows = load_tracker()
        self.questions = {q["question"].strip(): q for q in load_questions()}
        self.drafts = list(DRAFTS)
        self.decisions: list[tuple[str, str]] = []

    def dashboard(self):
        from inbox_agent.dashboard import build_dashboard

        # NOW is a Monday, and the page counts from Monday 00:00.
        claims = [(NOW - timedelta(hours=1), "urgent"), (NOW - timedelta(hours=3), "urgent"), (NOW - timedelta(hours=5), "possible")]
        return build_dashboard(self.rows, claims, now=NOW)

    def ask(self, session_id: str, question: str) -> str:
        from eval.replay import ReplayChat, ReplyStore, StaleReply, cloud_model_ids
        from eval.run_chat import ask

        suite_question = self.questions.get(question.strip())
        if suite_question is None:
            return NOT_IN_SUITE
        chat = ReplayChat(ReplyStore.load("ev3"), cloud_model_ids()["chat"])
        try:
            return ask(suite_question, chat, self.today, self.rows)["_answer"]
        except StaleReply:
            return NOT_IN_SUITE

    def follow_ups(self):
        return self.drafts

    def decide(self, draft, decision: str) -> None:
        self.decisions.append((draft.approval_id, decision))


def main() -> None:
    import streamlit as st

    app_backend.make_backend = DemoBackend
    st.set_page_config(page_title="Inbox agent (demo)", layout="wide")
    st.navigation(
        [
            st.Page("dashboard_page.py", title="Dashboard", default=True),
            st.Page("ask_page.py", title="Ask"),
            st.Page("followups_page.py", title="Follow-ups"),
        ]
    ).run()


main()

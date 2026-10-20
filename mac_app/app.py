"""The Mac app (OQ-40) on 127.0.0.1: Dashboard (DASH-1), Ask (CHT-1) and
Follow-ups (APR-2, APR-3).

Start it from the repo root, so Streamlit reads .streamlit/config.toml:

    uv run streamlit run mac_app/app.py

It uses your `aws login` session for every call (CHT-2). In MacBook mode
(`AGENT_MODE=local`), Ask goes to the local worker instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Inbox agent", layout="wide")

st.navigation(
    [
        st.Page("dashboard_page.py", title="Dashboard", default=True),
        st.Page("ask_page.py", title="Ask"),
        st.Page("followups_page.py", title="Follow-ups"),
    ]
).run()

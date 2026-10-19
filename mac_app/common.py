"""What both pages share: the backend for this browser session, and the
hint shown when AWS refuses (usually an expired `aws login`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

from inbox_agent import app_backend  # noqa: E402

SESSION_HINT = "Your AWS session may have expired: run `aws login`, then reload this page."


def backend():
    if "backend" not in st.session_state:
        st.session_state.backend = app_backend.make_backend()
        st.session_state.session_id = app_backend.new_session_id()
    return st.session_state.backend

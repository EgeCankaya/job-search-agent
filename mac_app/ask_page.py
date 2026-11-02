"""CHT-1: questions answered from the tracker by the agent's chat path."""

import streamlit as st
from common import SESSION_HINT, backend

from inbox_agent.app_backend import AskError


def answer(question: str) -> str:
    try:
        return backend().ask(st.session_state.session_id, question)
    except AskError as error:
        return f"No answer: {error}"
    except Exception as error:  # noqa: BLE001
        return f"No answer ({type(error).__name__}). {SESSION_HINT}"


st.title("Ask")
st.caption(
    "Questions about your applications, answered from the tracker. The agent remembers this conversation, "
    'so a follow-up like "and Brightpath?" works, and keeps the preferences you state (for example the tone of follow-ups).'
)
history = st.session_state.setdefault("history", [])
for role, text in history:
    with st.chat_message(role):
        st.markdown(text)

question = st.chat_input("What is still open with …?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Asking the agent…"):
            reply = answer(question)
        st.markdown(reply)
    history += [("user", question), ("assistant", reply)]

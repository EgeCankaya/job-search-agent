"""APR-2 and APR-3: each follow-up draft waiting for my decision, with
exactly two buttons. Approve or Skip goes to the agent queue with my
session; the draft reaches Gmail only after Approve, as a draft I send
myself."""

import streamlit as st
from common import SESSION_HINT, backend

LANGUAGES = {"tr": "Turkish", "en": "English"}

st.title("Follow-ups")
st.caption(
    "Drafts for applications that have been silent for 10+ days. Approve saves the text below as a Gmail draft; "
    "nothing is ever sent. Skip saves nothing. A draft with no decision expires after 7 days."
)

try:
    drafts = backend().follow_ups()
except Exception as error:  # noqa: BLE001
    st.error(f"Couldn't load the drafts ({type(error).__name__}). {SESSION_HINT}")
    st.stop()

sent = st.session_state.setdefault("sent_decisions", {})
if not drafts:
    st.info("No follow-up drafts are waiting.")

for draft in drafts:
    with st.container(border=True):
        st.subheader(f"{draft.company}, {draft.role}" if draft.role else draft.company)
        details = [f"Proposed {draft.proposed_on}", LANGUAGES.get(draft.language, draft.language)]
        st.caption(" · ".join(d for d in details if d))
        if draft.to_address:
            st.markdown(f"**To:** `{draft.to_address}`")
        else:
            st.warning("No reply-to address found: the draft's To field is empty. Add the recipient in Gmail before sending.")
        st.markdown(f"**Subject:** {draft.subject}")
        # Plain text, exactly as it will be saved: no Markdown rendering.
        st.text(draft.text)
        if draft.approval_id in sent:
            st.success(f"{sent[draft.approval_id]} sent. The page updates once the agent has handled it.")
            continue
        approve, skip = st.columns(2)
        for column, decision, label in ((approve, "approve", "Approve"), (skip, "skip", "Skip")):
            if column.button(label, key=f"{decision}-{draft.approval_id}", use_container_width=True):
                try:
                    backend().decide(draft, decision)
                except Exception as error:  # noqa: BLE001
                    st.error(f"Not sent ({type(error).__name__}). {SESSION_HINT}")
                else:
                    sent[draft.approval_id] = label
                    st.rerun()

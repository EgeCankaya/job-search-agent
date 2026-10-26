"""EV-1's labeling page (OQ-47 (d)), on 127.0.0.1, separate from the Mac app.

Start it from the repo root, so Streamlit reads .streamlit/config.toml
(loopback only, no usage stats); port 8502 leaves 8501 to the Mac app:

    uv run --group app streamlit run eval/label_app.py --server.port 8502

It shows one email at a time: the trimmed text the model sees, and the full
text on request. I enter the class (CLS-1's definitions, after precedence),
the yes/no question, company, role, key date and language. Nothing is
pre-filled by a model, so the labels aren't pulled toward the model's
answers. Labels go to `labels.jsonl` in `EVAL_DATA_DIR`; the page reads and
writes nothing else, and calls no AWS service.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import streamlit as st  # noqa: E402

from eval.labeled_set import LANGUAGES, LabeledSet, LabelError, data_dir, make_label  # noqa: E402
from inbox_agent.classes import PRECEDENCE  # noqa: E402
from inbox_agent.classify import CLASS_DEFINITIONS, UNCERTAINTY_QUESTION  # noqa: E402
from inbox_agent.trim import clean_text, html_to_text, trim_email  # noqa: E402

CLASSES = [str(name) for name in PRECEDENCE]

st.set_page_config(page_title="EV-1 labeling", layout="wide")
st.title("EV-1 labeling")

try:
    store = LabeledSet(data_dir()).ensure()
except ValueError as error:
    st.error(str(error))
    st.stop()

index, labels = store.index(), store.labels()
if not index:
    st.info("No emails yet. Run `eval/fetch_labeling.py` for a date window first.")
    st.stop()

progress = store.progress()
left, middle, right = st.columns(3)
left.metric("Emails", progress["emails"])
middle.metric("Labeled", progress["labeled"])
right.metric("Found by me, not the query", progress["by_found_by"]["manual"])

ids = sorted(index)
unlabeled = [eval_id for eval_id in ids if eval_id not in labels]
# A widget's state can only be set before the widget is drawn, so the next
# email chosen after a save is handed over through another key.
if "next_eval_id" in st.session_state:
    st.session_state["eval_id"] = st.session_state.pop("next_eval_id")
if st.session_state.get("eval_id") not in index:
    st.session_state["eval_id"] = unlabeled[0] if unlabeled else ids[0]
eval_id = st.selectbox(
    "Email", ids, key="eval_id", format_func=lambda i: f"{i}  (labeled)" if i in labels else i
)

email = store.email(eval_id)
trimmed = trim_email(
    subject=email.header.subject, sender=email.header.sender, date=email.header.received_at,
    text=email.text, html=email.html,
)
st.caption(f"{index[eval_id].found_by} · {trimmed.date}")
st.text(f"From: {trimmed.sender}\nSubject: {trimmed.subject}")
if email.invite is not None:
    st.text(f"Calendar invite: {email.invite.start.isoformat()}")
st.text(trimmed.body)
with st.expander("Full text"):
    body = email.text if email.text and email.text.strip() else html_to_text(email.html or "")
    st.text(clean_text(body))

with st.expander("Class definitions"):
    st.text("\n".join(f"{name}: {text}" for name, text in CLASS_DEFINITIONS.items()))

saved = labels.get(eval_id)
with st.form(f"label-{eval_id}"):
    email_class = st.radio(
        "Class (after precedence)", CLASSES, index=CLASSES.index(saved.email_class) if saved else None, horizontal=True
    )
    asks_by_date = st.checkbox(UNCERTAINTY_QUESTION, value=saved.asks_by_date if saved else False)
    company = st.text_input("Company (empty if none)", value=(saved.company or "") if saved else "")
    role = st.text_input("Role (empty if none)", value=(saved.role or "") if saved else "")
    key_date = st.text_input(
        "Key date: YYYY-MM-DD, or YYYY-MM-DDTHH:MM with a time (empty if none)",
        value=(saved.key_date or "") if saved else "",
    )
    language = st.radio(
        "Language", LANGUAGES, index=LANGUAGES.index(saved.language) if saved else None, horizontal=True
    )
    note = st.text_area("Note", value=saved.note if saved else "")
    if st.form_submit_button("Save label"):
        try:
            if email_class is None or language is None:
                raise LabelError("choose a class and a language")
            store.save_label(
                make_label(
                    eval_id=eval_id, email_class=email_class, asks_by_date=asks_by_date, company=company,
                    role=role, key_date=key_date, language=language, note=note,
                )
            )
        except LabelError as error:
            st.error(str(error))
        else:
            remaining = [i for i in ids if i not in store.labels()]
            if remaining:
                st.session_state["next_eval_id"] = remaining[0]
            st.rerun()

"""DASH-1: where the search stands. Reads only."""

import streamlit as st
from common import SESSION_HINT, backend


def show(data) -> None:
    total, open_, urgent, possible = st.columns(4)
    total.metric("Applications", data.total)
    open_.metric("Open", data.open)
    urgent.metric("Alerts this week", data.alerts_this_week["urgent"])
    possible.metric('"Possible:" alerts this week', data.alerts_this_week["possible"])
    if data.alerts_this_week.get("unrecorded"):
        st.caption(f"{data.alerts_this_week['unrecorded']} more alerts this week were sent before their kind was recorded.")

    st.subheader("By status")
    st.dataframe(
        [{"Status": status, "Applications": count} for status, count in data.by_status.items()],
        hide_index=True,
    )

    st.subheader("Open and silent for 10+ days")
    if data.silent:
        st.dataframe(
            [
                {"Company": s.company, "Role": s.role or "", "Status": s.status, "Last contact": s.last_contact.isoformat(), "Days": s.days}
                for s in data.silent
            ],
            hide_index=True,
        )
    else:
        st.write("None.")

    st.subheader("Next 7 days")
    if data.upcoming:
        st.dataframe(
            [
                {"When": u.when_text, "Company": u.company, "Role": u.role or "", "What": (u.kind or "").replace("_", " ")}
                for u in data.upcoming
            ],
            hide_index=True,
        )
    else:
        st.write("Nothing dated.")


st.title("Dashboard")
if st.button("Refresh"):
    st.rerun()
try:
    loaded = backend().dashboard()
except Exception as error:  # noqa: BLE001 - any AWS failure gets the same advice
    st.error(f"Couldn't read the tracker ({type(error).__name__}). {SESSION_HINT}")
else:
    show(loaded)

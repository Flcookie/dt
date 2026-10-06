"""跨页联动：Part Trace 等（改进2 · 方案B）。"""
from __future__ import annotations

UI_NAV_PART_ID = "UI_NAV_PART_ID"


def set_trace_part_id(part_id: str) -> None:
    import streamlit as st

    st.session_state[UI_NAV_PART_ID] = (part_id or "").strip()


def switch_to_part_trace(part_id: str | None = None) -> None:
    import streamlit as st

    pid = (part_id or "").strip()
    if pid:
        st.switch_page("pages/03_Part_Trace.py", query_params={"part_id": pid})
    else:
        st.switch_page("pages/03_Part_Trace.py")

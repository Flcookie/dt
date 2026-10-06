import os
import sys

_fp = os.path.abspath(__file__)
_d = os.path.dirname(_fp)
_APP = os.path.dirname(_d) if os.path.basename(_d) == "pages" else _d
if _APP not in sys.path:
    sys.path.insert(0, _APP)
from paths import ensure_paths  # noqa: E402

ensure_paths()

import streamlit as st

import ui.nav as ui_nav
import ui.part_trace_panel as ui_part_trace_panel
import ui.sidebar as ui_sidebar

st.set_page_config(
    page_title="Part Trace",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui_sidebar.render_shell(page="03 Part trace")

if ui_nav.UI_NAV_PART_ID in st.session_state:
    _v = st.session_state.pop(ui_nav.UI_NAV_PART_ID)
    if _v:
        st.session_state["part_trace_part_id"] = _v

st.title("Part trace")
ui_part_trace_panel.render_part_trace_panel(from_query_params=True)
ui_sidebar.finalize_neo4j_indexes()

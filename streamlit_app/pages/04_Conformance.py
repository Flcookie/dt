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

import ui.conformance_panel as ui_conformance_panel
import ui.sidebar as ui_sidebar

st.set_page_config(
    page_title="Conformance",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui_sidebar.render_shell(page="04 Conformance")

st.title("Conformance")
ui_conformance_panel.render_conformance_panel()
ui_sidebar.finalize_neo4j_indexes()

"""History UI lives on Control Panel (HISTORY); this page is a bookmark redirect."""
import os
import sys

_fp = os.path.abspath(__file__)
_d = os.path.dirname(_fp)
_APP = os.path.dirname(_d) if os.path.basename(_d) == "pages" else _d
if _APP not in sys.path:
    sys.path.insert(0, _APP)
from paths import ensure_paths  # noqa: E402

ensure_paths()

import streamlit as st  # noqa: E402

import services.mqtt_backend as mqtt_backend  # noqa: E402
import ui.sidebar as ui_sidebar  # noqa: E402

st.set_page_config(
    page_title="History",
    layout="centered",
    initial_sidebar_state="expanded",
)
mqtt_backend.ensure_started()
ui_sidebar.render_shell(page="02 History")

st.info(
    "**HISTORY** (import / replay / export) is on the **Control panel** home page."
)
st.page_link("app.py", label="← DASHBOARD", use_container_width=True)
ui_sidebar.finalize_neo4j_indexes()

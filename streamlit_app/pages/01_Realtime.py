import os
import sys

_fp = os.path.abspath(__file__)
_d = os.path.dirname(_fp)
_APP = os.path.dirname(_d) if os.path.basename(_d) == "pages" else _d
if _APP not in sys.path:
    sys.path.insert(0, _APP)
from paths import ensure_paths  # noqa: E402

ensure_paths()

import time

import streamlit as st

import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import ui.kpi_display as ui_kpi_display
import ui.live_refresh as ui_live_refresh
import ui.sidebar as ui_sidebar

st.set_page_config(
    page_title="KPI Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui_sidebar.render_shell(page="01 KPI")

st.title("KPI Dashboard")
st.page_link("app.py", label="← DASHBOARD", icon="🏠")

@st.fragment(run_every=ui_live_refresh.live_ui_refresh_delta())
def _kpi_fragment():
    kpi, tupd = mqtt_backend.get_kpi_snapshot()
    if not mqtt_backend.kpi_connected():
        _sid = None
        if st.session_state.get("cp_data_source") != "live":
            _sid = st.session_state.get("dt_resolved_session")
        _alt = neo4j_backend.get_session_kpi_snapshot(_sid)
        if _alt:
            kpi = _alt
            tupd = time.time()

    st.session_state["_kpi_cache"] = kpi
    st.session_state["_kpi_tupd"] = tupd

    tupd_f = tupd
    age = ui_kpi_display.replay_age_seconds(tupd_f) if tupd_f else None

    if kpi.get("run_mode") == "history":
        st.caption(
            "KPI from **Neo4j** (selected or latest session). Live MQTT / file replay overrides when active."
        )

    ui_kpi_display.render_kpi_dashboard(
        kpi,
        age_sec=age,
        cols_per_row=4,
        compact_stations=True,
    )


_kpi_fragment()
ui_sidebar.finalize_neo4j_indexes()

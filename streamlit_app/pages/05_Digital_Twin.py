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

import twin.digital_twin_cache as digital_twin_cache
import twin.factory_floor_plotly as factory_floor_plotly
import twin.factory_floor_sim as factory_floor_sim
import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import ui.live_refresh as ui_live_refresh
import ui.nav as ui_nav
import ui.part_trace_panel as ui_part_trace_panel
import ui.sidebar as ui_sidebar

st.set_page_config(
    page_title="Digital Twin",
    layout="wide",
    initial_sidebar_state="expanded",
)
mqtt_backend.ensure_started()
ui_sidebar.render_shell(page="05 Digital Twin")

if "dt_q_injected" not in st.session_state:
    st.session_state.dt_q_injected = True
    _qp0 = st.query_params.get("part_id")
    if _qp0 and str(_qp0).strip():
        st.session_state.part_trace_part_id = str(_qp0).strip()

if ui_nav.UI_NAV_PART_ID in st.session_state:
    _v = st.session_state.pop(ui_nav.UI_NAV_PART_ID)
    if _v:
        st.session_state.part_trace_part_id = _v.strip()

st.title("Digital Twin")
st.page_link("app.py", label="← DASHBOARD", icon="🏠")

st.subheader("Factory Layout")


@st.fragment(run_every=ui_live_refresh.live_ui_refresh_delta())
def _digital_twin_synced():
    """单 fragment：地图与 Part Tracking 同一拍拉 Neo4j / session，避免两区错开刷新。"""
    sess = mqtt_backend.resolve_digital_twin_neo4j_session_id()
    neo = neo4j_backend.neo4j_ping()
    kpi, _tupd = mqtt_backend.get_kpi_snapshot()
    is_replay = isinstance(kpi, dict) and (kpi.get("run_mode") or "") == "replay"

    floor_sim: dict | None = None
    status: str | None = None
    twin_preload_parts: list[dict] | None = None
    twin_preload_rows: list[dict] | None = None
    twin_preload_sid: str | None = None

    floor_sim, status = factory_floor_sim.sync_factory_floor_sim(
        sess,
        is_replay=is_replay,
        kpi=kpi,
        neo_connected=bool(neo.get("connected")),
    )

    if neo.get("connected") and sess:
        twin_preload_parts, twin_preload_rows, twin_preload_sid = (
            digital_twin_cache.resolve_twin_part_trace(
                sess,
                is_replay=is_replay,
                kpi=kpi,
                neo_connected=True,
            )
        )

    if status:
        st.markdown(status)

    with st.container(border=True):
        _fig = factory_floor_plotly.build_factory_floor_figure(sim_state=floor_sim)
        st.plotly_chart(
            _fig,
            use_container_width=True,
            key="digital_twin_factory_floor",
            config={"displayModeBar": False},
        )

    st.divider()
    st.subheader("Part Trace")
    with st.container(border=True):
        ui_part_trace_panel.render_part_trace_panel(
            from_query_params=False,
            use_page_session=True,
            use_coordinated_twin_session=True,
            coordinated_twin_session_id=sess,
            kpi_for_replay=kpi,
            twin_preloaded_parts=twin_preload_parts,
            twin_preloaded_rows=twin_preload_rows,
            twin_preloaded_session_id=twin_preload_sid,
        )


_digital_twin_synced()
ui_part_trace_panel.render_pending_complete_trace_dialog()
ui_sidebar.finalize_neo4j_indexes()

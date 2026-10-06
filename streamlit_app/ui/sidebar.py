"""Per-page hook: MQTT ready + Neo4j index check."""
from __future__ import annotations

import streamlit as st

import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend

_NEO4J_INDEX_SESSION_KEY = "_neo4j_index_report"

# 全站略放大字号（各页在 set_page_config 后调用 ui_sidebar.render 即生效）
_GLOBAL_APP_FONT_CSS = """
<style>
    html {
        font-size: 17px;
    }
    div[data-testid="stMainBlockContainer"] {
        max-width: 1440px !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        padding-top: 1rem !important;
    }
    div[data-testid="stMainBlockContainer"] h1 {
        font-size: 1.88rem !important;
    }
    div[data-testid="stMainBlockContainer"] h2 {
        font-size: 1.65rem !important;
    }
    div[data-testid="stMainBlockContainer"] h3 {
        font-size: 1.4rem !important;
    }
    div[data-testid="stSidebarContent"] {
        font-size: 1rem !important;
    }
</style>
"""


@st.cache_resource(show_spinner=False)
def _cached_neo4j_indexes() -> dict:
    return neo4j_backend.ensure_indexes()


def render_shell(*, page: str = "") -> None:
    """Fast path: global CSS + MQTT. Call right after `st.set_page_config`."""
    _ = page
    st.markdown(_GLOBAL_APP_FONT_CSS, unsafe_allow_html=True)
    mqtt_backend.ensure_started()


def finalize_neo4j_indexes() -> None:
    """Neo4j index check — call after main page content so first paint is not blocked.

    Do not keep a failed result forever: Neo4j may come up after the first load.
    """
    _idx = st.session_state.get(_NEO4J_INDEX_SESSION_KEY)
    if not isinstance(_idx, dict) or not _idx.get("ok"):
        if isinstance(_idx, dict) and not _idx.get("ok"):
            _cached_neo4j_indexes.clear()
        try:
            _idx = _cached_neo4j_indexes()
        except Exception as exc:
            _idx = {"ok": False, "errors": [str(exc)]}
        st.session_state[_NEO4J_INDEX_SESSION_KEY] = _idx
    if not _idx.get("ok"):
        st.warning(
            "Neo4j indexes not fully ready: **{}**".format(
                "; ".join(_idx.get("errors", [])[:2]) or "unknown error"
            )
        )


def render(*, page: str = "") -> None:
    """Shell only; call `finalize_neo4j_indexes()` after page body when possible."""
    render_shell(page=page)

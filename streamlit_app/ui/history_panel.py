"""HISTORY block: latest session (or post-import target), CSV import, replay, export."""
from __future__ import annotations

import csv
import io
import os
import time

import streamlit as st

import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import services.process_control as process_control
import services.recording as recording
import ui.replay_panel as ui_replay_panel
from paths import PROJECT_ROOT

# Selectbox 内部值；展示文案为「--Select Session--」
_HIST_SESSION_PLACEHOLDER = "__hist_session_placeholder__"
_SESSION_SELECT_DISPLAY = "--Select Session--"


def _resolve_history_session_id(
    session_key: str,
    export_key: str,
    sel_ids: list[str],
    id_to_label: dict[str, str] | None = None,
) -> str | None:
    """Resolve the active History session id from widget / export state."""
    for raw in (st.session_state.get(session_key), st.session_state.get(export_key)):
        if not isinstance(raw, str):
            continue
        sid = raw.strip()
        if not sid or sid == _HIST_SESSION_PLACEHOLDER:
            continue
        if sid in sel_ids:
            return sid
        if id_to_label:
            for known_id, label in id_to_label.items():
                if sid == label:
                    return known_id
    return None


def _import_feedback_key(kp: str) -> str:
    return "{}_import_feedback".format(kp)


def _history_panel_css(kp: str) -> str:
    """统一工具条按钮高度/白底描边；压缩上传区；无图标前缀。"""
    k_import = "{}_import_btn".format(kp)
    k_go = "{}_replay_toggle".format(kp)
    k_dl1 = "{}_dl_log".format(kp)
    k_dl2 = "{}_dl_kpi".format(kp)
    k_spd = "{}_replay_spd".format(kp)
    k_spd_row = "{}_speed_row".format(kp)
    k_sess = "{}_history_session_id".format(kp)
    k_toolbar = "{}_hist_toolbar".format(kp)
    k_up = "{}_csv_up".format(kp)
    k_d1 = "{}_dup_skip".format(kp)
    k_d2 = "{}_dup_force".format(kp)
    k_import_col = "{}_import_col".format(kp)
    k_import_block = "{}_import_block".format(kp)
    return """
<style>
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
        flex: 1 1 0 !important;
        min-width: 0 !important;
        width: auto !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} [data-testid="stDownloadButton"] > button {{
        width: 100% !important;
        min-height: var(--cp-btn-h, 46px) !important;
        max-height: var(--cp-btn-h, 46px) !important;
        height: var(--cp-btn-h, 46px) !important;
        font-size: 15px !important;
        font-weight: 600 !important;
        letter-spacing: 0.01em !important;
        padding: 0 10px !important;
        border-radius: 8px !important;
        box-sizing: border-box !important;
        box-shadow: none !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        background: #ffffff !important;
        border: 1px solid #dee2e6 !important;
        border-bottom: 1px solid #dee2e6 !important;
        color: #374151 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_go} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl1} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl2} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_d1} button,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_d2} button {{
        width: 100% !important;
        min-height: var(--cp-btn-h, 46px) !important;
        max-height: var(--cp-btn-h, 46px) !important;
        height: var(--cp-btn-h, 46px) !important;
        font-size: 15px !important;
        font-weight: 600 !important;
        letter-spacing: 0.01em !important;
        padding: 0 10px !important;
        border-radius: 8px !important;
        box-sizing: border-box !important;
        box-shadow: none !important;
        background: #ffffff !important;
        border: 1px solid #dee2e6 !important;
        border-bottom: 1px solid #dee2e6 !important;
        color: #374151 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import} button:hover:not(:disabled),
    div[data-testid="stMainBlockContainer"] div.st-key-{k_go} button:hover:not(:disabled),
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl1} button:hover:not(:disabled),
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl2} button:hover:not(:disabled),
    div[data-testid="stMainBlockContainer"] div.st-key-{k_d1} button:hover:not(:disabled),
    div[data-testid="stMainBlockContainer"] div.st-key-{k_d2} button:hover:not(:disabled) {{
        background: #f8f9fa !important;
        border-color: #cbd5e1 !important;
        border-bottom-color: #cbd5e1 !important;
        box-shadow: none !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_go} button:disabled,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl1} button:disabled,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_dl2} button:disabled,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import} button:disabled,
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} [data-testid="stDownloadButton"] > button:disabled {{
        opacity: 0.45 !important;
        cursor: not-allowed !important;
        background: #ffffff !important;
        color: #94a3b8 !important;
        border-color: #e2e8f0 !important;
        box-shadow: none !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_spd} [data-testid="stSelectbox"] {{
        min-height: var(--cp-btn-h, 46px) !important;
        width: 100% !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_spd} [data-baseweb="select"] {{
        width: 100% !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_spd} [data-baseweb="select"] > div {{
        min-height: var(--cp-btn-h, 46px) !important;
        height: var(--cp-btn-h, 46px) !important;
        font-size: 15px !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_spd_row} > div > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"],
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} > div > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {{
        gap: 8px !important;
        align-items: flex-start !important;
    }}
    div[data-testid="stMainBlockContainer"] .hist-replay-status-line {{
        font-size: 13px !important;
        color: #64748b !important;
        margin: 4px 0 0 0 !important;
        min-height: 18px !important;
        line-height: 1.35 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_sess} [data-testid="stSelectbox"] {{
        min-height: 40px !important;
        width: 100% !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_sess} [data-baseweb="select"] {{
        width: 100% !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_sess} [data-baseweb="select"] > div {{
        min-height: 40px !important;
        font-size: 15px !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_sess} [role="listbox"] [role="option"] {{
        font-size: 15px !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_sess} {{
        margin-bottom: 2px !important;
        width: 100% !important;
    }}
    div[data-testid="stMainBlockContainer"] .hist-select-label {{
        font-family: var(--sans) !important;
        font-size: 15px !important;
        font-weight: 600 !important;
        color: #64748b !important;
        margin: 0 0 6px 0 !important;
        line-height: 1.2 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_toolbar} {{
        margin-top: 10px !important;
    }}
    div[data-testid="stMainBlockContainer"] hr.hist-section-divider {{
        border: none !important;
        border-top: 1px solid #e0e0e0 !important;
        margin: 1rem 0 0.85rem 0 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploader"] {{
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 8px 10px !important;
        min-height: 48px !important;
        box-sizing: border-box !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploader"] section {{
        padding: 0 !important;
        border: none !important;
        background: transparent !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploaderDropzone"] {{
        min-height: 32px !important;
        max-height: 42px !important;
        padding: 0 !important;
        align-items: center !important;
        border: none !important;
        background: transparent !important;
        gap: 10px !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploaderDropzoneInstructions"] {{
        font-size: 13px !important;
        color: #64748b !important;
        opacity: 0.72 !important;
        line-height: 1.2 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploaderDropzoneInstructions"] small {{
        display: none !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploaderDropzone"] button {{
        font-size: 13px !important;
        font-weight: 600 !important;
        padding: 5px 12px !important;
        min-height: 32px !important;
        border-radius: 7px !important;
        background: #f8fafc !important;
        border: 1px solid #cbd5e1 !important;
        border-bottom: 1px solid #cbd5e1 !important;
        color: #334155 !important;
        box-shadow: none !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_up} [data-testid="stFileUploaderDropzone"] button:hover {{
        border: 1px solid #94a3b8 !important;
        border-bottom: 1px solid #94a3b8 !important;
        background: #f1f5f9 !important;
        box-shadow: none !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import_block} {{
        margin: 0 0 0.1rem 0 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import_block} [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {{
        flex: 1 1 0 !important;
        min-width: 0 !important;
        width: auto !important;
    }}
    /* Import button column above upload dropzone if overlap (stacked layout uses this less). */
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import_col} {{
        position: relative !important;
        z-index: 2 !important;
    }}
    div[data-testid="stMainBlockContainer"] div.st-key-{k_import_col} button {{
        min-height: var(--cp-btn-h, 46px) !important;
        max-height: var(--cp-btn-h, 46px) !important;
        height: var(--cp-btn-h, 46px) !important;
        width: 100% !important;
        border-radius: 8px !important;
    }}
    div[data-testid="stMainBlockContainer"] .hist-import-status-line {{
        font-size: 15px !important;
        margin: 0.35rem 0 0 0 !important;
        line-height: 1.35 !important;
    }}
</style>
""".format(
        k_import=k_import,
        k_go=k_go,
        k_dl1=k_dl1,
        k_dl2=k_dl2,
        k_spd=k_spd,
        k_spd_row=k_spd_row,
        k_sess=k_sess,
        k_toolbar=k_toolbar,
        k_up=k_up,
        k_d1=k_d1,
        k_d2=k_d2,
        k_import_col=k_import_col,
        k_import_block=k_import_block,
    )


@st.cache_data(ttl=15, show_spinner=False)
def _cached_sessions_enriched(limit: int) -> list[dict]:
    return neo4j_backend.list_sessions_enriched(limit)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_export_events_csv(session_id: str) -> bytes:
    return neo4j_backend.export_session_events_csv(session_id).encode("utf-8")


@st.cache_data(ttl=60, show_spinner=False)
def _cached_export_kpi_csv(session_id: str) -> bytes:
    return neo4j_backend.export_session_kpi_log_csv(session_id).encode("utf-8")


def _clear_history_caches() -> None:
    _cached_sessions_enriched.clear()
    _cached_export_events_csv.clear()
    _cached_export_kpi_csv.clear()


_SPEED_PRESETS = {
    "1×": 1.0,
    "2×": 2.0,
    "5×": 5.0,
    "10×": 10.0,
    "20×": 20.0,
    "50×": 50.0,
}


def render_history_panel(*, key_prefix: str = "hist", disabled: bool = False) -> None:
    """All widget keys are prefixed to avoid clashes when embedded.

    When ``disabled`` is True (e.g. Live data source or no config selected),
    controls are shown but non-interactive — **no Neo4j round-trips** on refresh.
    """
    kp = key_prefix
    d = disabled
    sess_key = "{}_history_session_id".format(kp)
    export_sid_key = "{}_export_session_id".format(kp)
    dup_key = "{}_dup_import".format(kp)
    feedback_key = _import_feedback_key(kp)

    if d:
        st.session_state.pop(dup_key, None)
        st.session_state.pop(feedback_key, None)
    ui_replay_panel.ensure_replay_session_state()

    st.markdown(_history_panel_css(kp), unsafe_allow_html=True)

    sel_ids, id_to_label = _sync_session_choices(kp, sess_key, d)

    _rp = st.session_state.get("replay_proc")
    if _rp is not None and _rp.poll() is not None:
        ui_replay_panel._clear_replay_child_and_temp_file()

    rec_on = recording.is_recording()
    replay_live = (
        st.session_state.get("replay_proc") is not None
        and st.session_state.replay_proc.poll() is None
    )

    chosen_id, export_sid = _render_session_select(
        sess_key, export_sid_key, sel_ids, id_to_label, d, replay_live
    )
    _render_replay_export_toolbar(
        kp,
        d,
        sess_key=sess_key,
        export_sid_key=export_sid_key,
        sel_ids=sel_ids,
        id_to_label=id_to_label,
        export_sid=export_sid,
        replay_live=replay_live,
        can_start=bool(chosen_id) and not rec_on and not replay_live and not d,
    )

    st.markdown('<hr class="hist-section-divider" />', unsafe_allow_html=True)

    up, do_import = _render_import_row(kp, d, dup_key)
    if do_import:
        with st.spinner("Importing..."):
            _import_uploaded_csv(up, kp, dup_key, feedback_key)

    _show_import_feedback(feedback_key)

    if dup_key in st.session_state:
        _render_duplicate_import_prompt(kp, d, dup_key, feedback_key)


def _sync_session_choices(
    kp: str, sess_key: str, disabled: bool
) -> tuple[list[str], dict[str, str]]:
    """Load selectable Neo4j sessions and keep the session selectbox on a valid value.

    Returns ``(session ids, id -> label)``; both empty when disabled or Neo4j is down
    (selection forced to the placeholder). A session queued by a successful import
    (``{kp}_pending_select_session``) is selected once.
    """
    if disabled:
        st.session_state[sess_key] = _HIST_SESSION_PLACEHOLDER
        return [], {}

    status = neo4j_backend.neo4j_ping()
    if not status.get("connected"):
        st.error("Database not connected: **{}**".format(status.get("error", "unknown")))
        st.session_state[sess_key] = _HIST_SESSION_PLACEHOLDER
        return [], {}

    pending = st.session_state.pop("{}_pending_select_session".format(kp), None)
    sessions = _cached_sessions_enriched(120)
    # Hide empty+open artifacts (typically aborted/placeholder sessions) from History selector.
    sessions = [
        s
        for s in sessions
        if not (
            int(s.get("event_count") or 0) <= 0
            and str(s.get("status_badge") or "").strip().lower() == "open"
        )
    ]
    sel_ids = [s["id"] for s in sessions]
    id_to_label = {s["id"]: s.get("label") or s["id"] for s in sessions}

    if pending and isinstance(pending, str) and pending in sel_ids:
        st.session_state[sess_key] = pending
    elif not sel_ids:
        st.session_state[sess_key] = _HIST_SESSION_PLACEHOLDER
    else:
        cur = st.session_state.get(sess_key)
        if not (
            isinstance(cur, str) and (cur in sel_ids or cur == _HIST_SESSION_PLACEHOLDER)
        ):
            st.session_state[sess_key] = _HIST_SESSION_PLACEHOLDER
    return sel_ids, id_to_label


def _render_session_select(
    sess_key: str,
    export_sid_key: str,
    sel_ids: list[str],
    id_to_label: dict[str, str],
    disabled: bool,
    replay_live: bool,
) -> tuple[str | None, str | None]:
    """Session selectbox. Returns ``(chosen session id, session id used for export)``.

    Also publishes the choice as ``dt_resolved_session`` for the Digital Twin page.
    """
    if not disabled and sel_ids:
        resolved_pre = _resolve_history_session_id(
            sess_key, export_sid_key, sel_ids, id_to_label
        )
        if resolved_pre:
            st.session_state[sess_key] = resolved_pre

    def _format_session_option(sid: str) -> str:
        if sid == _HIST_SESSION_PLACEHOLDER:
            return _SESSION_SELECT_DISPLAY
        return id_to_label.get(sid, sid)

    st.selectbox(
        "session",
        [_HIST_SESSION_PLACEHOLDER] + sel_ids,
        format_func=_format_session_option,
        key=sess_key,
        label_visibility="collapsed",
        disabled=disabled or replay_live or not sel_ids,
        help="Choose a Neo4j session for replay and export.",
    )

    raw_sel = st.session_state.get(sess_key)
    chosen_id = _resolve_history_session_id(sess_key, export_sid_key, sel_ids, id_to_label)
    if isinstance(raw_sel, str) and raw_sel == _HIST_SESSION_PLACEHOLDER:
        st.session_state.pop(export_sid_key, None)
    elif chosen_id:
        st.session_state[export_sid_key] = chosen_id

    export_sid: str | None = st.session_state.get(export_sid_key) if not disabled else None
    if not disabled:
        st.session_state["dt_resolved_session"] = chosen_id or export_sid
    return chosen_id, export_sid


def _render_replay_export_toolbar(
    kp: str,
    disabled: bool,
    *,
    sess_key: str,
    export_sid_key: str,
    sel_ids: list[str],
    id_to_label: dict[str, str],
    export_sid: str | None,
    replay_live: bool,
    can_start: bool,
) -> None:
    """Speed · Start/Stop Replay · Export Event Log · Export KPI Report."""
    ev_csv = b""
    kpi_csv = b""
    if export_sid:
        ev_csv = _cached_export_events_csv(export_sid)
        kpi_csv = _cached_export_kpi_csv(export_sid)
    export_disabled = disabled or not export_sid

    with st.container(key="{}_hist_toolbar".format(kp)):
        c_speed, c2, c3, c4 = st.columns(
            4,
            gap="small",
            vertical_alignment="top",
        )
        with c_speed:
            sp_label = st.selectbox(
                "Speed",
                list(_SPEED_PRESETS.keys()),
                index=3,
                key="{}_replay_spd".format(kp),
                label_visibility="collapsed",
                disabled=disabled or replay_live,
                help="Playback speed (before Start Replay)",
            )
        speed = _SPEED_PRESETS[sp_label]
        with c2:
            if replay_live:
                if st.button(
                    "Stop Replay",
                    key="{}_replay_toggle".format(kp),
                    disabled=disabled,
                    use_container_width=True,
                ):
                    ui_replay_panel._clear_replay_child_and_temp_file()
                    st.rerun()
            elif st.button(
                "Start Replay",
                key="{}_replay_toggle".format(kp),
                disabled=not can_start,
                use_container_width=True,
            ):
                replay_sid = _resolve_history_session_id(
                    sess_key, export_sid_key, sel_ids, id_to_label
                )
                if not replay_sid:
                    st.error("Choose a session first.")
                else:
                    st.session_state[export_sid_key] = replay_sid
                    _start_session_replay(replay_sid, speed)
            _rp_show = st.session_state.get("replay_proc")
            if _rp_show is not None and _rp_show.poll() is None:
                st.markdown(
                    (
                        '<p class="hist-replay-status-line">'
                        "Replay started · PID <strong>{}</strong>"
                        "</p>"
                    ).format(_rp_show.pid),
                    unsafe_allow_html=True,
                )

        with c3:
            st.download_button(
                "Export Event Log",
                data=ev_csv if ev_csv else b"",
                file_name="session_log_{}.csv".format(export_sid or "none"),
                mime="text/csv",
                key="{}_dl_log".format(kp),
                use_container_width=True,
                help="Events: time, component_id, part_id, activity",
                disabled=export_disabled,
            )
        with c4:
            st.download_button(
                "Export KPI Report",
                data=kpi_csv if kpi_csv else b"",
                file_name="kpi_log_{}.csv".format(export_sid or "none"),
                mime="text/csv",
                key="{}_dl_kpi".format(kp),
                use_container_width=True,
                help="KPI long table: system / stage / station",
                disabled=export_disabled,
            )


def _start_session_replay(replay_sid: str, speed: float) -> None:
    """Start the KPI-only replay worker for an existing Neo4j session.

    Order: check the session has events -> stop any previous replay -> stop main_service
    -> switch to ``config_local.json`` if it exists -> clear KPI caches -> spawn worker.
    Any failure is shown with ``st.error`` and stops the sequence.
    """
    n_events = neo4j_backend.count_session_events(replay_sid)
    if n_events < 0:
        st.error(
            "Could not read events from the database. "
            "Check Neo4j connection and try again."
        )
        return
    if n_events <= 0:
        st.error("No events in this session.")
        return

    ui_replay_panel._clear_replay_child_and_temp_file()
    ok_ms, ms_msg = process_control.ensure_main_service_replay()
    if not ok_ms:
        st.error(ms_msg)
        return

    local_cfg = os.path.normpath(os.path.join(PROJECT_ROOT, "config_local.json"))
    if os.path.isfile(local_cfg):
        try:
            mqtt_backend.switch_config_file("config_local.json")
            time.sleep(2)
        except Exception as e:
            st.error("Failed to switch local config: {}".format(e))
            return

    ui_replay_panel._reset_replay_downstream_for_new_run()
    try:
        st.session_state.replay_proc = mqtt_backend.run_replay_session_subprocess(
            replay_sid, speed
        )
    except Exception as ex:
        st.error(str(ex))


def _render_import_row(kp: str, disabled: bool, dup_key: str):
    """CSV uploader + "Import to Database" button. Returns ``(uploaded file, clicked)``."""
    dup_wait = dup_key in st.session_state
    with st.container(key="{}_import_block".format(kp)):
        _imp_l, _imp_r = st.columns(
            [3, 1], gap="small", vertical_alignment="center"
        )
        with _imp_l:
            up = st.file_uploader(
                "CSV file",
                type=["csv"],
                key="{}_csv_up".format(kp),
                label_visibility="collapsed",
                disabled=disabled,
            )
        with _imp_r:
            with st.container(key="{}_import_col".format(kp)):
                if dup_wait:
                    st.caption("Duplicate session — see options below.")
                do_import = st.button(
                    "Import to Database",
                    key="{}_import_btn".format(kp),
                    use_container_width=True,
                    disabled=disabled or dup_wait or up is None,
                )
    return up, do_import


def _import_uploaded_csv(up, kp: str, dup_key: str, feedback_key: str) -> None:
    """Validate the uploaded CSV and import it as a new session.

    If a session with the same first event time and row count exists, park the rows
    under ``dup_key`` (the duplicate prompt asks the user) instead of importing.
    Results are reported through ``feedback_key``.
    """
    st.session_state.pop(feedback_key, None)
    if not up:
        _set_import_feedback(feedback_key, "error", "Import failed. Choose a CSV file first.")
        return

    try:
        text = io.TextIOWrapper(up, encoding="utf-8", errors="replace")
        rows = list(csv.DictReader(text))
    except Exception as ex:
        _set_import_feedback(feedback_key, "error", "Import failed. {}".format(ex))
        return
    if not rows:
        _set_import_feedback(feedback_key, "error", "Import failed. The file has no data rows.")
        return

    missing = [
        r
        for r in rows
        if not str(r.get("time") or "").strip()
        or "activity" not in r
        or "component_id" not in r
    ]
    if missing:
        _set_import_feedback(
            feedback_key,
            "error",
            "Import failed. Each row must include time, component_id, part_id, and activity.",
        )
        return

    sorted_rows = sorted(
        rows,
        key=lambda r: neo4j_backend._parse_event_time_key(r.get("time")),
    )
    first_raw = str(sorted_rows[0].get("time") or "").strip()
    dup_info = neo4j_backend.find_csv_import_duplicate_info(first_raw, len(sorted_rows))
    if dup_info:
        st.session_state.pop(feedback_key, None)
        st.session_state[dup_key] = {
            "rows": sorted_rows,
            "filename": up.name or "uploaded.csv",
            "dup_info": dup_info,
        }
        st.rerun()
    else:
        _import_rows_as_session(
            sorted_rows, up.name or "uploaded.csv", kp, feedback_key, force_new_id=False
        )


def _import_rows_as_session(
    rows: list[dict], filename: str, kp: str, feedback_key: str, *, force_new_id: bool
) -> None:
    """Import rows into Neo4j. On success: select the new session, report, clear caches, rerun.

    On failure only the error feedback is set (callers decide whether to rerun).
    """
    try:
        _sid, n_ev, _ = neo4j_backend.import_csv_session(
            rows,
            filename,
            force_new_id=force_new_id,
            display_name=None,
        )
        st.session_state["{}_pending_select_session".format(kp)] = _sid
        _set_import_feedback(
            feedback_key, "success", "Import successful. {} event(s) imported.".format(n_ev)
        )
        _clear_history_caches()
        st.rerun()
    except Exception as ex:
        _set_import_feedback(feedback_key, "error", "Import failed. {}".format(ex))


def _set_import_feedback(feedback_key: str, level: str, text: str) -> None:
    st.session_state[feedback_key] = {"level": level, "text": text}


def _show_import_feedback(feedback_key: str) -> None:
    fb = st.session_state.get(feedback_key)
    if not (isinstance(fb, dict) and str(fb.get("text") or "").strip()):
        return
    level = str(fb.get("level") or "info")
    text = str(fb["text"])
    if level == "success":
        st.success(text, icon="✅")
    elif level == "error":
        st.error(text, icon="❌")
    else:
        st.info(text, icon="ℹ️")


def _render_duplicate_import_prompt(
    kp: str, disabled: bool, dup_key: str, feedback_key: str
) -> None:
    """Warning + "Skip" / "Import as new session anyway" for a parked duplicate CSV import."""
    payload = st.session_state[dup_key]
    di = payload.get("dup_info") or {}
    n = len(payload.get("rows") or [])
    st.warning(
        "This dataset already exists as session **`{}`** "
        "(same first event time and **{}** rows). "
        "Session start_time: `{}`.".format(
            di.get("id", "—"),
            n,
            di.get("start_time") or "—",
        )
    )
    b1, b2 = st.columns(2)
    if b1.button(
        "Skip",
        key="{}_dup_skip".format(kp),
        use_container_width=True,
        disabled=disabled,
    ):
        del st.session_state[dup_key]
        st.session_state.pop(feedback_key, None)
        st.rerun()
    if b2.button(
        "Import as new session anyway",
        key="{}_dup_force".format(kp),
        use_container_width=True,
        disabled=disabled,
    ):
        p = st.session_state.pop(dup_key, None)
        if p:
            with st.spinner("Importing..."):
                _import_rows_as_session(
                    p["rows"], p["filename"], kp, feedback_key, force_new_id=True
                )
                # Reached only when the import failed (success already reran).
                st.rerun()

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


def render_history_panel(*, key_prefix: str = "hist", disabled: bool = False) -> None:
    """All widget keys are prefixed to avoid clashes when embedded.

    When ``disabled`` is True (e.g. Live data source or no config selected),
    controls are shown but non-interactive — **no Neo4j round-trips** on refresh.
    """
    kp = key_prefix
    d = disabled
    if d:
        _dup_k = "{}_dup_import".format(kp)
        if _dup_k in st.session_state:
            del st.session_state[_dup_k]
        st.session_state.pop(_import_feedback_key(kp), None)
    ui_replay_panel.ensure_replay_session_state()

    st.markdown(_history_panel_css(kp), unsafe_allow_html=True)

    _sess_key = "{}_history_session_id".format(kp)
    sel_ids: list[str] = []
    id_to_label: dict[str, str] = {}

    if d:
        st.session_state[_sess_key] = _HIST_SESSION_PLACEHOLDER
    else:
        status = neo4j_backend.neo4j_ping()
        if not status.get("connected"):
            st.error(
                "Database not connected: **{}**".format(status.get("error", "unknown"))
            )
            st.session_state[_sess_key] = _HIST_SESSION_PLACEHOLDER
        else:
            _pend = st.session_state.pop("{}_pending_select_session".format(kp), None)
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

            if _pend and isinstance(_pend, str) and _pend in sel_ids:
                st.session_state[_sess_key] = _pend
            elif not sel_ids:
                st.session_state[_sess_key] = _HIST_SESSION_PLACEHOLDER
            else:
                _cur = st.session_state.get(_sess_key)
                if isinstance(_cur, str) and (
                    _cur in sel_ids or _cur == _HIST_SESSION_PLACEHOLDER
                ):
                    pass
                else:
                    st.session_state[_sess_key] = _HIST_SESSION_PLACEHOLDER

    _rp = st.session_state.get("replay_proc")
    if _rp is not None and _rp.poll() is not None:
        ui_replay_panel._clear_replay_child_and_temp_file()

    speed_presets = {
        "1×": 1.0,
        "2×": 2.0,
        "5×": 5.0,
        "10×": 10.0,
        "20×": 20.0,
        "50×": 50.0,
    }
    _rec_on = recording.is_recording()
    _replay_live = (
        st.session_state.get("replay_proc") is not None
        and st.session_state.replay_proc.poll() is None
    )

    _sess_options = (
        [_HIST_SESSION_PLACEHOLDER] + sel_ids if sel_ids else [_HIST_SESSION_PLACEHOLDER]
    )
    _export_sid_key = "{}_export_session_id".format(kp)
    if not d and sel_ids:
        _resolved_pre = _resolve_history_session_id(
            _sess_key, _export_sid_key, sel_ids, id_to_label
        )
        if _resolved_pre:
            st.session_state[_sess_key] = _resolved_pre

    def _format_session_option(sid: str) -> str:
        if sid == _HIST_SESSION_PLACEHOLDER:
            return _SESSION_SELECT_DISPLAY
        return id_to_label.get(sid, sid)

    st.selectbox(
        "session",
        _sess_options,
        format_func=_format_session_option,
        key=_sess_key,
        label_visibility="collapsed",
        disabled=d or _replay_live or not sel_ids,
        help="Choose a Neo4j session for replay and export.",
    )

    _raw_sel = st.session_state.get(_sess_key)
    chosen_id: str | None = _resolve_history_session_id(
        _sess_key, _export_sid_key, sel_ids, id_to_label
    )
    if isinstance(_raw_sel, str) and _raw_sel == _HIST_SESSION_PLACEHOLDER:
        st.session_state.pop(_export_sid_key, None)
    elif chosen_id:
        st.session_state[_export_sid_key] = chosen_id

    export_sid: str | None = st.session_state.get(_export_sid_key) if not d else None
    if not d:
        st.session_state["dt_resolved_session"] = chosen_id or export_sid

    _exp_disabled = d or not export_sid
    ev_csv = b""
    kpi_csv = b""
    if export_sid:
        ev_csv = _cached_export_events_csv(export_sid)
        kpi_csv = _cached_export_kpi_csv(export_sid)

    _can_start = bool(chosen_id) and not _rec_on and not _replay_live and not d

    with st.container(key="{}_hist_toolbar".format(kp)):
        c_speed, c2, c3, c4 = st.columns(
            4,
            gap="small",
            vertical_alignment="top",
        )
        with c_speed:
            sp_label = st.selectbox(
                "Speed",
                list(speed_presets.keys()),
                index=3,
                key="{}_replay_spd".format(kp),
                label_visibility="collapsed",
                disabled=d or _replay_live,
                help="Playback speed (before Start Replay)",
            )
        speed = speed_presets[sp_label]
        with c2:
            if _replay_live:
                if st.button(
                    "Stop Replay",
                    key="{}_replay_toggle".format(kp),
                    disabled=d,
                    use_container_width=True,
                ):
                    ui_replay_panel._clear_replay_child_and_temp_file()
                    st.rerun()
            else:
                if st.button(
                    "Start Replay",
                    key="{}_replay_toggle".format(kp),
                    disabled=not _can_start,
                    use_container_width=True,
                ):
                    replay_sid = _resolve_history_session_id(
                        _sess_key, _export_sid_key, sel_ids, id_to_label
                    )
                    if not replay_sid:
                        st.error("Choose a session first.")
                    else:
                        st.session_state[_export_sid_key] = replay_sid
                        n_events = neo4j_backend.count_session_events(replay_sid)
                        if n_events < 0:
                            st.error(
                                "Could not read events from the database. "
                                "Check Neo4j connection and try again."
                            )
                        elif n_events <= 0:
                            st.error("No events in this session.")
                        else:
                            ui_replay_panel._clear_replay_child_and_temp_file()
                            _ok_ms, _ms_msg = process_control.ensure_main_service_replay()
                            if not _ok_ms:
                                st.error(_ms_msg)
                            else:
                                _abort = False
                                _local = os.path.normpath(
                                    os.path.join(PROJECT_ROOT, "config_local.json")
                                )
                                if os.path.isfile(_local):
                                    try:
                                        mqtt_backend.switch_config_file("config_local.json")
                                        time.sleep(2)
                                    except Exception as e:
                                        st.error(
                                            "Failed to switch local config: {}".format(e)
                                        )
                                        _abort = True
                                if not _abort:
                                    ui_replay_panel._reset_replay_downstream_for_new_run()
                                    try:
                                        st.session_state.replay_proc = (
                                            mqtt_backend.run_replay_session_subprocess(
                                                replay_sid, speed
                                            )
                                        )
                                    except Exception as ex:
                                        st.error(str(ex))
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
                disabled=_exp_disabled,
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
                disabled=_exp_disabled,
            )

    st.markdown('<hr class="hist-section-divider" />', unsafe_allow_html=True)

    dup_key = "{}_dup_import".format(kp)
    _ifb = _import_feedback_key(kp)
    _dup_wait = dup_key in st.session_state
    do_import = False
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
                disabled=d,
            )
        with _imp_r:
            with st.container(key="{}_import_col".format(kp)):
                if _dup_wait:
                    st.caption("Duplicate session — see options below.")
                do_import = st.button(
                    "Import to Database",
                    key="{}_import_btn".format(kp),
                    use_container_width=True,
                    disabled=d or _dup_wait or up is None,
                )

    if do_import:
        with st.spinner("Importing..."):
            st.session_state.pop(_ifb, None)
            if not up:
                st.session_state[_ifb] = {
                    "level": "error",
                    "text": "Import failed. Choose a CSV file first.",
                }
            else:
                rows = []
                try:
                    text = io.TextIOWrapper(up, encoding="utf-8", errors="replace")
                    rows = list(csv.DictReader(text))
                except Exception as ex:
                    st.session_state[_ifb] = {
                        "level": "error",
                        "text": "Import failed. {}".format(ex),
                    }
                    rows = []
                if not rows and not st.session_state.get(_ifb):
                    st.session_state[_ifb] = {
                        "level": "error",
                        "text": "Import failed. The file has no data rows.",
                    }
                elif rows:
                    missing = [
                        r
                        for r in rows
                        if not str(r.get("time") or "").strip()
                        or "activity" not in r
                        or "component_id" not in r
                    ]
                    if missing:
                        st.session_state[_ifb] = {
                            "level": "error",
                            "text": (
                                "Import failed. Each row must include time, component_id, "
                                "part_id, and activity."
                            ),
                        }
                    else:
                        sorted_rows = sorted(
                            rows,
                            key=lambda r: neo4j_backend._parse_event_time_key(
                                r.get("time")
                            ),
                        )
                        first_raw = str(sorted_rows[0].get("time") or "").strip()
                        n = len(sorted_rows)
                        dup_info = neo4j_backend.find_csv_import_duplicate_info(
                            first_raw, n
                        )
                        if dup_info:
                            st.session_state.pop(_ifb, None)
                            st.session_state[dup_key] = {
                                "rows": sorted_rows,
                                "filename": up.name or "uploaded.csv",
                                "dup_info": dup_info,
                            }
                            st.rerun()
                        else:
                            try:
                                _sid, n_ev, _ = neo4j_backend.import_csv_session(
                                    sorted_rows,
                                    up.name or "uploaded.csv",
                                    force_new_id=False,
                                    display_name=None,
                                )
                                st.session_state[
                                    "{}_pending_select_session".format(kp)
                                ] = _sid
                                st.session_state[_ifb] = {
                                    "level": "success",
                                    "text": "Import successful. {} event(s) imported.".format(
                                        n_ev
                                    ),
                                }
                                _clear_history_caches()
                                st.rerun()
                            except Exception as ex:
                                st.session_state[_ifb] = {
                                    "level": "error",
                                    "text": "Import failed. {}".format(ex),
                                }

    _fbv = st.session_state.get(_ifb)
    if isinstance(_fbv, dict) and str(_fbv.get("text") or "").strip():
        _lvl = str(_fbv.get("level") or "info")
        _ft = str(_fbv["text"])
        if _lvl == "success":
            st.success(_ft, icon="✅")
        elif _lvl == "error":
            st.error(_ft, icon="❌")
        else:
            st.info(_ft, icon="ℹ️")

    if dup_key in st.session_state:
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
            disabled=d,
        ):
            del st.session_state[dup_key]
            st.session_state.pop(_ifb, None)
            st.rerun()
        if b2.button(
            "Import as new session anyway",
            key="{}_dup_force".format(kp),
            use_container_width=True,
            disabled=d,
        ):
            p = st.session_state.pop(dup_key, None)
            if p:
                with st.spinner("Importing..."):
                    try:
                        _sid, n_ev, _ = neo4j_backend.import_csv_session(
                            p["rows"],
                            p["filename"],
                            force_new_id=True,
                            display_name=None,
                        )
                        st.session_state["{}_pending_select_session".format(kp)] = _sid
                        st.session_state[_ifb] = {
                            "level": "success",
                            "text": "Import successful. {} event(s) imported.".format(
                                n_ev
                            ),
                        }
                        _clear_history_caches()
                        st.rerun()
                    except Exception as ex:
                        st.session_state[_ifb] = {
                            "level": "error",
                            "text": "Import failed. {}".format(ex),
                        }
                        st.rerun()

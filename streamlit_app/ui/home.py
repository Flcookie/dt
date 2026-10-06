"""
Main page: global control + dashboard entry links (week6 §六阶段 A).
"""
from __future__ import annotations

import json
import os
import time

import streamlit as st

from paths import PROJECT_ROOT

import services.mqtt_backend as mqtt_backend
import services.physical_workflow as physical_workflow
import services.process_control as process_control
import services.recording as recording
import ui.history_panel as ui_history_panel
import ui.replay_panel as ui_replay_panel


@st.dialog("Control Runs", width="large")
def _service_logs_unified_dialog() -> None:
    """Control run history (Start/Stop/Shutdown/System and related actions)."""
    hist = process_control.read_control_action_history(80)
    if not hist:
        st.info("暂无记录。点击 Start Programs 后，本轮控制动作会显示在此。")
    else:
        for i, row in enumerate(reversed(hist)):
            cmd = str(row.get("cmd") or "—")
            tm = str(row.get("time_full") or row.get("time") or "")
            status = process_control.control_action_status_label(row)
            icon = (
                "✓"
                if status == "successful"
                else ("✗" if status == "failed" else "—")
            )
            title = "{} {} · {} · {}".format(icon, cmd, tm, status)
            with st.expander(title, expanded=(i == 0)):
                body = process_control.format_control_action_record_body(row)
                if body.strip():
                    st.code(body, language=None)
                else:
                    st.caption("(no details)")


def _render_live_section_header(status_html: str, *, logs_disabled: bool) -> None:
    """LIVE 卡片顶栏：左侧标题 + 状态，右上角 View logs（与状态同一视线）。"""
    with st.container(key="home_live_head_row"):
        _left, _btn = st.columns([1, 0.16], gap="small", vertical_alignment="center")
        with _left:
            st.markdown(
                (
                    '<div style="display:flex;align-items:center;flex-wrap:wrap;'
                    'gap:10px 14px;">'
                    '<span style="font-size:22px;font-weight:700;letter-spacing:.04em;'
                    'color:#0284c7;">Live</span>'
                    '<div style="font-size:15px;color:#64748b;display:flex;'
                    'align-items:center;gap:8px;">{status}</div>'
                    "</div>"
                ).format(status=status_html),
                unsafe_allow_html=True,
            )
        with _btn:
            if st.button(
                "View logs",
                key="home_view_logs",
                type="secondary",
                use_container_width=True,
                help="控制动作历史（Start/Stop/Shutdown/System 等）",
                disabled=logs_disabled,
            ):
                _service_logs_unified_dialog()


_HOME_UI_REFRESH_CSS = """
<style>
:root {
  --cp-btn-h: 46px;
}
#MainMenu, header, footer { visibility: hidden; }
.stApp { background: #f6f8fb; }
div[data-testid="stMainBlockContainer"] {
    padding-top: 1rem !important;
}
div[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05) !important;
    margin-bottom: 14px !important;
}
div[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button {
    min-height: var(--cp-btn-h) !important;
    max-height: var(--cp-btn-h) !important;
    height: var(--cp-btn-h) !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    letter-spacing: .01em !important;
    border-radius: 7px !important;
    padding: 0 16px !important;
    width: 100% !important;
    white-space: nowrap !important;
    border: 1px solid #dee2e6 !important;
    border-bottom: 1px solid #dee2e6 !important;
    background: #f8f9fa !important;
    color: #1f2937 !important;
    box-shadow: none !important;
    transition: background .15s ease, border-color .15s ease, color .15s ease !important;
}
div[data-testid="stMainBlockContainer"] div[data-testid="stButton"] > button:hover:not(:disabled) {
    background: #e9ecef !important;
    box-shadow: none !important;
}
.cp-title-wrap {
    margin-bottom:20px;
    display:flex;
    align-items:baseline;
    gap:12px;
}
.cp-title-main {
    font-size:22px;
    font-weight:700;
    color:#1e293b;
    letter-spacing:-.3px;
}
.cp-title-sub { font-size:15px; color:#64748b; }
div[data-testid="stMainBlockContainer"] div.st-key-home_start_progs button,
div[data-testid="stMainBlockContainer"] div.st-key-home_btn_start button,
div[data-testid="stMainBlockContainer"] div.st-key-home_btn_stop button,
div[data-testid="stMainBlockContainer"] div.st-key-home_stop_progs button,
div[data-testid="stMainBlockContainer"] div.st-key-home_shutdown button,
div[data-testid="stMainBlockContainer"] div.st-key-home_ul_code button,
div[data-testid="stMainBlockContainer"] div.st-key-home_ul_cfg button {
    background: #ffffff !important;
    border: 1px solid #dee2e6 !important;
    border-bottom: 1px solid #dee2e6 !important;
    color: #374151 !important;
    font-weight: 600 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_start_progs button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_btn_start button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_btn_stop button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_stop_progs button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_shutdown button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_ul_code button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_ul_cfg button:hover:not(:disabled) {
    background: #f8f9fa !important;
    border-color: #cbd5e1 !important;
    border-bottom-color: #cbd5e1 !important;
    color: #1f2937 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_live_ops_block [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    flex: 1 1 0 !important;
    min-width: 0 !important;
    width: auto !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_live_ops_block button {
    width: 100% !important;
    min-height: var(--cp-btn-h) !important;
    max-height: var(--cp-btn-h) !important;
    height: var(--cp-btn-h) !important;
    padding: 0 10px !important;
    box-sizing: border-box !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_replay_toggle button,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_import_btn button,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_log button,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_kpi button,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_ops_block [data-testid="stDownloadButton"] > button {
    min-height: var(--cp-btn-h) !important;
    max-height: var(--cp-btn-h) !important;
    height: var(--cp-btn-h) !important;
    background: #ffffff !important;
    border: 1px solid #dee2e6 !important;
    border-bottom: 1px solid #dee2e6 !important;
    color: #374151 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_replay_toggle button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_import_btn button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_log button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_kpi button:hover:not(:disabled) {
    background: #f8f9fa !important;
    border-color: #cbd5e1 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_replay_toggle button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_import_btn button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_log button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_dl_kpi button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_ops_block [data-testid="stDownloadButton"] > button:disabled {
    opacity: 0.45 !important;
    cursor: not-allowed !important;
    color: #94a3b8 !important;
    border-color: #e2e8f0 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_live_head_row {
    margin: -16px -16px 10px -16px !important;
    padding: 10px 14px 9px !important;
    border-bottom: 1px solid #f1f3f5 !important;
    border-left: 4px solid #0284c7 !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_view_logs button {
    background: #ffffff !important;
    border: 1px solid #dee2e6 !important;
    border-bottom: 1px solid #dee2e6 !important;
    color: #374151 !important;
    min-height: 34px !important;
    max-height: 34px !important;
    height: 34px !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    padding: 0 14px !important;
    border-radius: 8px !important;
    white-space: nowrap !important;
    width: 100% !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_view_logs button:hover:not(:disabled) {
    background: #f8f9fa !important;
    border-color: #cbd5e1 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_live button,
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_local button {
    height: 84px !important;
    min-height: 84px !important;
    width: 100% !important;
    margin-top: -84px !important;
    margin-bottom: 0 !important;
    padding: 0 !important;
    font-size: 1px !important;
    background: transparent !important;
    border: none !important;
    color: transparent !important;
    box-shadow: none !important;
    border-radius: 10px !important;
    position: relative !important;
    z-index: 5 !important;
    cursor: pointer !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_live button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_local button:hover:not(:disabled) {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_live button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-cp_card_local button:disabled {
    opacity: 0 !important;
}
div[data-testid="stMainBlockContainer"] .nav-card-hover:hover {
    background: #f0f4f8 !important;
    border-color: #c8d0da !important;
}
div[data-testid="stSelectbox"] > div > div {
    font-size: 15px !important;
    border-radius: 8px !important;
    border-color: #dee2e6 !important;
}
div[data-testid="stFileUploader"] section {
    border: 1.5px dashed #dee2e6 !important;
    border-radius: 8px !important;
    background: #fafafa !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_kpi button,
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_twin button,
div[data-testid="stMainBlockContainer"] div.st-key-home_what_if button {
    min-height: 84px !important;
    height: 84px !important;
    width: 100% !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding: 12px 18px !important;
    font-size: 20px !important;
    line-height: 1.05 !important;
    font-weight: 700 !important;
    background: #eef2f7 !important;
    border: 1px solid #c9d2df !important;
    border-bottom: 1px solid #c9d2df !important;
    color: #1f2937 !important;
    box-shadow: none !important;
    border-radius: 10px !important;
    position: static !important;
    z-index: auto !important;
    cursor: pointer !important;
    justify-content: center !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_kpi button p,
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_twin button p,
div[data-testid="stMainBlockContainer"] div.st-key-home_what_if button p {
    font-family: var(--sans) !important;
    font-size: 20px !important;
    line-height: 1.05 !important;
    font-weight: 700 !important;
    color: #1f2937 !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_kpi button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_twin button:hover:not(:disabled),
div[data-testid="stMainBlockContainer"] div.st-key-home_what_if button:hover:not(:disabled) {
    background: #f0f4f8 !important;
    border: 1px solid #c8d0da !important;
    border-bottom: 1px solid #c8d0da !important;
    color: #1f2937 !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_kpi button:disabled,
div[data-testid="stMainBlockContainer"] div.st-key-home_dash_twin button:disabled {
    opacity: 0.45 !important;
    cursor: not-allowed !important;
}
div[data-testid="stMainBlockContainer"] .cp-nav-tile {
    border: 1px solid #c9d2df !important;
    background: #eef2f7 !important;
    border-radius: 10px !important;
    min-height: 84px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 12px !important;
    padding: 12px 16px !important;
    box-sizing: border-box !important;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08) !important;
    transition: background .15s ease, border-color .15s ease, box-shadow .15s ease !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_nav_kpi_wrap,
div[data-testid="stMainBlockContainer"] div.st-key-home_nav_twin_wrap {
    position: relative !important;
    min-height: 84px !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_nav_kpi_wrap [data-testid="stMarkdownContainer"],
div[data-testid="stMainBlockContainer"] div.st-key-home_nav_twin_wrap [data-testid="stMarkdownContainer"] {
    pointer-events: none !important;
}
div[data-testid="stMainBlockContainer"] .cp-nav-icon {
    width: 34px !important;
    height: 34px !important;
    border-radius: 8px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    flex-shrink: 0 !important;
}
div[data-testid="stMainBlockContainer"] .cp-nav-title {
    font-size: 20px !important;
    line-height: 1.05 !important;
    font-weight: 700 !important;
    color: #1f2937 !important;
    font-family: var(--sans) !important;
    text-align: center !important;
}
div[data-testid="stMainBlockContainer"] .cp-nav-sub {
    margin-top: 3px !important;
    font-size: 15px !important;
    line-height: 1.2 !important;
    color: #6b7280 !important;
    font-family: var(--sans) !important;
}
</style>
"""


def section_title(
    text: str, color: str, right_html: str = "", accent: str = ""
) -> None:
    left = "border-left:4px solid {};".format(accent) if accent else ""
    st.markdown(
        """
<div style="display:flex;align-items:center;justify-content:space-between;
padding:10px 18px 9px;border-bottom:1px solid #f1f3f5;margin:-16px -16px 10px -16px;{left}">
  <span style="font-size:22px;font-weight:700;letter-spacing:.04em;color:{color}">{text}</span>
  <div style="font-size:15px;color:#64748b;display:flex;align-items:center;gap:8px">{right_html}</div>
</div>
""".format(
            left=left, color=color, text=text, right_html=right_html
        ),
        unsafe_allow_html=True,
    )


def sub_label(text: str = "") -> None:
    if not text:
        return
    st.markdown(
        (
            '<p style="font-size:15px;font-weight:700;color:#adb5bd;'
            'letter-spacing:.05em;text-transform:uppercase;margin:4px 0 8px 0">{}</p>'
        ).format(text),
        unsafe_allow_html=True,
    )


def hr() -> None:
    st.markdown(
        '<hr style="border:none;border-top:1px solid #f1f3f5;margin:14px 0 12px">',
        unsafe_allow_html=True,
    )


def _replay_session_active() -> bool:
    p = st.session_state.get("replay_proc")
    if p is not None and p.poll() is None:
        return True
    kpi, _ = mqtt_backend.get_kpi_snapshot()
    return (kpi or {}).get("run_mode") == "replay"


def _validate_config_basename(basename: str) -> None:
    p = os.path.normpath(os.path.join(PROJECT_ROOT, basename))
    if not os.path.isfile(p):
        raise FileNotFoundError("Missing file: {}".format(basename))
    with open(p, encoding="utf-8") as f:
        json.load(f)


def _local_config_basename() -> str:
    cl = os.path.normpath(os.path.join(PROJECT_ROOT, "config_local.json"))
    return "config_local.json" if os.path.isfile(cl) else "config.json"


def _render_data_source_configuration() -> None:
    ds = st.session_state.get("cp_data_source")
    live_active = ds == "live"
    hist_active = ds == "local"
    chk_svg = (
        '<svg width="9" height="7" viewBox="0 0 9 7" fill="none">'
        '<path d="M1 3l2.5 2.5L8 1" stroke="#fff" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )

    def _src_card_html(
        active: bool,
        border_color: str,
        bg: str,
        chk_style: str,
        chk_inner: str,
        dot_color: str,
        name: str,
        desc: str,
    ) -> str:
        desc_html = (
            '<div style="font-size:13px;color:#64748b;margin-top:2px">{}</div>'.format(desc)
            if str(desc).strip()
            else ""
        )
        return """
<div style="border:{bw} solid {bc};background:{bg};border-radius:10px;padding:14px 16px;
display:flex;align-items:center;gap:12px;">
  <div style="width:17px;height:17px;border-radius:3px;flex-shrink:0;display:flex;
  align-items:center;justify-content:center;{cs}">{ci}</div>
  <div style="width:9px;height:9px;border-radius:50%;background:{dc};flex-shrink:0"></div>
  <div>
    <div style="font-size:18px;font-weight:700;color:#1e293b">{nm}</div>
    {dh}
  </div>
</div>
""".format(
            bw="2px" if active else "1px",
            bc=border_color,
            bg=bg,
            cs=chk_style,
            ci=chk_inner,
            dc=dot_color,
            nm=name,
            dh=desc_html,
        )

    section_title("Mode", "#64748b", accent="#64748b")
    err = st.session_state.get("cp_config_load_error")
    if err:
        st.error("Failed to load config: **{}**".format(err))

    c1, c2 = st.columns(2, gap="small")
    with c1:
        st.markdown(
            _src_card_html(
                live_active,
                "#0284c7" if live_active else "#dee2e6",
                "#f0f9ff" if live_active else "#fff",
                "background:#0284c7;border:2px solid #0284c7"
                if live_active
                else "border:1.5px solid #ced4da",
                chk_svg if live_active else "",
                "#22c55e",
                "Live Monitoring",
                "",
            ),
            unsafe_allow_html=True,
        )
        if st.button(
            " ",
            key="cp_card_live",
            disabled=live_active,
            help="config.json (lab)",
            use_container_width=True,
        ):
            if ds != "live":
                try:
                    ui_replay_panel.ensure_replay_session_state()
                    ui_replay_panel._clear_replay_child_and_temp_file()
                    mqtt_backend.clear_kpi_snapshot()
                    _validate_config_basename("config.json")
                    mqtt_backend.switch_config_file("config.json")
                    st.session_state.cp_data_source = "live"
                    st.session_state.pop("dt_resolved_session", None)
                    st.session_state.cp_config_load_error = None
                except Exception as e:
                    st.session_state.cp_config_load_error = str(e)
                st.rerun()

    with c2:
        st.markdown(
            _src_card_html(
                hist_active,
                "#e67700" if hist_active else "#dee2e6",
                "#fff9db" if hist_active else "#fff",
                "background:#e67700;border:2px solid #e67700"
                if hist_active
                else "border:1.5px solid #ced4da",
                chk_svg if hist_active else "",
                "#f59f00",
                "History Replay",
                "",
            ),
            unsafe_allow_html=True,
        )
        if st.button(
            " ",
            key="cp_card_local",
            disabled=hist_active,
            help="Prefer config_local.json",
            use_container_width=True,
        ):
            if ds != "local":
                try:
                    if ds == "live":
                        ui_replay_panel.ensure_replay_session_state()
                        ui_replay_panel._clear_replay_child_and_temp_file()
                    mqtt_backend.clear_kpi_snapshot()
                    fn = _local_config_basename()
                    _validate_config_basename(fn)
                    mqtt_backend.switch_config_file(fn)
                    st.session_state.cp_data_source = "local"
                    st.session_state.cp_config_load_error = None
                except Exception as e:
                    st.session_state.cp_config_load_error = str(e)
                st.rerun()


# Greys out and blocks the Live header + controls (no mode chosen, History mode, or busy).
_LIVE_LOCKED_CSS = """
<style>
div[data-testid="stMainBlockContainer"] div.st-key-home_live_head_row,
div[data-testid="stMainBlockContainer"] div.st-key-home_live_ops_block {
  pointer-events: none !important;
  opacity: 0.48 !important;
  filter: grayscale(0.15);
  user-select: none;
}
div[data-testid="stMainBlockContainer"] div.st-key-home_live_head_row button,
div[data-testid="stMainBlockContainer"] div.st-key-home_live_ops_block button {
  cursor: not-allowed !important;
}
</style>
"""

# Greys out and blocks the History block (no mode chosen, or Live mode).
_HIST_LOCKED_CSS = """
<style>
div[data-testid="stMainBlockContainer"] div.st-key-home_hist_ops_block {
  pointer-events: none !important;
  opacity: 0.48 !important;
  filter: grayscale(0.15);
  user-select: none;
}
</style>
"""

_BG_BUSY_TOAST = "Wait for the background operation to finish."


def render() -> None:
    """Control + deploy + entry links. Caller must have set page_config and sidebar."""
    st.markdown(_HOME_UI_REFRESH_CSS, unsafe_allow_html=True)

    if "cp_data_source" not in st.session_state:
        st.session_state.cp_data_source = None

    st.markdown(
        '<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:20px">'
        '<span style="font-size:22px;font-weight:700;color:#1e293b;letter-spacing:-.3px">DASHBOARD</span>'
        '<span style="font-size:15px;color:#64748b">MOTOWN Digital Twin</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    ds = st.session_state.get("cp_data_source")
    lock_all = ds is None
    is_live = ds == "live"
    is_local = ds == "local"

    replay_active = _replay_session_active()
    bg_busy = process_control.is_control_operation_running()
    # Live controls are usable only in Live mode while no replay is running.
    live_btn_disabled = lock_all or is_local or replay_active

    if is_live and replay_active:
        st.info(
            "**Replay mode** — stop replay or use **Stop System** before starting the line."
        )

    # Upload buttons need local_code_paths / local_config_paths in the active site config.
    _cfg_name = mqtt_backend.active_config_name() if not lock_all else "config.json"
    _cfg_path = os.path.normpath(os.path.join(PROJECT_ROOT, _cfg_name))
    try:
        with open(_cfg_path, encoding="utf-8") as f:
            _site_cfg = json.load(f)
    except OSError:
        _site_cfg = {}
    local_code_paths = _site_cfg.get("local_code_paths")
    local_config_paths = _site_cfg.get("local_config_paths")

    # Data Source
    with st.container(border=True):
        _render_data_source_configuration()

    _render_live_section(
        _live_status_html(is_live),
        lock_all=lock_all,
        locked=lock_all or is_local or bg_busy,
        bg_busy=bg_busy,
        buttons_disabled=live_btn_disabled,
        local_code_paths=local_code_paths,
        local_config_paths=local_config_paths,
    )

    # History
    with st.container(border=True):
        section_title("History", "#e67700", accent="#e67700")
        hist_disabled = lock_all or is_live
        if hist_disabled:
            st.markdown(_HIST_LOCKED_CSS, unsafe_allow_html=True)
        with st.container(key="home_hist_ops_block"):
            ui_history_panel.render_history_panel(
                key_prefix="home_hist", disabled=hist_disabled
            )

    # Navigate
    with st.container(border=True):
        section_title("Navigate", "#64748b", accent="#64748b")
        n1, n2 = st.columns(2, gap="small")
        with n1:
            if st.button(
                "KPI Dashboard",
                key="home_dash_kpi",
                use_container_width=True,
                disabled=lock_all,
            ):
                st.switch_page("pages/01_Realtime.py")
        with n2:
            if st.button(
                "Digital Twin",
                key="home_dash_twin",
                use_container_width=True,
                disabled=lock_all,
            ):
                st.switch_page("pages/05_Digital_Twin.py")

    # Other Services
    with st.container(border=True):
        section_title("Other Services", "#adb5bd", accent="#adb5bd")
        if st.button(
            "What-if Analysis",
            key="home_what_if",
            use_container_width=True,
        ):
            st.switch_page("pages/what-if-analysis.py")


def _live_status_html(is_live: bool) -> str:
    """"Programs: … | System: …" badge line; real status only in Live Monitoring mode."""
    if is_live:
        prog_raw = process_control.get_programs_status()
        sys_raw = process_control.get_system_status()
        prog_label = {
            "activated": "Activated",
            "partial": "Partial",
            "deactivated": "Deactivated",
        }.get(prog_raw, "Unknown")
        prog_color = {
            "activated": "#2f9e44",
            "partial": "#e67700",
            "deactivated": "#64748b",
        }.get(prog_raw, "#64748b")
        sys_color = "#22c55e" if sys_raw == "start" else "#64748b"
        sys_label = "Running" if sys_raw == "start" else "Stopped"
    else:
        prog_label = "—"
        sys_label = "—"
        prog_color = "#cbd5e1"
        sys_color = "#cbd5e1"

    def _dot(c: str) -> str:
        return (
            '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
            "background:{};vertical-align:middle;margin-right:4px\"></span>".format(c)
        )

    def _vbar() -> str:
        return (
            '<span style="display:inline-block;width:1px;height:14px;background:#dee2e6;'
            'vertical-align:middle;margin:0 8px"></span>'
        )

    return (
        "{}Programs: <b style='color:#212529'>{}</b>{}"
        "{}System: <b style='color:#212529'>{}</b>"
    ).format(
        _dot(prog_color),
        prog_label,
        _vbar(),
        _dot(sys_color),
        sys_label,
    )


def _render_live_section(
    status_html: str,
    *,
    lock_all: bool,
    locked: bool,
    bg_busy: bool,
    buttons_disabled: bool,
    local_code_paths,
    local_config_paths,
) -> None:
    """Live card: status + View logs, CONTROL (programs / line / shutdown), DEPLOY (uploads).

    ``locked`` only greys the card out (CSS); ``buttons_disabled`` disables the widgets.
    While ``bg_busy`` the buttons stay clickable but every action only shows a toast.
    """
    with st.container(border=True):
        _render_live_section_header(status_html, logs_disabled=lock_all)

        if bg_busy:
            st.caption("Background operation in progress — controls paused until it finishes.")

        if locked:
            st.markdown(_LIVE_LOCKED_CSS, unsafe_allow_html=True)

        with st.container(key="home_live_ops_block"):
            sub_label("CONTROL")
            c1, c2, c3, c4, c5 = st.columns(5, gap="small")
            with c1:
                if st.button(
                    "Start Programs",
                    key="home_start_progs",
                    use_container_width=True,
                    disabled=buttons_disabled,
                ):
                    _submit_program_script(
                        "Start Programs", "start_programs", bg_busy=bg_busy, reset_log=True
                    )
            with c2:
                if st.button(
                    "Start System",
                    key="home_btn_start",
                    use_container_width=True,
                    disabled=buttons_disabled,
                ):
                    _run_line_action(
                        "Start System",
                        physical_workflow.start_physical_line_integrated,
                        bg_busy=bg_busy,
                    )
            with c3:
                if st.button(
                    "Stop System",
                    key="home_btn_stop",
                    use_container_width=True,
                    disabled=buttons_disabled,
                ):
                    _run_line_action(
                        "Stop System",
                        physical_workflow.stop_physical_line_integrated,
                        bg_busy=bg_busy,
                    )
            with c4:
                if st.button(
                    "Stop Programs",
                    key="home_stop_progs",
                    use_container_width=True,
                    disabled=buttons_disabled,
                ):
                    _submit_program_script("Stop Programs", "stop_programs", bg_busy=bg_busy)
            with c5:
                if st.button(
                    "Shutdown",
                    key="home_shutdown",
                    use_container_width=True,
                    disabled=buttons_disabled,
                ):
                    _submit_program_script("Shutdown", "shutdown", bg_busy=bg_busy)

            hr()
            sub_label("DEPLOY")
            d1, d2, _, _, _ = st.columns(5, gap="small")
            with d1:
                if st.button(
                    "Upload Code",
                    key="home_ul_code",
                    use_container_width=True,
                    disabled=buttons_disabled or not local_code_paths,
                ):
                    _submit_upload(
                        "Upload Code",
                        "upload_code",
                        local_code_paths,
                        paths_key="local_code_paths",
                        bg_busy=bg_busy,
                    )
            with d2:
                if st.button(
                    "Upload Config",
                    key="home_ul_cfg",
                    use_container_width=True,
                    disabled=buttons_disabled or not local_config_paths,
                ):
                    _submit_upload(
                        "Upload Config",
                        "upload_config",
                        local_config_paths,
                        paths_key="local_config_paths",
                        bg_busy=bg_busy,
                    )

        if recording.is_recording():
            st.warning("Recording · **{}**".format(recording.current_path() or ""))


def _submit_program_script(
    label: str, script_name: str, *, bg_busy: bool, reset_log: bool = False
) -> None:
    """Start / Stop Programs and Shutdown: run the repo-root script in the background.

    Unless recording, the active config is re-applied first (MQTT reconnect + 1 s pause).
    ``reset_log`` starts a new control-log session (Start Programs only).
    Failures are toasted and recorded as a failed control action.
    """
    if bg_busy:
        st.toast(_BG_BUSY_TOAST)
        return
    try:
        enforce_config = mqtt_backend.active_config_name()
        if not recording.is_recording():
            mqtt_backend.switch_config_file(enforce_config)
            time.sleep(1.0)
        if reset_log:
            process_control.reset_control_log_session()
        process_control.run_script_background(script_name, enforce_config=enforce_config)
        st.toast("{} submitted. Check View Logs.".format(label))
    except Exception as ex:
        st.toast("{} failed. Check View Logs.".format(label))
        process_control.record_control_action(label, False, str(ex))


def _run_line_action(label: str, action, *, bg_busy: bool) -> None:
    """Start / Stop System: run the integrated line workflow now and record its result.

    Exceptions from ``action`` are not caught.
    """
    if bg_busy:
        st.toast(_BG_BUSY_TOAST)
        return
    ok, msg = action()
    process_control.record_control_action(label, ok, (msg or "")[:800])
    if ok:
        st.toast("{} done. See View Logs for details.".format(label))
    else:
        st.toast("{} failed. See View Logs for details.".format(label))


def _submit_upload(label: str, script_name: str, paths, *, paths_key: str, bg_busy: bool) -> None:
    """Upload Code / Config: run the repo-root upload script in the background (no config switch)."""
    if bg_busy:
        st.toast(_BG_BUSY_TOAST)
    elif not paths:
        st.toast("{} unavailable: {} empty.".format(label, paths_key))
    else:
        enforce_config = mqtt_backend.active_config_name()
        process_control.run_script_background(script_name, enforce_config=enforce_config)
        st.toast("{} submitted. Check View Logs.".format(label))

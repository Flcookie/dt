"""What-if Analysis — Arena SIMAN Input/Output sweep."""
from __future__ import annotations

import html as html_module
import os
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from whatif.siman_runner import (
    PARAMETER_KEYS,
    default_work_folder,
    parameter_default,
    replications_default,
    run_parameter_sweep,
    sweep_to_chart_series,
    work_folder_ready,
)

# 与 ui_kpi_display._UI_THEME / Home Other Services 对齐
_WI_ACCENT = "#adb5bd"
_WI_TEXT = "#1e293b"
_WI_TEXT_DIM = "#64748b"
_WI_BORDER2 = "#cbd5e1"
_WI_BTN_BG = "#f8f9fa"
_WI_BTN_BORDER = "#dee2e6"
_WI_BTN_TEXT = "#1f2937"
_WI_BTN_HOVER = "#e9ecef"

_FONT_L2_PX = 22
_FONT_L3_PX = 15
_FONT_CHART_TITLE_PX = 17
_CHART_BOX_H_PX = 280
_PLOT_FONT = '"Barlow Condensed","Segoe UI",sans-serif'
_CHART_PLOT_CONFIG = {"displayModeBar": False}

WHAT_IF_PARAMETERS: tuple[str, ...] = PARAMETER_KEYS

_CHART_TITLES: tuple[str, ...] = (
    "WIP",
    "Completion Rate",
    "Scrap Rate",
    "Lead Time",
)

_RUN_ERR_KEY = "_what_if_run_error"
_WORK_FOLDER_KEY = "what_if_work_folder"
_SWEEP_RESULT_KEY = "_what_if_sweep_result"

# Work Folder path 85% | Select Folder 15%
_WORK_FOLDER_COL_WEIGHTS = [85, 15]
# Parameter 35% | From/To/Step 10% each | Replications 12% | Run 10%
_CFG_COL_WEIGHTS = [35, 10, 10, 10, 12, 10]

_WHAT_IF_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700&display=swap');
div[data-testid="stMainBlockContainer"] div.st-key-what_if_panel
[data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.85rem 1rem 3rem 1rem !important;
    box-sizing: border-box !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
}
div[data-testid="stMainBlockContainer"] .what-if-cfg-heading {
    margin: 0 0 0.55rem 0 !important;
    padding: 0 0 0 10px !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L2_PX) + """px !important;
    font-weight: 700 !important;
    line-height: 1.25 !important;
    letter-spacing: 0.02em !important;
    color: """ + _WI_TEXT + """ !important;
    border-left: 4px solid """ + _WI_ACCENT + """ !important;
}
div[data-testid="stMainBlockContainer"] .what-if-section-divider {
    margin: 1rem 0 1.05rem 0 !important;
    border: none !important;
    border-top: 1px solid #e2e8f0 !important;
    height: 0 !important;
}
div[data-testid="stMainBlockContainer"] .what-if-cfg-label {
    margin: 0 0 0.15rem 0 !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    font-weight: 700 !important;
    color: """ + _WI_TEXT_DIM + """ !important;
    letter-spacing: 0.02em !important;
    white-space: nowrap !important;
}
div[data-testid="stMainBlockContainer"] .what-if-chart-title {
    margin: 0 0 10px 0 !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_CHART_TITLE_PX) + """px !important;
    font-weight: 600 !important;
    color: """ + _WI_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_parameter_wrap
[data-testid="stPopover"] > button {
    min-height: 46px !important;
    height: 46px !important;
    width: 100% !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    font-weight: 400 !important;
    color: """ + _WI_TEXT + """ !important;
    background: #ffffff !important;
    border: 1px solid """ + _WI_BTN_BORDER + """ !important;
    border-radius: 7px !important;
    box-shadow: none !important;
    justify-content: space-between !important;
    text-align: left !important;
    padding: 0 0.75rem !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_parameter_wrap
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
    min-height: 46px !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    color: """ + _WI_TEXT + """ !important;
    background: #ffffff !important;
    border: 1px solid """ + _WI_BTN_BORDER + """ !important;
    border-radius: 7px !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_parameter_wrap
[data-testid="stPopover"] > button:hover:not(:disabled) {
    background: #ffffff !important;
    border-color: """ + _WI_BTN_BORDER + """ !important;
    color: """ + _WI_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_parameter_wrap
[data-testid="stPopoverBody"] [data-testid="stRadio"] label {
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    color: """ + _WI_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_parameter_wrap
[data-testid="stPopoverBody"] [data-testid="stRadio"] label span {
    color: """ + _WI_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_panel
[data-testid="stNumberInput"] button,
div[data-testid="stMainBlockContainer"] div.st-key-what_if_panel
[data-testid="stNumberInputStepUp"],
div[data-testid="stMainBlockContainer"] div.st-key-what_if_panel
[data-testid="stNumberInputStepDown"] {
    display: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_panel
[data-testid="stNumberInput"] input {
    padding-right: 0.65rem !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_cfg_row
[data-testid="stHorizontalBlock"] {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    align-items: flex-end !important;
    width: 100% !important;
    gap: 0.4rem !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_cfg_row
[data-testid="stHorizontalBlock"] > [data-testid="column"] {
    min-width: 0 !important;
    flex-shrink: 1 !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_run_btn button {
    min-height: 46px !important;
    height: 46px !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    background: """ + _WI_BTN_BG + """ !important;
    color: """ + _WI_BTN_TEXT + """ !important;
    border: 1px solid """ + _WI_BTN_BORDER + """ !important;
    border-bottom: 1px solid """ + _WI_BTN_BORDER + """ !important;
    font-weight: 600 !important;
    white-space: nowrap !important;
    border-radius: 7px !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_run_btn button:hover:not(:disabled) {
    background: """ + _WI_BTN_HOVER + """ !important;
    border-color: """ + _WI_BTN_BORDER + """ !important;
    color: """ + _WI_BTN_TEXT + """ !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_charts_block {
    padding-bottom: 0.35rem !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_charts_row2 {
    margin-top: 20px !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_work_folder_row {
    margin-bottom: 0.65rem !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_work_folder_row
[data-testid="stTextInput"] input:disabled {
    min-height: 46px !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    color: """ + _WI_TEXT + """ !important;
    background: #f8f9fa !important;
    border: 1px solid """ + _WI_BTN_BORDER + """ !important;
    border-radius: 7px !important;
    opacity: 1 !important;
    -webkit-text-fill-color: """ + _WI_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_select_folder_btn button {
    min-height: 46px !important;
    height: 46px !important;
    font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
    font-size: """ + str(_FONT_L3_PX) + """px !important;
    background: """ + _WI_BTN_BG + """ !important;
    color: """ + _WI_BTN_TEXT + """ !important;
    border: 1px solid """ + _WI_BTN_BORDER + """ !important;
    font-weight: 600 !important;
    white-space: nowrap !important;
    border-radius: 7px !important;
    box-shadow: none !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_select_folder_btn button:hover:not(:disabled) {
    background: """ + _WI_BTN_HOVER + """ !important;
    border-color: """ + _WI_BTN_BORDER + """ !important;
    color: """ + _WI_BTN_TEXT + """ !important;
}
div[data-testid="stMainBlockContainer"] div.st-key-what_if_run_error {
    margin: 0.35rem 0 0 0 !important;
}
</style>
"""


def _pick_work_folder() -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        picked = filedialog.askdirectory(parent=root)
    finally:
        root.destroy()
    return picked or None


def _active_work_folder() -> str:
    raw = str(st.session_state.get(_WORK_FOLDER_KEY, "") or "").strip()
    return raw if raw else str(default_work_folder())


def _validate_run_inputs() -> str | None:
    work_folder = _active_work_folder()
    if not os.path.isdir(work_folder):
        return "Work Folder does not exist."
    if not work_folder_ready(work_folder):
        return f"Folder must contain model.p, Input.txt, and Config.txt: {work_folder}"

    raw_from = st.session_state.get("what_if_range_from")
    raw_to = st.session_state.get("what_if_range_to")
    raw_step = st.session_state.get("what_if_step")
    raw_rep = st.session_state.get("what_if_replications")

    try:
        from_val = float(raw_from)
        to_val = float(raw_to)
        step_val = float(raw_step)
        rep_val = int(raw_rep)
    except (TypeError, ValueError):
        return "From, To, Step, and Replications must be numbers."

    if to_val < from_val:
        return "To must be greater than or equal to From."
    if step_val <= 0:
        return "Step must be greater than 0."
    if rep_val < 1:
        return "Replications must be at least 1."

    n_points = int((to_val - from_val) / step_val) + 1
    if n_points > 15:
        return f"Too many sweep points ({n_points}). Use a larger step (max 15)."

    return None


def _default_range_for_parameter(param: str) -> tuple[int, int, int]:
    base = int(round(parameter_default(param, _active_work_folder())))
    lo = max(1, base - 4)
    hi = base + 4
    return lo, hi, 1


def _block_heading(text: str) -> None:
    st.markdown(
        '<p class="what-if-cfg-heading">{}</p>'.format(html_module.escape(text)),
        unsafe_allow_html=True,
    )


def _section_divider() -> None:
    st.markdown('<hr class="what-if-section-divider">', unsafe_allow_html=True)


def _cfg_label(text: str) -> None:
    st.markdown(
        '<div class="what-if-cfg-label">{}</div>'.format(html_module.escape(text)),
        unsafe_allow_html=True,
    )


def _fig_sensitivity_chart(
    *,
    xs: list[float],
    ys: list[float],
) -> go.Figure:
    fig = go.Figure()
    if xs and ys:
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines+markers",
                line=dict(color="#475569", width=2),
                marker=dict(size=7, color="#334155"),
                hovertemplate="%{y:.4f}<extra></extra>",
                showlegend=False,
            )
        )
    x_min, x_max = (min(xs), max(xs)) if xs else (0.0, 1.0)
    if x_min == x_max:
        pad = max(1.0, abs(float(x_min)) * 0.1)
        x_range = [x_min - pad, x_max + pad]
    else:
        x_range = [x_min, x_max]
    y_max = max(max(ys) * 1.12, 1.0) if ys else 10.0
    ax_font = dict(size=_FONT_L3_PX, color=_WI_TEXT_DIM, family=_PLOT_FONT)
    fig.update_layout(
        height=_CHART_BOX_H_PX,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(t=12, b=32, l=40, r=16),
        font=dict(family=_PLOT_FONT, size=_FONT_L3_PX, color=_WI_TEXT_DIM),
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_xaxes(
        title=None,
        tickfont=ax_font,
        range=x_range,
        showgrid=False,
        zeroline=False,
        showline=True,
        linecolor=_WI_BORDER2,
        mirror=False,
    )
    fig.update_yaxes(
        title=None,
        tickfont=ax_font,
        range=[0.0, y_max],
        showgrid=True,
        gridcolor="#e2e8f0",
        gridwidth=1,
        zeroline=True,
        zerolinecolor=_WI_BORDER2,
        zerolinewidth=1,
        showline=True,
        linecolor=_WI_BORDER2,
        mirror=False,
    )
    return fig


def _render_chart(title: str, *, chart_key: str, series: dict[str, list[float]] | None) -> None:
    st.markdown(
        '<p class="what-if-chart-title">{}</p>'.format(html_module.escape(title)),
        unsafe_allow_html=True,
    )
    if series and series.get("x"):
        fig = _fig_sensitivity_chart(xs=series["x"], ys=series.get(title, []))
    else:
        fig = _fig_sensitivity_chart(xs=[], ys=[])
    st.plotly_chart(
        fig,
        use_container_width=True,
        config=_CHART_PLOT_CONFIG,
        key=chart_key,
    )


def _run_sweep() -> None:
    work_folder = _active_work_folder()
    parameter = str(st.session_state.get("what_if_parameter", WHAT_IF_PARAMETERS[0]))
    from_val = float(st.session_state["what_if_range_from"])
    to_val = float(st.session_state["what_if_range_to"])
    step_val = float(st.session_state["what_if_step"])
    replications = int(st.session_state["what_if_replications"])

    with st.spinner("Running analysis…"):
        try:
            result = run_parameter_sweep(
                work_folder=work_folder,
                parameter=parameter,
                from_val=from_val,
                to_val=to_val,
                step=step_val,
                replications=replications,
                progress_cb=None,
            )
            st.session_state[_SWEEP_RESULT_KEY] = sweep_to_chart_series(result)
        except Exception as exc:
            st.error(str(exc))


def render_what_if_panel() -> None:
    st.markdown(_WHAT_IF_CSS, unsafe_allow_html=True)

    if _WORK_FOLDER_KEY not in st.session_state:
        st.session_state[_WORK_FOLDER_KEY] = str(default_work_folder())
    if "what_if_replications" not in st.session_state:
        st.session_state["what_if_replications"] = replications_default(
            st.session_state[_WORK_FOLDER_KEY]
        )

    with st.container(border=True, key="what_if_panel"):
        _block_heading("Configuration")

        with st.container(key="what_if_work_folder_row"):
            c_path, c_btn = st.columns(
                _WORK_FOLDER_COL_WEIGHTS,
                gap="small",
                vertical_alignment="bottom",
            )
            with c_path:
                _cfg_label("Work Folder")
                st.text_input(
                    "Work Folder",
                    disabled=True,
                    label_visibility="collapsed",
                    key=_WORK_FOLDER_KEY,
                )
            with c_btn:
                _cfg_label("\u00a0")
                if st.button(
                    "Select Folder",
                    key="what_if_select_folder_btn",
                    use_container_width=True,
                ):
                    picked = _pick_work_folder()
                    if picked:
                        st.session_state[_WORK_FOLDER_KEY] = str(Path(picked))
                        st.session_state["what_if_replications"] = replications_default(
                            picked
                        )
                        st.session_state[_RUN_ERR_KEY] = ""
                        st.rerun()

        _model_ok = work_folder_ready(_active_work_folder())
        param = str(st.session_state.get("what_if_parameter", WHAT_IF_PARAMETERS[0]))
        if param not in WHAT_IF_PARAMETERS:
            param = WHAT_IF_PARAMETERS[0]
        lo, hi, step = _default_range_for_parameter(param)

        with st.container(key="what_if_cfg_row"):
            c_param, c_from, c_to, c_step, c_rep, c_run = st.columns(
                _CFG_COL_WEIGHTS,
                gap="small",
                vertical_alignment="bottom",
            )
            with c_param:
                _cfg_label("Parameter")
                with st.container(key="what_if_parameter_wrap"):
                    st.selectbox(
                        "Parameter",
                        WHAT_IF_PARAMETERS,
                        label_visibility="collapsed",
                        disabled=not _model_ok,
                        key="what_if_parameter",
                    )
            with c_from:
                _cfg_label("From")
                st.number_input(
                    "From",
                    min_value=0,
                    value=lo,
                    step=1,
                    format="%d",
                    label_visibility="collapsed",
                    disabled=not _model_ok,
                    key="what_if_range_from",
                )
            with c_to:
                _cfg_label("To")
                st.number_input(
                    "To",
                    min_value=0,
                    value=hi,
                    step=1,
                    format="%d",
                    label_visibility="collapsed",
                    disabled=not _model_ok,
                    key="what_if_range_to",
                )
            with c_step:
                _cfg_label("Step")
                st.number_input(
                    "Step",
                    min_value=1,
                    value=step,
                    step=1,
                    format="%d",
                    label_visibility="collapsed",
                    disabled=not _model_ok,
                    key="what_if_step",
                )
            with c_rep:
                _cfg_label("Replications")
                st.number_input(
                    "Replications",
                    min_value=1,
                    step=1,
                    format="%d",
                    label_visibility="collapsed",
                    disabled=not _model_ok,
                    key="what_if_replications",
                    help="Writes Config.txt ReplicasNum (WarmUp / SimLength kept from Config.txt).",
                )
            with c_run:
                _cfg_label("\u00a0")
                if st.button(
                    "Run Analysis",
                    key="what_if_run_btn",
                    use_container_width=True,
                    disabled=not _model_ok,
                ):
                    err = _validate_run_inputs()
                    if err:
                        st.session_state[_RUN_ERR_KEY] = err
                    else:
                        st.session_state[_RUN_ERR_KEY] = ""
                        _run_sweep()

        if not _model_ok:
            st.warning(
                "Work folder 需要包含 model.p、Input.txt 和 Config.txt（默认 model/）。"
            )
        if st.session_state.get(_RUN_ERR_KEY):
            with st.container(key="what_if_run_error"):
                st.error(st.session_state[_RUN_ERR_KEY])

        _section_divider()
        _block_heading("Charts")

        series = st.session_state.get(_SWEEP_RESULT_KEY)

        with st.container(key="what_if_charts_block"):
            chart_row_a, chart_row_b = st.columns(2, gap="medium")
            with chart_row_a:
                _render_chart(_CHART_TITLES[0], chart_key="what_if_chart_wip", series=series)
            with chart_row_b:
                _render_chart(_CHART_TITLES[1], chart_key="what_if_chart_completion", series=series)

            with st.container(key="what_if_charts_row2"):
                chart_row_c, chart_row_d = st.columns(2, gap="medium")
                with chart_row_c:
                    _render_chart(_CHART_TITLES[2], chart_key="what_if_chart_scrap", series=series)
                with chart_row_d:
                    _render_chart(_CHART_TITLES[3], chart_key="what_if_chart_lead_time", series=series)

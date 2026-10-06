"""KPI 展示：System / Stage / Station，与 `kpi_calculator.get_snapshot()` 对齐。

趋势图在 ``ui.kpi_trends``，两边共用的样式常量在 ``ui.kpi_style``。
"""
from __future__ import annotations

import html
import time

import plotly.graph_objects as go
import streamlit as st

from ui.kpi_style import (
    _FONT_CHART_AXIS_PX,
    _FONT_CHART_TITLE_PX,
    _FONT_L5_PX,
    _KPI_COLOR_COMPLETION,
    _KPI_COLOR_SCRAP,
    _KPI_COLOR_WIP,
    _PLOT_FONT,
    _UI_THEME,
)
from ui.kpi_trends import render_kpi_trends_block

# 字号层级（布局.md · 可读性微调：Barlow Condensed 下同 px 略增一档）；L5 与图表字号在 ui.kpi_style
_FONT_L1_PX = 24   # section：System KPI
_FONT_L2_PX = 22   # section：Stage / Station
_FONT_L3_PX = 15   # 指标名、工站名、按钮、表头
_FONT_L4_SYSTEM_PX = 30  # System 双行卡主数值
_FONT_L4_STAGE_PX = 28   # Stage 卡主数值
_FONT_L4_STAGE_THR_PX = 30  # Stage Throughput 略强调
_FONT_STAGE_NAME_PX = 16  # Stage 卡内阶段名（略大于 L3）

# Lead / Flow Time：Tailwind yellow-500，与 WIP/Completion/Scrap 及 History 橙区分
_KPI_COLOR_LEAD = "#eab308"
# Stage Throughput：Tailwind indigo-500，产出速率（与 WIP 蓝、Completion 绿、Flow Time 黄区分）
_KPI_COLOR_THROUGHPUT = "#6366f1"
_KPI_COLOR_DEFAULT = "#1e293b"
_STAGE_TITLE_COLOR = "#1e293b"
_STAGE_BADGE_BG = "#f1f5f9"
_STAGE_BADGE_TEXT = "#64748b"
_STAGE_CARD_ACCENT = "#e2e8f0"

_KPI_SECTION_TITLE_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700&display=swap');
    div[data-testid="stMainBlockContainer"] .kpi-sec-title {
        margin: 0 0 0.75rem 0 !important;
        padding: 0 0 0 12px !important;
        font-family: "Barlow Condensed", "Segoe UI", sans-serif !important;
        font-weight: 700 !important;
        line-height: 1.3 !important;
        letter-spacing: 0.02em !important;
        border-left-style: solid !important;
        border-left-width: 4px !important;
        color: #1e293b !important;
    }
    div[data-testid="stMainBlockContainer"] .kpi-sec-title--system {
        font-size: """ + str(_FONT_L1_PX) + """px !important;
        border-left-color: #0284c7 !important;
    }
    div[data-testid="stMainBlockContainer"] .kpi-sec-title--stage {
        font-size: """ + str(_FONT_L2_PX) + """px !important;
        border-left-color: #64748b !important;
    }
    div[data-testid="stMainBlockContainer"] .kpi-sec-title--station {
        font-size: """ + str(_FONT_L2_PX) + """px !important;
        border-left-color: #cbd5e1 !important;
    }
    /* Stage | Station 并列：顶对齐，Stage 列随内容高度（不拉伸填空白） */
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_stage_station_panel [data-testid="stHorizontalBlock"] {
        align-items: start !important;
    }
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap,
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stVerticalBlockBorderWrapper"] {
        background: linear-gradient(165deg, #f8fafc 0%, #ffffff 55%) !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 16px 20px !important;
        margin-top: 12px !important;
        margin-bottom: 0 !important;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
        overflow: hidden !important;
    }
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stHorizontalBlock"],
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="column"],
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stPlotlyChart"],
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stPlotlyChart"] > div,
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stPlotlyChart"] iframe {
        overflow: hidden !important;
        overflow-x: hidden !important;
        overflow-y: hidden !important;
        scrollbar-width: none !important;
        -ms-overflow-style: none !important;
    }
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stPlotlyChart"]::-webkit-scrollbar,
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_trends_wrap [data-testid="stPlotlyChart"] > div::-webkit-scrollbar {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }
    div[data-testid="stMainBlockContainer"] .kpi-station-pills-grid {
        display: grid !important;
        grid-template-columns: repeat(9, minmax(0, 1fr)) !important;
        gap: 10px !important;
        align-items: center !important;
    }
    div[data-testid="stMainBlockContainer"] .kpi-station-pills-grid > span {
        min-width: 0 !important;
        white-space: nowrap !important;
    }
    @media (max-width: 1440px) {
        div[data-testid="stMainBlockContainer"] .kpi-station-pills-grid {
            grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
        }
    }
    div[data-testid="stMainBlockContainer"] .kpi-station-state-box {
        background: #ffffff !important;
        border: 0.5px solid #e0e0e0 !important;
        border-left: 3px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 14px 18px !important;
        margin-bottom: 12px !important;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
    }
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_station_fractions_wrap,
    div[data-testid="stMainBlockContainer"] div.st-key-kpi_station_fractions_wrap [data-testid="stVerticalBlockBorderWrapper"] {
        background: #ffffff !important;
        border: 0.5px solid #e0e0e0 !important;
        border-left: 3px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 14px 18px 8px 18px !important;
        margin-bottom: 0 !important;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
        overflow: hidden !important;
    }
</style>
"""


def _kpi_section_title_html(title: str, variant: str) -> str:
    v = html.escape(variant.strip().lower())
    t = html.escape(title)
    return '<p class="kpi-sec-title kpi-sec-title--{}">{}</p>'.format(v, t)


# Station KPI 卡片标题（与 Digital Twin / part_track_conformance.SLOT_HEADERS_M 风格一致：M1-1 …）
STATION_KPI_DISPLAY_NAME: dict[str, str] = {
    "station11": "M1-1",
    "station21": "M2-1",
    "station22": "M2-2",
    "station31": "M3-1",
    "station41": "M4-1",
    "station51": "M5-1",
    "station52": "M5-2",
    "station61": "M6-1",
    "station71": "M7-1",
}

_STATE_LABEL_EN = {
    "busy": "Busy",
    "fail": "Failed",
    "blocked": "Blocked",
    "idle": "Idle",
}

# Station state semantics（优化2 定稿：Busy 绿 / Idle 灰 / Blocked 黄 / Fail 红）
_STATION_BUSY_COLOR = "#22c55e"
_STATION_FAIL_COLOR = "#ef4444"
_STATION_BLOCKED_COLOR = "#eab308"
_STATION_IDLE_COLOR = "#94a3b8"

def station_kpi_display_name(sid: str) -> str:
    """工站 KPI 卡片标题：M1-1 …（与 Twin 一致；未知 id 则回退为原 id）。"""
    key = str(sid or "").strip().lower()
    return STATION_KPI_DISPLAY_NAME.get(key, sid or key or "—")


def _dominant_state_key(pn: dict[str, float]) -> str:
    """busy / fail / blocked / idle 中占比最大者（并列时按元组顺序先者优先）。"""
    keys = ("busy", "fail", "blocked", "idle")
    return max(keys, key=lambda s: float(pn.get(s, 0.0) or 0.0))


def _state_key_dot_and_color(state_key: str) -> tuple[str, str]:
    """Busy=绿 / Fail=红 / Blocked=黄 / Idle=灰。"""
    k = (state_key or "idle").strip().lower()
    if k == "busy":
        return "\u25cf", _STATION_BUSY_COLOR
    if k == "fail":
        return "\u25cf", _STATION_FAIL_COLOR
    if k == "blocked":
        return "\u25cf", _STATION_BLOCKED_COLOR
    return "\u25cf", _STATION_IDLE_COLOR


@st.cache_data(ttl=60.0, show_spinner=False)
def default_station_ids() -> tuple[str, ...]:
    from paths import ensure_paths

    ensure_paths()
    import common  # noqa: E402

    cfg = common.load_config("config.json")
    hosts = list(cfg.get("controller_hostnames") or [])
    stations = [str(h) for h in hosts if str(h).startswith("station")]
    if stations:
        return tuple(sorted(stations))
    w = cfg.get("component_wips") or {}
    stations = [str(k) for k in w if str(k).startswith("station")]
    return tuple(sorted(stations)) if stations else tuple()


def _format_runtime_hms(seconds: float) -> str:
    sec = max(0, int(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "{:d}:{:02d}:{:02d}".format(h, m, s)
    if m and s:
        return "{:d} min {:d} s".format(m, s)
    if m:
        return "{:d} min".format(m)
    return "{:d} s".format(s)


def _four_state_percent_ints(b: float, f: float, bl: float, i: float) -> tuple[int, int, int, int]:
    """整数百分比 busy/fail/blocked/idle，总和恒为 100（最大余数法）。全 Idle 时 (0,0,0,100)，条为整段灰。"""
    b = max(0.0, min(1.0, float(b)))
    f = max(0.0, min(1.0, float(f)))
    bl = max(0.0, min(1.0, float(bl)))
    i = max(0.0, min(1.0, float(i)))
    sm = b + f + bl + i
    if sm > 1e-9:
        b, f, bl, i = b / sm, f / sm, bl / sm, i / sm
    else:
        b, f, bl, i = 0.0, 0.0, 0.0, 1.0
    if b + f + bl < 1e-9:
        return 0, 0, 0, 100
    raw = [100.0 * b, 100.0 * f, 100.0 * bl, 100.0 * i]
    base = [int(x) for x in raw]
    rem = 100 - sum(base)
    frac_order = sorted(
        range(4), key=lambda j: raw[j] - base[j], reverse=True
    )
    for k in range(max(0, rem)):
        base[frac_order[k % 4]] += 1
    return base[0], base[1], base[2], base[3]


def _station_stacked_bar_html(pn: dict[str, float]) -> str:
    """单行四色堆叠条（无图例；全 Idle 时整段为 Idle 灰）。"""
    b = max(0.0, min(1.0, float(pn.get("busy", 0.0) or 0.0)))
    f = max(0.0, min(1.0, float(pn.get("fail", 0.0) or 0.0)))
    bl = max(0.0, min(1.0, float(pn.get("blocked", 0.0) or 0.0)))
    i = max(0.0, min(1.0, float(pn.get("idle", 0.0) or 0.0)))
    sm = b + f + bl + i
    if sm > 1e-9:
        b, f, bl, i = b / sm, f / sm, bl / sm, i / sm
    else:
        b, f, bl, i = 0.0, 0.0, 0.0, 1.0
    pb, pf, pbl, pi = _four_state_percent_ints(b, f, bl, i)
    wb = float(pb)
    wf = float(pf)
    wbl = float(pbl)
    wi = float(pi)
    return (
        '<div style="display:flex;height:7px;border-radius:4px;overflow:hidden;'
        'width:100%;margin:4px 0 0 0;">'
        '<div style="width:{:.4f}%;min-width:0;background:{};"></div>'
        '<div style="width:{:.4f}%;min-width:0;background:{};"></div>'
        '<div style="width:{:.4f}%;min-width:0;background:{};"></div>'
        '<div style="width:{:.4f}%;min-width:0;background:{};"></div>'
        "</div>"
    ).format(
        wb,
        _STATION_BUSY_COLOR,
        wf,
        _STATION_FAIL_COLOR,
        wbl,
        _STATION_BLOCKED_COLOR,
        wi,
        _STATION_IDLE_COLOR,
    )


def _station_probs_normalize(probs: dict) -> dict[str, float]:
    if not probs:
        return {"busy": 0.0, "fail": 0.0, "blocked": 0.0, "idle": 1.0}
    pl = {str(k).lower(): max(0.0, float(v or 0)) for k, v in probs.items()}
    b = pl.get("busy", pl.get("loading", 0.0))
    f = pl.get("fail", 0.0)
    bl = pl.get("blocked", 0.0)
    i = pl.get("idle", 0.0)
    sm = b + f + bl + i
    if sm > 1e-9:
        b, f, bl, i = b / sm, f / sm, bl / sm, i / sm
    else:
        b, f, bl, i = 0.0, 0.0, 0.0, 1.0
    return {"busy": b, "fail": f, "blocked": bl, "idle": i}


def _station_compact_card_html(
    sid: str,
    util_raw,
    probs: dict,
    *,
    live: dict | None = None,
) -> str:
    _ = util_raw, live
    pn = _station_probs_normalize(probs)
    body = _station_stacked_bar_html(pn)

    dom = _dominant_state_key(pn)
    dot, dot_color = _state_key_dot_and_color(dom)
    line_lbl = html.escape(_STATE_LABEL_EN.get(dom, dom.title()))
    title_esc = html.escape(station_kpi_display_name(sid))
    top_bar = (
        f"<div style='font-size:{_FONT_L5_PX}px;font-family:\"Barlow Condensed\",\"Segoe UI\",sans-serif;"
        "color:{};margin:0 0 2px 0;line-height:1.35;letter-spacing:0.01em;"
        "display:flex;align-items:center;flex-wrap:wrap;gap:6px 8px;'>"
        "<span style='font-weight:700;color:{};'>{}</span>"
        "<span style='color:{};font-size:1.02em;line-height:1;'>{}</span>"
        "<b style='font-weight:600;'>{}</b>"
        "</div>"
    ).format(
        _UI_THEME["text_dim"],
        _UI_THEME["text"],
        title_esc,
        dot_color,
        html.escape(dot),
        line_lbl,
    )

    return (
        "<div style='padding:10px 12px;border-radius:10px;box-sizing:border-box;"
        "background:linear-gradient(165deg,{} 0%,{} 52%);"
        "border:1px solid {};box-shadow:0 1px 2px rgba(15,23,42,0.06);"
        "margin-bottom:10px;position:relative;overflow:hidden;'>"
        "<div style='position:absolute;top:0;left:0;right:0;height:2px;"
        "background:{};'></div>{}{}</div>"
    ).format(
        _UI_THEME["bg"],
        _UI_THEME["surface"],
        _UI_THEME["border"],
        _UI_THEME["accent"],
        top_bar,
        body,
    )


def render_station_card_grid(
    kpi: dict | None,
    *,
    cols_per_row: int = 4,
    age_sec: float | None = None,
    compact: bool = True,
    station_ids: tuple[str, ...] | None = None,
) -> None:
    _ = age_sec
    k = dict(kpi or {})
    probs = dict(k.get("state_probability") or {})
    live_all = dict(k.get("station_live") or {})
    defaults = station_ids if station_ids is not None else default_station_ids()
    stations = sorted(set(defaults) | set(probs.keys()))
    st.markdown(
        "<hr style='border:none;border-top:1px solid #e2e8f0;margin:14px 0 10px 0;'>",
        unsafe_allow_html=True,
    )
    if not stations:
        st.markdown(
            _kpi_section_title_html("Station KPI", "station"),
            unsafe_allow_html=True,
        )
        st.text("No station* list in config.")
        return
    st.markdown(
        _kpi_section_title_html("Station KPI", "station"),
        unsafe_allow_html=True,
    )
    station_order = [
        "station11",
        "station21",
        "station22",
        "station31",
        "station41",
        "station51",
        "station52",
        "station61",
        "station71",
    ]
    state_color = {
        "BUSY": _STATION_BUSY_COLOR,
        "FAIL": _STATION_FAIL_COLOR,
        "BLOCKED": _STATION_BLOCKED_COLOR,
        "IDLE": _STATION_IDLE_COLOR,
    }
    state_dot = {"BUSY": "●", "FAIL": "●", "BLOCKED": "●", "IDLE": "●"}
    pills = ""
    for sid in station_order:
        live = live_all.get(sid) or {}
        state = str(live.get("current_state") or "IDLE").upper()
        color = state_color.get(state, _STATION_IDLE_COLOR)
        dot = state_dot.get(state, "○")
        name = station_kpi_display_name(sid)
        pills += (
            '<span style="display:inline-flex;align-items:center;justify-content:center;gap:6px;'
            f'background:{_UI_THEME["surface2"]};border-radius:18px;padding:7px 12px;'
            f'margin:0;font-size:{_FONT_L3_PX}px;width:100%;box-sizing:border-box;">'
            f'<span style="color:{color};font-size:{_FONT_L3_PX}px">{dot}</span>'
            f'<span style="color:{_UI_THEME["text"]};font-weight:700;font-size:{_FONT_L3_PX}px">{html.escape(name)}</span>'
            f'<span style="color:{color};font-size:{_FONT_L3_PX}px">{html.escape(state.capitalize())}</span>'
            "</span>"
        )
    st.markdown(
        (
            '<div class="kpi-station-state-box">'
            f'<div style="font-size:{_FONT_STAGE_NAME_PX}px;color:{_STAGE_TITLE_COLOR};'
            'margin-bottom:12px;font-weight:700;">'
            "State"
            "</div>"
            '<div class="kpi-station-pills-grid">'
            f"{pills}"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    # 2x2 station comparison bars (Idle / Busy / Blocked / Failed)
    state_probs = dict(k.get("state_probability") or {})
    labels = [station_kpi_display_name(s) for s in station_order]
    busy_vals = [float((state_probs.get(s) or {}).get("busy", 0) or 0) * 100 for s in station_order]
    fail_vals = [float((state_probs.get(s) or {}).get("fail", 0) or 0) * 100 for s in station_order]
    blocked_vals = [float((state_probs.get(s) or {}).get("blocked", 0) or 0) * 100 for s in station_order]
    idle_vals = [float((state_probs.get(s) or {}).get("idle", 0) or 0) * 100 for s in station_order]

    _STATION_HOVER = dict(
        bgcolor="#1e2a3a",
        font_size=_FONT_L5_PX,
        font_family=_PLOT_FONT,
        font_color="#e0e6f0",
        bordercolor="#444c56",
    )

    def _make_station_bar(
        title: str,
        values: list[float],
        color: str,
        metric_name: str,
    ) -> go.Figure:
        n = len(labels)
        fig = go.Figure(
            go.Bar(
                x=labels,
                y=values,
                marker_color=color,
                customdata=[metric_name] * n,
                hovertemplate="<b>%{x}</b><br>%{customdata}: %{y:.1f}<extra></extra>",
            )
        )
        y_max = max(105.0, max(values, default=0) + 8.0)
        for lb, val in zip(labels, values):
            if val > 0:
                fig.add_annotation(
                    x=lb,
                    y=val + 1.0,
                    text="{:.0f}".format(val),
                    showarrow=False,
                    font=dict(size=_FONT_L5_PX, color=color, family=_PLOT_FONT),
                    yanchor="bottom",
                )
        fig.update_layout(
            title=dict(text=title, font=dict(size=_FONT_CHART_TITLE_PX, family=_PLOT_FONT)),
            height=200,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#f8f9fa",
            margin=dict(t=36, b=40, l=8, r=10),
            yaxis=dict(
                range=[0, y_max],
                showticklabels=False,
                showline=False,
                showgrid=False,
                zeroline=False,
                ticks="",
            ),
            xaxis=dict(tickfont=dict(size=_FONT_CHART_AXIS_PX)),
            font=dict(size=_FONT_CHART_AXIS_PX, color=_UI_THEME["text_dim"], family=_PLOT_FONT),
            showlegend=False,
            hoverlabel=_STATION_HOVER,
        )
        return fig

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    with st.container(key="kpi_station_fractions_wrap"):
        row1_col1, row1_col2 = st.columns(2)
        row2_col1, row2_col2 = st.columns(2)
        with row1_col1:
            st.plotly_chart(
                _make_station_bar(
                    "Idle fraction (%)", idle_vals, _STATION_IDLE_COLOR, "Idle"
                ),
                use_container_width=True,
                config={"displayModeBar": False},
                key="kpi_station_idle_bar",
            )
        with row1_col2:
            st.plotly_chart(
                _make_station_bar(
                    "Busy fraction (%)", busy_vals, _STATION_BUSY_COLOR, "Busy"
                ),
                use_container_width=True,
                config={"displayModeBar": False},
                key="kpi_station_busy_bar",
            )
        with row2_col1:
            st.plotly_chart(
                _make_station_bar(
                    "Failed fraction (%)",
                    fail_vals,
                    _STATION_FAIL_COLOR,
                    "Failed",
                ),
                use_container_width=True,
                config={"displayModeBar": False},
                key="kpi_station_fail_bar",
            )
        with row2_col2:
            st.plotly_chart(
                _make_station_bar(
                    "Blocked fraction (%)",
                    blocked_vals,
                    _STATION_BLOCKED_COLOR,
                    "Blocked",
                ),
                use_container_width=True,
                config={"displayModeBar": False},
                key="kpi_station_blocked_bar",
            )


def _system_values(kpi: dict) -> dict[str, float | int]:
    """Normalize snapshot `system` + legacy top-level keys."""
    k = dict(kpi or {})
    sysb = dict(k.get("system") or {})
    n_comp = int(sysb.get("num_completions", k.get("finished_count", 0)) or 0)
    n_scrap = int(sysb.get("num_scraps", k.get("scrap_count", 0)) or 0)
    wip = int(sysb.get("wip_instantaneous", k.get("current_wip", 0)) or 0)
    awip = float(sysb.get("wip_average", k.get("avg_wip", 0)) or 0)
    cr = float(sysb.get("complete_rate", k.get("throughput", 0)) or 0)
    # Backend scrap_rate = scraps/departed (fraction); display uses n_scrap/obs (rate /s).
    scrap_rate_fraction = float(
        sysb.get("scrap_rate", k.get("scrap_rate", 0)) or 0
    )
    ct_fin = float(sysb.get("avg_cycle_time_fin", k.get("avg_flow_time_sec", 0)) or 0)
    ct_all = float(sysb.get("avg_cycle_time_all", k.get("avg_cycle_time_all_sec", 0)) or 0)
    ftc = int(k.get("flow_time_count", 0) or 0)
    ftca = int(k.get("avg_cycle_all_sample_count", 0) or 0)
    departed = n_comp + n_scrap
    yield_rate = float(k.get("yield_rate", 0) or 0)
    if departed > 0 and yield_rate <= 0:
        yield_rate = n_comp / departed
    return {
        "n_comp": n_comp,
        "n_scrap": n_scrap,
        "wip": wip,
        "awip": awip,
        "cr": cr,
        "scrap_rate_fraction": scrap_rate_fraction,
        "yield_rate": yield_rate,
        "ct_fin": ct_fin,
        "ct_all": ct_all,
        "ftc": ftc,
        "ftca": ftca,
        "obs": float(k.get("observation_time_sec", 0) or 0),
    }


def render_system_kpi_group(
    kpi: dict | None,
    *,
    age_sec: float | None = None,
) -> None:
    _ = age_sec
    v = _system_values(dict(kpi or {}))
    st.markdown(
        _kpi_section_title_html("System KPI", "system"),
        unsafe_allow_html=True,
    )
    def _pair_card_html(
        top_label: str,
        top_value: str,
        top_color: str,
        bottom_label: str,
        bottom_value: str,
        bottom_color: str,
        *,
        top_sub: str = "",
        bottom_sub: str = "",
        top_sub_inline: bool = False,
        bottom_sub_inline: bool = False,
    ) -> str:
        top_label_e = html.escape(str(top_label))
        top_value_e = html.escape(str(top_value))
        bottom_label_e = html.escape(str(bottom_label))
        bottom_value_e = html.escape(str(bottom_value))
        top_sub_html = ""
        l3, l4, l5 = _FONT_L3_PX, _FONT_L4_SYSTEM_PX, _FONT_L5_PX
        if top_sub:
            if top_sub_inline:
                top_sub_html = (
                    f'<span style="font-size:{l5}px;color:#64748b;line-height:1.2;">{html.escape(str(top_sub))}</span>'
                )
            else:
                top_sub_html = (
                    f'<div style="font-size:{l3}px;color:#64748b;line-height:1.2;">{html.escape(str(top_sub))}</div>'
                )
        bottom_sub_html = ""
        if bottom_sub:
            if bottom_sub_inline:
                bottom_sub_html = (
                    f'<span style="font-size:{l5}px;color:#64748b;line-height:1.2;">{html.escape(str(bottom_sub))}</span>'
                )
            else:
                bottom_sub_html = (
                    f'<div style="font-size:{l3}px;color:#64748b;line-height:1.2;">{html.escape(str(bottom_sub))}</div>'
                )
        top_title_html = (
            f'<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:nowrap;">'
            f'<span style="font-size:{l3}px;color:#64748b;font-weight:600;white-space:nowrap;">{top_label_e}</span>'
            f'{top_sub_html}'
            "</div>"
            if top_sub_inline
            else f'<div style="font-size:{l3}px;color:#64748b;font-weight:600;">{top_label_e}</div>{top_sub_html}'
        )
        bottom_title_html = (
            f'<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:nowrap;">'
            f'<span style="font-size:{l3}px;color:#64748b;font-weight:600;white-space:nowrap;">{bottom_label_e}</span>'
            f'{bottom_sub_html}'
            "</div>"
            if bottom_sub_inline
            else f'<div style="font-size:{l3}px;color:#64748b;font-weight:600;">{bottom_label_e}</div>{bottom_sub_html}'
        )
        return (
            '<div style="background:white;border:1px solid #e0e0e0;border-radius:8px;'
            'padding:14px 16px;height:162px;display:flex;flex-direction:column;justify-content:space-between;'
            'margin-bottom:6px;">'
            '<div style="min-height:68px;display:flex;flex-direction:column;justify-content:flex-start;">'
            f"{top_title_html}"
            f'<div style="font-size:{l4}px;font-weight:700;color:{top_color};line-height:1.15;">{top_value_e}</div>'
            '</div>'
            '<div style="border-top:1px solid #f0f0f0;padding-top:10px;min-height:64px;'
            'display:flex;flex-direction:column;justify-content:flex-start;">'
            f"{bottom_title_html}"
            f'<div style="font-size:{l4}px;font-weight:700;color:{bottom_color};line-height:1.15;">{bottom_value_e}</div>'
            '</div>'
            '</div>'
        )

    completion_rate_text = "{:.3f}".format(float(v["cr"]) * 60.0)
    scrap_rate_text = "{:.3f}".format(
        (float(v["n_scrap"]) / max(float(v["obs"]), 0.001)) * 60.0
    )
    lead_fin_text = "{:.1f}".format(float(v["ct_fin"]))
    lead_all_text = "{:.1f}".format(float(v["ct_all"]))

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            _pair_card_html(
                "WIP",
                str(v["wip"]),
                _KPI_COLOR_WIP,
                "AVG WIP",
                "{:.3f}".format(v["awip"]),
                _KPI_COLOR_WIP,
            ),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            _pair_card_html(
                "Completions",
                str(v["n_comp"]),
                _KPI_COLOR_COMPLETION,
                "Completion Rate (/min)",
                completion_rate_text,
                _KPI_COLOR_COMPLETION,
            ),
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            _pair_card_html(
                "Scraps",
                str(v["n_scrap"]),
                _KPI_COLOR_SCRAP,
                "Scrap Rate (/min)",
                scrap_rate_text,
                _KPI_COLOR_SCRAP,
            ),
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            _pair_card_html(
                "Avg Lead Time (Finished) (s)",
                lead_fin_text,
                _KPI_COLOR_LEAD,
                "Avg Lead Time (Finished+Scrapped) (s)",
                lead_all_text,
                _KPI_COLOR_LEAD,
            ),
            unsafe_allow_html=True,
        )
    render_kpi_trends_block(kpi)


def render_stage_kpi_group(kpi: dict | None) -> None:
    k = dict(kpi or {})
    stg = k.get("stages") or {}
    st.markdown(
        _kpi_section_title_html("Stage KPI", "stage"),
        unsafe_allow_html=True,
    )
    stage_cfg = {
        1: {"name": "Stage 1", "type": "Non-Looping"},
        2: {"name": "Stage 2", "type": "Looping"},
        3: {"name": "Stage 3", "type": "Non-Looping"},
        4: {"name": "Stage 4", "type": "Looping"},
        5: {"name": "Stage 5", "type": "Non-Looping"},
        6: {"name": "Stage 6", "type": "Looping"},
    }
    def _stage_card_html(s: int) -> str:
        data = stg.get("stage{}".format(s)) or stg.get(str(s)) or {}
        cfg = stage_cfg[s]
        thr_v = float(data.get("throughput", data.get("throughput_per_sec", 0)) or 0)
        ft_v = float(data.get("avg_flow_time", data.get("avg_flow_time_sec", 0)) or 0)
        awip_v = float(data.get("wip_average", data.get("avg_wip", 0)) or 0)
        wip_v = int(data.get("wip_instantaneous", data.get("instantaneous_wip", 0)) or 0)
        dep_v = int(data.get("num_departures", data.get("departures", 0)) or 0)

        thr = "{:.4f}".format(thr_v)
        ft = "{:.1f}".format(ft_v)
        awip = "{:.3f}".format(awip_v)
        wip = str(wip_v)
        dep = str(dep_v)
        l3, l4, l4t, l5n = _FONT_L3_PX, _FONT_L4_STAGE_PX, _FONT_L4_STAGE_THR_PX, _FONT_STAGE_NAME_PX
        c_wip = _KPI_COLOR_WIP
        c_lead = _KPI_COLOR_LEAD
        c_thr = _KPI_COLOR_THROUGHPUT
        c_dep = _KPI_COLOR_COMPLETION
        c_title, c_badge_bg, c_badge_txt = _STAGE_TITLE_COLOR, _STAGE_BADGE_BG, _STAGE_BADGE_TEXT
        c_accent = _STAGE_CARD_ACCENT
        return f"""
<div style="background:white;border:0.5px solid #e0e0e0;
            border-radius:12px;border-left:3px solid {c_accent};
            padding:14px 18px;margin-bottom:8px;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
    <span style="font-size:{l5n}px;font-weight:700;color:{c_title};">{cfg['name']}</span>
    <span style="background:{c_badge_bg};color:{c_badge_txt};font-size:{l3}px;
                 font-weight:500;padding:2px 8px;border-radius:20px;">
      {cfg['type']}
    </span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;">
    <div>
      <div style="font-size:{l3}px;color:#64748b;margin-bottom:3px;font-weight:600;">WIP</div>
      <div style="font-size:{l4}px;font-weight:700;color:{c_wip};">{wip}</div>
    </div>
    <div>
      <div style="font-size:{l3}px;color:#64748b;margin-bottom:3px;font-weight:600;">AVG WIP</div>
      <div style="font-size:{l4}px;font-weight:700;color:{c_wip};">{awip}</div>
    </div>
    <div>
      <div style="font-size:{l3}px;color:#64748b;margin-bottom:3px;font-weight:600;">Departures</div>
      <div style="font-size:{l4}px;font-weight:700;color:{c_dep};">{dep}</div>
    </div>
    <div>
      <div style="font-size:{l3}px;color:#64748b;margin-bottom:3px;font-weight:600;">Throughput (/s)</div>
      <div style="font-size:{l4t}px;font-weight:700;color:{c_thr};">{thr}</div>
    </div>
    <div>
      <div style="font-size:{l3}px;color:#64748b;margin-bottom:3px;font-weight:600;">Avg Flow Time (s)</div>
      <div style="font-size:{l4}px;font-weight:700;color:{c_lead};">{ft}</div>
    </div>
  </div>
</div>
"""

    col_l, col_r = st.columns(2, gap="small")
    with col_l:
        for s in (1, 2, 3):
            st.markdown(_stage_card_html(s), unsafe_allow_html=True)
    with col_r:
        for s in (4, 5, 6):
            st.markdown(_stage_card_html(s), unsafe_allow_html=True)


def replay_age_seconds(last_update_unix: float) -> float | None:
    if not last_update_unix:
        return None
    return max(0.0, time.time() - last_update_unix)




def render_kpi_metrics(
    kpi: dict | None,
    *,
    age_sec: float | None = None,
) -> None:
    """Deprecated name: same as **System KPI** block only (for older callers/tests)."""
    render_system_kpi_group(kpi, age_sec=age_sec)


def render_kpi_dashboard(
    kpi: dict | None,
    *,
    age_sec: float | None = None,
    cols_per_row: int = 4,
    compact_stations: bool = True,
) -> None:
    st.markdown(_KPI_SECTION_TITLE_CSS, unsafe_allow_html=True)
    with st.container(border=True, key="kpi_all_panel"):
        render_system_kpi_group(kpi, age_sec=age_sec)
        st.markdown(
            "<hr style='border:none;border-top:1px solid #e2e8f0;margin:14px 0 10px 0;'>",
            unsafe_allow_html=True,
        )
        with st.container(key="kpi_stage_station_panel"):
            with st.container(key="kpi_stage_col"):
                render_stage_kpi_group(kpi)
            render_station_card_grid(
                kpi,
                cols_per_row=cols_per_row,
                age_sec=age_sec,
                compact=compact_stations,
            )

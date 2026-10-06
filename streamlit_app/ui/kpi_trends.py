"""System KPI trend charts: WIP and Completion & Scrap over time, drawn under the System KPI
cards (``ui.kpi_display.render_system_kpi_group``) from the snapshot's ``trend_*`` series.
"""
from __future__ import annotations

import datetime
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots
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

# Trends 双图：等高、同 margin；仅使用 KPI 内 trend_* 序列（不用 MQTT 缓冲假滚动）
_WIP_TREND_MAX_POINTS = 200
_TREND_CHART_HEIGHT = 250
_TREND_PLOTLY_CONTAINER_HEIGHT = 290
_TREND_PAIR_MARGIN_WIP = dict(t=42, b=36, l=60, r=20)
_TREND_PAIR_MARGIN_RATE = dict(t=42, b=36, l=40, r=20)
_TREND_PLOT_CONFIG = {
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": False,
}
_CHART_TITLE_WIP = "WIP"
_CHART_TITLE_RATES = "Completion & Scrap (/min)"
_TREND_X_WINDOW_SEC = 300.0  # 最长 5 分钟；实际横轴按数据跨度收紧
_TREND_X_EDGE_PAD_SEC = 2.0
_TREND_ROLLING_DEPARTURES = 20
_RATE_PER_SEC_TO_PCS_MIN = 60.0
_RATE_TREND_Y_MAX_HISTORY = 20
_RATE_TREND_Y_PAD = 1.2
_TREND_WIP_EMPTY_Y_HI = 10
_RATE_TREND_COMP_EMPTY_HI = 3.0   # ≈ 0.05 /s
_RATE_TREND_SCRAP_EMPTY_HI = 1.0
_SESSION_RATE_Y_PEAKS = "_kpi_rate_trend_y_peak_history"
_SESSION_SCRAP_Y_PEAKS = "_kpi_scrap_trend_y_peak_history"
_SESSION_RATE_Y_SESSION = "_kpi_rate_trend_y_session_id"


def _trend_chart_title(text: str) -> dict[str, Any]:
    return dict(
        text=text,
        font=dict(family=_PLOT_FONT, size=_FONT_CHART_TITLE_PX, color=_UI_THEME["text"]),
    )


_TREND_HOVERLABEL: dict[str, Any] = dict(
    bgcolor="#1e2a3a",
    font_size=_FONT_L5_PX,
    font_family=_PLOT_FONT,
    font_color="#e0e6f0",
    bordercolor="#444c56",
)


def _trend_layout_common(*, showlegend: bool) -> dict[str, Any]:
    d: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "#f8f9fa",
        "margin": dict(t=36, b=30, l=50, r=20),
        "height": 260,
        "font": dict(family=_PLOT_FONT, size=_FONT_CHART_AXIS_PX, color="#64748b"),
        "showlegend": showlegend,
        "hovermode": "x unified",
        "hoverlabel": _TREND_HOVERLABEL,
    }
    if showlegend:
        d["legend"] = dict(
            x=0.01,
            y=0.99,
            xanchor="left",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            borderwidth=0,
            font=dict(size=_FONT_CHART_AXIS_PX, color="#64748b", family=_PLOT_FONT),
        )
    return d


def _trend_apply_grid(fig: go.Figure) -> None:
    g = _UI_THEME["border"]
    z = _UI_THEME["border2"]
    ax_font = dict(size=_FONT_CHART_AXIS_PX, color=_UI_THEME["text_dim"])
    title_font = dict(size=_FONT_CHART_AXIS_PX, color=_UI_THEME["text_dim"], family=_PLOT_FONT)
    fig.update_xaxes(
        showgrid=False,
        gridcolor=g,
        gridwidth=1,
        zeroline=False,
        zerolinecolor=z,
        zerolinewidth=1,
        tickfont=ax_font,
        title_font=title_font,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=g,
        gridwidth=1,
        zeroline=True,
        zerolinecolor=z,
        zerolinewidth=1,
        tickfont=ax_font,
        title_font=title_font,
    )


def _trend_pair_layout_extras(
    *, margin_left: int = 60, margin_right: int | None = None
) -> dict[str, Any]:
    """Trends 双图共用：等高、margin 对齐；图例隐藏（hover 已覆盖）。"""
    base = _trend_layout_common(showlegend=False)
    base["height"] = _TREND_CHART_HEIGHT
    margin = dict(_TREND_PAIR_MARGIN_WIP if margin_left >= 60 else _TREND_PAIR_MARGIN_RATE)
    margin["l"] = margin_left
    if margin_right is not None:
        margin["r"] = margin_right
    base["margin"] = margin
    return base


def _trend_empty_x_range() -> tuple[datetime.datetime, datetime.datetime]:
    end = datetime.datetime.now()
    start = end - datetime.timedelta(minutes=2)
    return start, end


def _trend_window_end_ts(kpi: dict) -> float | None:
    end: float | None = None
    chart_ts = kpi.get("chart_time_unix")
    if chart_ts is not None:
        try:
            end = float(chart_ts)
        except (TypeError, ValueError):
            pass
    for key in ("trend_sys_wip_history", "trend_departure_history"):
        for row in kpi.get(key) or []:
            if isinstance(row, (list, tuple)) and row:
                try:
                    ts = float(row[0])
                except (TypeError, ValueError):
                    continue
                end = ts if end is None else max(end, ts)
    return end


def _trend_max_window_start_ts(kpi: dict) -> float | None:
    end = _trend_window_end_ts(kpi)
    if end is None:
        return None
    return end - _TREND_X_WINDOW_SEC


def _filter_wip_by_max_window(
    series: list[tuple[float, int]], kpi: dict
) -> list[tuple[float, int]]:
    start = _trend_max_window_start_ts(kpi)
    if start is None:
        return series
    return [(ts, w) for ts, w in series if ts >= start]


def _filter_rates_by_max_window(
    series: list[tuple[float, float, float]], kpi: dict
) -> list[tuple[float, float, float]]:
    start = _trend_max_window_start_ts(kpi)
    if start is None:
        return series
    return [row for row in series if row[0] >= start]


def _trend_x_range_from_series(
    kpi: dict,
    wip_hist: list[tuple[float, int]],
    rate_hist: list[tuple[float, float, float]],
) -> tuple[datetime.datetime, datetime.datetime] | None:
    """横轴贴合可见数据；跨度 < 5min 时左端从首点开始，避免左侧空白。"""
    end_ts = _trend_window_end_ts(kpi)
    if end_ts is None:
        return None
    ts_all: list[float] = [t for t, _ in wip_hist] + [t for t, _, _ in rate_hist]
    if ts_all:
        data_min = min(ts_all)
        data_max = max(ts_all)
        end_ts = max(end_ts, data_max)
        span = end_ts - data_min
        if span > _TREND_X_WINDOW_SEC:
            start_ts = end_ts - _TREND_X_WINDOW_SEC
        else:
            start_ts = data_min - _TREND_X_EDGE_PAD_SEC
    else:
        start_ts = end_ts - min(60.0, _TREND_X_WINDOW_SEC)
    return (
        datetime.datetime.fromtimestamp(start_ts),
        datetime.datetime.fromtimestamp(end_ts + _TREND_X_EDGE_PAD_SEC),
    )


def _ts_from_x_range(
    x_range: tuple[datetime.datetime, datetime.datetime] | None,
) -> tuple[float | None, float | None]:
    if x_range is None:
        return None, None
    return x_range[0].timestamp(), x_range[1].timestamp()


def _extend_wip_hist_to_x_range(
    hist: list[tuple[float, int]],
    x_range: tuple[datetime.datetime, datetime.datetime] | None,
) -> list[tuple[float, int]]:
    if not hist or x_range is None:
        return hist
    x0, x1 = _ts_from_x_range(x_range)
    if x0 is None or x1 is None:
        return hist
    out = list(hist)
    if out[0][0] > x0:
        out.insert(0, (x0, out[0][1]))
    if out[-1][0] < x1:
        out.append((x1, out[-1][1]))
    return out


def _extend_rate_hist_to_x_range(
    hist: list[tuple[float, float, float]],
    x_range: tuple[datetime.datetime, datetime.datetime] | None,
) -> list[tuple[float, float, float]]:
    if x_range is None:
        return hist
    x0, x1 = _ts_from_x_range(x_range)
    if x0 is None or x1 is None:
        return hist
    if not hist:
        return [(x0, 0.0, 0.0), (x1, 0.0, 0.0)]
    out = list(hist)
    if out[0][0] > x0:
        out.insert(0, (x0, 0.0, 0.0))
    if out[-1][0] < x1:
        out.append((x1, out[-1][1], out[-1][2]))
    return out


def _trend_pair_apply_xaxis(
    fig: go.Figure,
    *,
    empty: bool,
    x_range: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> None:
    kw: dict[str, Any] = dict(
        title=dict(text=""),
        showticklabels=True,
        tickformat="%H:%M:%S",
        nticks=4,
        tickangle=0,
    )
    if x_range is not None:
        kw["range"] = list(x_range)
    elif empty:
        x0, x1 = _trend_empty_x_range()
        kw["range"] = [x0, x1]
    fig.update_xaxes(**kw)


def _wip_trend_series_average(hist: list[tuple[float, int]]) -> float:
    """Time-weighted mean over the points actually drawn (not full-run system avg)."""
    if not hist:
        return 0.0
    if len(hist) == 1:
        return float(hist[0][1])
    total = 0.0
    for i in range(len(hist) - 1):
        t0, w = hist[i]
        t1, _ = hist[i + 1]
        total += float(w) * max(0.0, t1 - t0)
    tail = max(0.0, hist[-1][0] - hist[-2][0])
    last_t, last_w = hist[-1]
    total += float(last_w) * tail
    span = max(0.001, (hist[-1][0] - hist[0][0]) + tail)
    return total / span


def _wip_trend_y_range(ys: list[int], avg_w: float, *, pad: float = 2.0) -> list[float]:
    """0 .. dataMax + pad（论文/分析口径从 0 起）。"""
    vals = [float(v) for v in ys] + [float(avg_w)]
    if not vals:
        return [0.0, 4.0]
    hi = max(vals) + pad
    if hi <= 0:
        hi = 4.0
    return [0.0, hi]


def _wip_trend_apply_axes(
    fig: go.Figure,
    *,
    empty: bool,
    y_range: list[float] | None = None,
    x_range: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> None:
    _trend_apply_grid(fig)
    if empty:
        fig.update_yaxes(
            title=dict(text=""),
            range=[0, _TREND_WIP_EMPTY_Y_HI],
            autorange=False,
            nticks=5,
            tickformat="d",
        )
        _trend_pair_apply_xaxis(fig, empty=x_range is None, x_range=x_range)
        return
    fig.update_yaxes(
        title=dict(text=""),
        range=y_range,
        autorange=False,
        nticks=5,
        tickformat="d",
    )
    _trend_pair_apply_xaxis(fig, empty=False, x_range=x_range)


def _wip_trend_hist_for_chart(kpi: dict) -> list[tuple[float, int]]:
    return _filter_wip_by_max_window(_parse_wip_trend_hist(kpi), kpi)


def _rate_trend_hist_for_chart(kpi: dict) -> list[tuple[float, float, float]]:
    return _filter_rates_by_max_window(_trend_rate_points(kpi), kpi)


def _fig_wip_over_time(
    kpi: dict,
    *,
    x_range: tuple[datetime.datetime, datetime.datetime] | None = None,
    hist: list[tuple[float, int]] | None = None,
) -> go.Figure:
    raw_hist = hist if hist is not None else _wip_trend_hist_for_chart(kpi)
    avg_w = _wip_trend_series_average(raw_hist)
    hist = _extend_wip_hist_to_x_range(raw_hist, x_range)
    fig = go.Figure()
    if not hist:
        fig.update_layout(
            **_trend_pair_layout_extras(),
            title=_trend_chart_title(_CHART_TITLE_WIP),
        )
        _stub_t = datetime.datetime.now()
        fig.add_trace(
            go.Scatter(
                x=[_stub_t],
                y=[0],
                mode="lines",
                name="WIP",
                line=dict(color=_KPI_COLOR_WIP, width=2.5),
                opacity=0,
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[_stub_t],
                y=[0],
                mode="lines",
                name="AVG WIP (window)",
                line=dict(color=_UI_THEME["orange"], width=2, dash="6px,3px"),
                opacity=0,
                hoverinfo="skip",
            )
        )
        _wip_trend_apply_axes(fig, empty=True, x_range=x_range)
        return fig
    n = len(hist)
    ys = [w for _, w in hist]
    x_dt = [
        datetime.datetime.fromtimestamp(max(0.0, min(1e12, float(ts))))
        for ts, _ in hist
    ]
    wip_c = _KPI_COLOR_WIP
    mk = dict(
        size=3,
        color=wip_c,
        symbol="circle",
        line=dict(width=1, color="#ffffff"),
    )
    fig.add_trace(
        go.Scatter(
            x=x_dt,
            y=ys,
            mode="lines+markers",
            name="WIP",
            line=dict(color=wip_c, width=2.5),
            line_shape="hv",
            marker=mk,
            selected=dict(marker=dict(size=10, color=wip_c)),
            unselected=dict(marker=dict(size=3, opacity=1.0)),
            hovertemplate="WIP: %{y}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_dt,
            y=[avg_w] * n,
            mode="lines",
            name="AVG WIP (window)",
            line=dict(color=_UI_THEME["orange"], width=2, dash="6px,3px"),
            hovertemplate="AVG WIP (window): %{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        **_trend_pair_layout_extras(),
        title=_trend_chart_title(_CHART_TITLE_WIP),
    )
    _wip_trend_apply_axes(
        fig,
        empty=False,
        y_range=_wip_trend_y_range(ys, avg_w),
        x_range=x_range,
    )
    return fig


def _parse_wip_trend_hist(kpi: dict) -> list[tuple[float, int]]:
    raw = kpi.get("trend_sys_wip_history")
    hist: list[tuple[float, int]] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    hist.append((float(row[0]), int(row[1])))
                except (TypeError, ValueError):
                    pass
    if len(hist) > _WIP_TREND_MAX_POINTS:
        hist = hist[-_WIP_TREND_MAX_POINTS:]
    return hist


def _parse_throughput_trend_hist(kpi: dict) -> list[tuple[float, float, float]]:
    raw = kpi.get("trend_throughput_rates")
    hist: list[tuple[float, float, float]] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                try:
                    hist.append((float(row[0]), float(row[1]), float(row[2])))
                except (TypeError, ValueError):
                    pass
    if len(hist) > _WIP_TREND_MAX_POINTS:
        hist = hist[-_WIP_TREND_MAX_POINTS:]
    return hist


def _trend_rate_points_from_departures(kpi: dict) -> list[tuple[float, float, float]]:
    """Fallback when snapshot has no trend_throughput_rates (older main_service)."""
    dep_hist: list[tuple[float, int, int]] = []
    raw = kpi.get("trend_departure_history")
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                try:
                    dep_hist.append((float(row[0]), int(row[1]), int(row[2])))
                except (TypeError, ValueError):
                    pass
    dep_indices: list[int] = []
    for i in range(1, len(dep_hist)):
        p_nc, p_ns = dep_hist[i - 1][1], dep_hist[i - 1][2]
        if dep_hist[i][1] != p_nc or dep_hist[i][2] != p_ns:
            dep_indices.append(i)
    out: list[tuple[float, float, float]] = []
    for di in range(len(dep_indices)):
        idx = dep_indices[di]
        ts, nc, ns = dep_hist[idx]
        j = di
        dep_in_window = 0
        while j > 0 and dep_in_window < _TREND_ROLLING_DEPARTURES:
            j -= 1
            p_idx, n_idx = dep_indices[j], dep_indices[j + 1]
            dep_in_window += (dep_hist[n_idx][1] - dep_hist[p_idx][1]) + (
                dep_hist[n_idx][2] - dep_hist[p_idx][2]
            )
        if j == 0:
            base_idx = dep_indices[0] - 1
            t0, c0, s0 = dep_hist[base_idx] if base_idx >= 0 else dep_hist[0]
        else:
            t0, c0, s0 = dep_hist[dep_indices[j]]
        dt = ts - t0
        if dt <= 0:
            continue
        out.append(
            (
                ts,
                round((nc - c0) / dt, 5),
                round((ns - s0) / dt, 5),
            )
        )
    if len(out) > _WIP_TREND_MAX_POINTS:
        out = out[-_WIP_TREND_MAX_POINTS:]
    return out


def _trend_rate_points(kpi: dict) -> list[tuple[float, float, float]]:
    """(unix_ts, completion_rate_/s, scrap_rate_/s) at departures only."""
    out = _parse_throughput_trend_hist(kpi)
    if not out:
        out = _trend_rate_points_from_departures(kpi)
    if not out:
        wip = _wip_trend_hist_for_chart(kpi)
        if wip:
            out = [(ts, 0.0, 0.0) for ts, _ in wip]
    return out


def _rate_per_sec_to_pcs_min(rate_per_sec: float) -> float:
    return round(float(rate_per_sec) * _RATE_PER_SEC_TO_PCS_MIN, 4)


def _rate_trend_reset_y_peak_history_if_session(kpi: dict) -> None:
    sid = str(kpi.get("observation_start_ts") or kpi.get("session_id") or "")
    prev = st.session_state.get(_SESSION_RATE_Y_SESSION)
    if prev != sid:
        st.session_state[_SESSION_RATE_Y_SESSION] = sid
        st.session_state[_SESSION_RATE_Y_PEAKS] = []
        st.session_state[_SESSION_SCRAP_Y_PEAKS] = []


def _rate_axis_y_range(
    kpi: dict,
    values: list[float],
    *,
    peaks_key: str,
    empty_hi: float,
    min_hi: float = 0.0,
) -> list[float]:
    """单轴 [0, peak*pad]；跨刷新保留峰值上限。"""
    _rate_trend_reset_y_peak_history_if_session(kpi)
    frame_max = 0.0
    for v in values:
        try:
            frame_max = max(frame_max, float(v))
        except (TypeError, ValueError):
            pass
    peaks: list[float] = list(st.session_state.get(peaks_key) or [])
    peaks.append(frame_max)
    if len(peaks) > _RATE_TREND_Y_MAX_HISTORY:
        peaks = peaks[-_RATE_TREND_Y_MAX_HISTORY:]
    st.session_state[peaks_key] = peaks
    peak = max(peaks) if peaks else 0.0
    hi = peak * _RATE_TREND_Y_PAD
    if hi <= 0:
        hi = empty_hi
    hi = max(hi, min_hi)
    return [0.0, hi]


def _rate_trend_apply_dual_axes(
    fig: go.Figure,
    *,
    empty: bool,
    comp_y_range: list[float],
    scrap_y_range: list[float],
    x_range: tuple[datetime.datetime, datetime.datetime] | None = None,
) -> None:
    _trend_apply_grid(fig)
    fig.update_yaxes(
        title=dict(text="", font=dict(size=_FONT_CHART_AXIS_PX)),
        range=comp_y_range,
        autorange=False,
        nticks=5,
        secondary_y=False,
    )
    fig.update_yaxes(
        title=dict(text="", font=dict(size=_FONT_CHART_AXIS_PX)),
        range=scrap_y_range,
        autorange=False,
        nticks=5,
        secondary_y=True,
        showgrid=False,
    )
    _trend_pair_apply_xaxis(fig, empty=empty and x_range is None, x_range=x_range)


def _fig_completion_scrap_over_time(
    kpi: dict,
    *,
    x_range: tuple[datetime.datetime, datetime.datetime] | None = None,
    hist: list[tuple[float, float, float]] | None = None,
) -> go.Figure:
    hist = _extend_rate_hist_to_x_range(
        hist if hist is not None else _rate_trend_hist_for_chart(kpi),
        x_range,
    )
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    layout_kw = _trend_pair_layout_extras(margin_left=48, margin_right=48)
    if not hist:
        fig.update_layout(
            **layout_kw,
            title=_trend_chart_title(_CHART_TITLE_RATES),
        )
        _stub_t = datetime.datetime.now()
        fig.add_trace(
            go.Scatter(
                x=[_stub_t],
                y=[0],
                mode="lines+markers",
                name="Completion",
                line=dict(color=_KPI_COLOR_COMPLETION, width=2.5),
                marker=dict(size=3, color=_KPI_COLOR_COMPLETION, opacity=0),
                opacity=0,
                hoverinfo="skip",
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=[_stub_t],
                y=[0],
                mode="lines+markers",
                name="Scrap",
                line=dict(color=_KPI_COLOR_SCRAP, width=2.5),
                marker=dict(size=3, color=_KPI_COLOR_SCRAP, opacity=0),
                opacity=0,
                hoverinfo="skip",
            ),
            secondary_y=True,
        )
        _rate_trend_apply_dual_axes(
            fig,
            empty=True,
            comp_y_range=[0.0, _RATE_TREND_COMP_EMPTY_HI],
            scrap_y_range=[0.0, _RATE_TREND_SCRAP_EMPTY_HI],
            x_range=x_range,
        )
        return fig
    x_dt = [
        datetime.datetime.fromtimestamp(max(0.0, min(1e12, float(ts))))
        for ts, _, _ in hist
    ]
    comp_y = [_rate_per_sec_to_pcs_min(c) for _, c, _ in hist]
    scrap_y = [_rate_per_sec_to_pcs_min(s) for _, _, s in hist]
    comp_c = _KPI_COLOR_COMPLETION
    scrap_c = _KPI_COLOR_SCRAP
    comp_mk = dict(
        size=3,
        color=comp_c,
        symbol="circle",
        line=dict(width=1, color="#ffffff"),
    )
    scrap_mk = dict(
        size=3,
        color=scrap_c,
        symbol="circle",
        line=dict(width=1, color="#ffffff"),
    )
    fig.add_trace(
        go.Scatter(
            x=x_dt,
            y=comp_y,
            mode="lines+markers",
            name="Completion",
            line=dict(color=comp_c, width=2.5),
            marker=comp_mk,
            selected=dict(marker=dict(size=10, color=comp_c)),
            unselected=dict(marker=dict(size=3, opacity=1.0)),
            hovertemplate="Completion: %{y:.3g} pcs/min<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x_dt,
            y=scrap_y,
            mode="lines+markers",
            name="Scrap",
            line=dict(color=scrap_c, width=2.5),
            marker=scrap_mk,
            selected=dict(marker=dict(size=10, color=scrap_c)),
            unselected=dict(marker=dict(size=3, opacity=1.0)),
            hovertemplate="Scrap: %{y:.3g} pcs/min<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        **layout_kw,
        title=_trend_chart_title(_CHART_TITLE_RATES),
    )
    _rate_trend_apply_dual_axes(
        fig,
        empty=False,
        comp_y_range=_rate_axis_y_range(
            kpi,
            comp_y,
            peaks_key=_SESSION_RATE_Y_PEAKS,
            empty_hi=_RATE_TREND_COMP_EMPTY_HI,
        ),
        scrap_y_range=_rate_axis_y_range(
            kpi,
            scrap_y,
            peaks_key=_SESSION_SCRAP_Y_PEAKS,
            empty_hi=_RATE_TREND_SCRAP_EMPTY_HI,
            min_hi=0.5,
        ),
        x_range=x_range,
    )
    return fig


def render_kpi_trends_block(kpi: dict | None) -> None:
    """System KPI 下 WIP / Completion & Scrap 双图（无独立 Trends 区块标题）。"""
    k = dict(kpi or {})
    wip_hist = _wip_trend_hist_for_chart(k)
    rate_hist = _rate_trend_hist_for_chart(k)
    x_range = _trend_x_range_from_series(k, wip_hist, rate_hist)
    with st.container(key="kpi_trends_wrap"):
        c1, c2 = st.columns(2, gap="small")
        with c1:
            st.plotly_chart(
                _fig_wip_over_time(k, x_range=x_range, hist=wip_hist),
                use_container_width=True,
                height=_TREND_PLOTLY_CONTAINER_HEIGHT,
                config=_TREND_PLOT_CONFIG,
                key="kpi_trend_wip_over_time",
            )
        with c2:
            st.plotly_chart(
                _fig_completion_scrap_over_time(k, x_range=x_range, hist=rate_hist),
                use_container_width=True,
                height=_TREND_PLOTLY_CONTAINER_HEIGHT,
                config=_TREND_PLOT_CONFIG,
                key="kpi_trend_completion_scrap",
            )

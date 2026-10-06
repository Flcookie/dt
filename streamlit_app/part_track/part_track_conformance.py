"""Part Track & Conformance — corner2 START 定界主流程圈；FINISH 后回线照常收录；路径期望对齐 code/flow.py。"""
from __future__ import annotations

import copy
import html as html_module
import re
from typing import Any, TypedDict

import part_track.flow_conformance_engine as fce


def _natural_part_id_key(pid: str) -> tuple:
    """p1, p2, … p10（与 neo4j_backend.natural_part_id_key 一致）。"""
    s = (pid or "").strip()
    m = re.match(r"^[Pp]?(\d+)$", s)
    if m:
        return (0, int(m.group(1)), s.lower())
    return (1, 0, s.lower())


class TraceActivityStyle(TypedDict):
    color: str
    opacity: float
    bold: bool

SLOTS: tuple[str, ...] = (
    "st11",
    "st21_22",
    "st31",
    "st41",
    "st51_52",
    "st61",
    "st71",
)

SLOT_HEADERS: dict[str, str] = {
    "st11": "St1-1",
    "st21_22": "St2-1/2-2",
    "st31": "St3-1",
    "st41": "St4-1",
    "st51_52": "St5-1/5-2",
    "st61": "St6-1",
    "st71": "St7-1",
}

# Digital Twin / 产线命名：与 SLOT_HEADERS 一一对应，仅展示用，逻辑仍用 SLOTS + SLOT_HEADERS。
SLOT_HEADERS_M: dict[str, str] = {
    "st11": "M1-1",
    "st21_22": "M2-1·M2-2",
    "st31": "M3-1",
    "st41": "M4-1",
    "st51_52": "M5-1·M5-2",
    "st61": "M6-1",
    "st71": "M7-1",
}

STATION_TO_SLOT: dict[str, str] = {
    "station11": "st11",
    "station21": "st21_22",
    "station22": "st21_22",
    "station31": "st31",
    "station41": "st41",
    "station51": "st51_52",
    "station52": "st51_52",
    "station61": "st61",
    "station71": "st71",
}

STATION_DISPLAY: dict[str, str] = {
    "station11": "ST1-1",
    "station21": "ST2-1",
    "station22": "ST2-2",
    "station31": "ST3-1",
    "station41": "ST4-1",
    "station51": "ST5-1",
    "station52": "ST5-2",
    "station61": "ST6-1",
    "station71": "ST7-1",
}

LOCATION_MAP: dict[str, str] = {
    "corner2": "Check In",
    "station11": "Station 1-1",
    "station21": "Station 2-1",
    "station22": "Station 2-2",
    "station31": "Station 3-1",
    "station41": "Station 4-1",
    "station51": "Station 5-1",
    "station52": "Station 5-2",
    "station61": "Station 6-1",
    "station71": "Station 7-1",
    "splitter5": "Check Out",
    "splitter1": "Splitter 1",
    "splitter2": "Splitter 2",
    "splitter3": "Splitter 3",
    "splitter4": "Splitter 4",
    "corner1": "Corner 1",
}

MEANINGFUL_EVENTS: dict[str, frozenset[str]] = {
    "corner1": frozenset({"TRANSFER", "RETURN"}),
    "corner2": frozenset({"START"}),
    "station11": frozenset({"LOAD", "PROCESS", "UNLOAD", "FAIL", "BLOCK"}),
    "station21": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station22": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station31": frozenset({"LOAD", "PROCESS", "UNLOAD", "FAIL", "BLOCK"}),
    "station41": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station51": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station52": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station61": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "station71": frozenset({"LOAD", "PROCESS", "PASS", "UNLOAD", "FAIL", "BLOCK"}),
    "splitter1": frozenset({"FORWARD"}),
    "splitter2": frozenset({"FORWARD", "RETURN"}),
    "splitter3": frozenset({"FORWARD", "RETURN"}),
    "splitter4": frozenset({"FORWARD", "RETURN"}),
    "splitter5": frozenset({"CHECKOUT", "FINISH", "SCRAP", "RETURN"}),
}

CELL_SYMBOL: dict[str, str] = {
    "NOT_DONE": "\u25a1",
    "DONE": "\u2713",
    "REWORK": "\u26a0",
    "SCRAP": "\u2717",
}


def _initial_grid() -> dict[str, str]:
    return {s: "NOT_DONE" for s in SLOTS}


def _grid_mark_scrap_at_lap_close(grid: dict[str, str]) -> None:
    """Deprecated: SCRAP is lap-level only (splitter5); matrix cells keep DONE/REWORK."""
    _ = grid


def _norm_cid(component_id: str) -> str:
    return str(component_id or "").strip().lower()


def _sort_steps(steps: list[dict]) -> list[dict]:
    def key(ev: dict) -> tuple[float, str]:
        ts = ev.get("timestamp")
        if ts is not None:
            try:
                return (float(ts), str(ev.get("time") or ""))
            except (TypeError, ValueError):
                pass
        return (0.0, str(ev.get("time") or ""))

    return sorted(steps, key=key)


def _ev_ts(ev: dict | None) -> float:
    if not ev:
        return 0.0
    try:
        t = ev.get("timestamp")
        return float(t) if t is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_lap_conformance(
    grid: dict[str, str],
    lap_failed_stations: set[str] | frozenset[str] | None = None,
    *,
    lap_outcome: str | None = None,
    flow_anomaly: bool = False,
    all_steps_reached: bool = False,
) -> dict[str, str]:
    """闭圈判定：SCRAP → ANOMALY → REWORK → SKIPPED → NORMAL（路径逻辑对齐 code/flow.py）。"""
    if str(lap_outcome or "").strip().upper() == "SCRAP":
        return {"status": "SCRAP", "detail": ""}
    fset: set[str] = set(lap_failed_stations or ())
    is_scrap = any(grid.get(s) == "SCRAP" for s in SLOTS)
    if is_scrap:
        return {"status": "SCRAP", "detail": ""}
    if flow_anomaly:
        return {"status": "ANOMALY", "detail": "Flow path anomaly"}
    if fset or any(grid.get(s) == "REWORK" for s in SLOTS):
        detail = ", ".join(
            STATION_DISPLAY.get(st, st) for st in sorted(fset)
        )
        return {"status": "REWORK", "detail": detail}
    skipped = [s for s in SLOTS if grid.get(s) == "NOT_DONE"]
    if skipped or not all_steps_reached:
        return {"status": "SKIPPED", "detail": ", ".join(skipped)}
    return {"status": "NORMAL", "detail": ""}


def _lap_conformance_label_color_status(
    grid: dict[str, str],
    lap_failed_stations: set[str] | frozenset[str] | None = None,
    *,
    lap_outcome: str | None = None,
    flow_anomaly: bool = False,
    all_steps_reached: bool = False,
) -> tuple[str, str, str]:
    c = get_lap_conformance(
        grid,
        lap_failed_stations,
        lap_outcome=lap_outcome,
        flow_anomaly=flow_anomaly,
        all_steps_reached=all_steps_reached,
    )
    st = c["status"]
    d = c.get("detail") or ""
    if st == "NORMAL":
        return "\u2713 Normal", "#3fb950", "NORMAL"
    if st == "REWORK":
        return "\u21ba Rework: {}".format(d), "#fb923c", "REWORK"
    if st == "ANOMALY":
        return "\u26a0 Flow anomaly", "#f85149", "ANOMALY"
    if st == "SKIPPED":
        return "\u26a0 Skipped: {}".format(d), "#f85149", "SKIPPED"
    if st == "SCRAP":
        return "\u2717 Scrap", "#f85149", "SCRAP"
    return "\u2014", "#8b949e", "UNKNOWN"


def lap_conformance_badge_from_lap(
    grid: dict[str, str],
    lap_failed_stations: set[str] | frozenset[str] | None = None,
) -> tuple[str, str]:
    """Complete Trace 圈标题后缀：文案 + HTML 颜色。"""
    lab, col, _ = _lap_conformance_label_color_status(grid, lap_failed_stations)
    return lab, col


def conformance_column_display(rep: dict[str, Any]) -> tuple[str, str]:
    """(cell text, style key: normal|rework|skipped|scrap|in_progress|neutral)."""
    if not rep.get("has_cycle_context"):
        return "\u2014", "neutral"
    if rep.get("lap_open"):
        return "\u2014 In progress", "in_progress"
    laps = rep.get("laps") or []
    last_outcome = str(laps[-1].get("outcome") or "") if laps else ""
    last_flow = (laps[-1].get("flow") or {}) if laps else {}
    reached = last_flow.get("reached") or []
    all_ok = bool(reached) and all(reached)
    lab, _, st = _lap_conformance_label_color_status(
        rep["display_grid"],
        rep.get("last_closed_lap_failed_stations") or set(),
        lap_outcome=last_outcome,
        flow_anomaly=bool(rep.get("flow_anomaly")),
        all_steps_reached=all_ok,
    )
    key = {
        "NORMAL": "normal",
        "REWORK": "rework",
        "ANOMALY": "anomaly",
        "SKIPPED": "skipped",
        "SCRAP": "scrap",
    }.get(st, "neutral")
    return lab, key


def replay_part_trace(steps: list[dict]) -> dict[str, Any]:
    """逐事件回放：9 步路径期望（code/flow.py）+ lap 定界（corner2 START / splitter5 FINISH|SCRAP）。"""
    ordered = _sort_steps([dict(s) for s in (steps or [])])
    laps: list[dict[str, Any]] = []
    flow = fce.new_flow_state()
    lap_events: list[dict] = []
    lap_open = False
    lap_start_ts: float | None = None
    last_closed_grid: dict[str, str] = _initial_grid()
    last_closed_step_grid: dict[int, str] = {
        i: "NOT_DONE" for i in range(fce.N_STEPS)
    }
    last_closed_lap_failed_stations: set[str] = set()
    last_closed_flow_anomaly = False
    last_closed_all_steps = False
    current_location = "—"
    last_ev: dict | None = None
    post_finish_buffer: list[dict] = []

    def archive_lap(outcome: str, end_ts: float, flow_snap: fce.FlowState) -> None:
        start_ts = lap_start_ts if lap_start_ts is not None else end_ts
        dur = max(0.0, end_ts - start_ts) if end_ts and start_ts else 0.0
        final_grid = fce.flow_state_to_display_grid(flow_snap)
        lap_failed = set(flow_snap.failed_stations)
        laps.append(
            {
                "outcome": outcome,
                "duration_sec": round(dur, 1),
                "events": copy.deepcopy(lap_events),
                "final_grid": copy.deepcopy(final_grid),
                "lap_failed_stations": lap_failed,
                "flow": fce.flow_state_snapshot(flow_snap),
            }
        )

    for ev in ordered:
        last_ev = ev
        comp = _norm_cid(str(ev.get("component_id") or ""))
        act = str(ev.get("activity") or "").strip().upper()
        ts_raw = ev.get("timestamp")
        try:
            ts = float(ts_raw) if ts_raw is not None else 0.0
        except (TypeError, ValueError):
            ts = 0.0

        current_location = LOCATION_MAP.get(comp, comp)

        if comp == "corner2" and act == "START":
            if lap_open and lap_events:
                archive_lap("IN_PROGRESS", ts, flow)
            buf = post_finish_buffer
            post_finish_buffer = []
            flow = fce.new_flow_state()
            lap_events = buf + [ev]
            lap_open = True
            lap_start_ts = _ev_ts(buf[0]) if buf else ts
            continue

        if (
            not lap_open
            and laps
            and str(laps[-1].get("outcome") or "") == "FINISH"
        ):
            post_finish_buffer.append(ev)
            continue

        if lap_open:
            lap_events.append(ev)

            if comp == "splitter5" and act == "FINISH":
                fce.apply_fail_rework_to_flow(flow)
                archive_lap("FINISH", ts, flow)
                last_closed_grid = fce.flow_state_to_display_grid(flow)
                last_closed_step_grid = fce.flow_state_to_step_display_grid(flow)
                last_closed_lap_failed_stations = set(flow.failed_stations)
                last_closed_flow_anomaly = flow.anomaly
                last_closed_all_steps = all(flow.reached)
                lap_open = False
                lap_events = []
                flow = fce.new_flow_state()
                continue

            if comp == "splitter5" and act == "SCRAP":
                fce.apply_flow_event(flow, ev)
                archive_lap("SCRAP", ts, flow)
                last_closed_grid = fce.flow_state_to_display_grid(flow)
                last_closed_step_grid = fce.flow_state_to_step_display_grid(flow)
                last_closed_lap_failed_stations = set()
                last_closed_flow_anomaly = flow.anomaly
                last_closed_all_steps = all(flow.reached)
                lap_open = False
                lap_events = []
                flow = fce.new_flow_state()
                continue

            fce.apply_flow_event(flow, ev)

    if post_finish_buffer and not lap_open and laps and str(
        laps[-1].get("outcome") or ""
    ) == "FINISH":
        flow = fce.new_flow_state()
        lap_events = copy.deepcopy(post_finish_buffer)
        lap_open = True
        lap_start_ts = _ev_ts(post_finish_buffer[0])
        post_finish_buffer.clear()
        for ev in lap_events:
            fce.apply_flow_event(flow, ev)

    if lap_open:
        display_grid = fce.flow_state_to_display_grid(flow)
        display_step_grid = fce.flow_state_to_step_display_grid(flow)
        conf = "IN PROGRESS"
    else:
        display_grid = copy.deepcopy(last_closed_grid)
        display_step_grid = copy.deepcopy(last_closed_step_grid)
        c = get_lap_conformance(
            display_grid,
            last_closed_lap_failed_stations,
            flow_anomaly=last_closed_flow_anomaly,
            all_steps_reached=last_closed_all_steps,
        )
        conf = c["status"]

    lap_index_display = len(laps) + (1 if lap_open else 0)
    outcome_cur = "IN_PROGRESS" if lap_open else (laps[-1]["outcome"] if laps else "—")
    has_cycle_context = bool(laps) or lap_open

    return {
        "laps": laps,
        "display_grid": display_grid,
        "display_step_grid": display_step_grid,
        "lap_open": lap_open,
        "current_lap_events": copy.deepcopy(lap_events) if lap_open else [],
        "current_location": current_location,
        "conformance": conf,
        "lap_index_display": lap_index_display,
        "current_lap_outcome": outcome_cur,
        "last_event": last_ev,
        "has_cycle_context": has_cycle_context,
        "last_closed_lap_failed_stations": last_closed_lap_failed_stations,
        "flow_anomaly": flow.anomaly if lap_open else last_closed_flow_anomaly,
        "flow_state": fce.flow_state_snapshot(flow) if lap_open else (
            (laps[-1].get("flow") if laps else None)
        ),
    }


def cell_display(state: str) -> str:
    sym = CELL_SYMBOL.get(state, "?")
    short = {"NOT_DONE": "ND", "DONE": "OK", "REWORK": "RW", "SCRAP": "SC"}.get(
        state, state[:2]
    )
    return "{} {}".format(sym, short)


def part_track_grid_cell_display(
    slot: str, state: str, *, lap_open: bool, has_cycle_context: bool
) -> str:
    st = state or "NOT_DONE"
    if st == "QC":
        return "QC"
    if has_cycle_context and st == "NOT_DONE" and not lap_open:
        return "! {}".format(cell_display("NOT_DONE"))
    return cell_display(st)


def event_is_meaningful(ev: dict) -> bool:
    comp = _norm_cid(str(ev.get("component_id") or ""))
    act = str(ev.get("activity") or "").strip().upper()
    allowed = MEANINGFUL_EVENTS.get(comp)
    return bool(allowed and act in allowed)


def _complete_trace_line_text(ev: dict) -> str:
    t = str(ev.get("time") or "").strip()
    if len(t) > 26:
        t = t[:26]
    cid = str(ev.get("component_id") or "").strip()
    act = str(ev.get("activity") or "").strip()
    return "{:<26}  {:<14}  {}".format(t, cid, act)


def trace_activity_color(activity: str) -> TraceActivityStyle:
    """Complete trace：按事件语义着色，含透明度与字重（路由事件降权）。"""
    act = (activity or "").strip().upper()
    if act == "LOAD":
        return {"color": "#79c0ff", "opacity": 1.0, "bold": True}
    if act == "PROCESS":
        return {"color": "#56d364", "opacity": 1.0, "bold": True}
    if act == "UNLOAD":
        return {"color": "#d2a8ff", "opacity": 1.0, "bold": True}
    if act == "FAIL":
        return {"color": "#f85149", "opacity": 1.0, "bold": True}
    if act == "BLOCK":
        return {"color": "#ffa657", "opacity": 1.0, "bold": True}
    if act == "START":
        return {"color": "#e6edf3", "opacity": 1.0, "bold": False}
    if act == "FINISH":
        return {"color": "#3fb950", "opacity": 1.0, "bold": True}
    if act == "SCRAP":
        return {"color": "#ff7b72", "opacity": 1.0, "bold": True}
    if act == "FORWARD":
        return {"color": "#ffa657", "opacity": 0.7, "bold": False}
    if act in ("PASS", "RETURN", "TRANSFER", "CHECKOUT"):
        return {"color": "#6e7681", "opacity": 0.5, "bold": False}
    return {"color": "#8b949e", "opacity": 0.7, "bold": False}


def trace_component_id_color(component_id: str) -> str:
    c = _norm_cid(component_id)
    if "station" in c:
        return "#58a6ff"
    if "splitter" in c:
        return "#e3b341"
    if "corner" in c:
        return "#8b949e"
    return "#6e7681"


_ROUTING_COLLAPSE: frozenset[str] = frozenset({"PASS", "RETURN", "TRANSFER"})


def collapse_routing_events(events: list[dict]) -> list[Any]:
    """连续的 PASS/RETURN/TRANSFER 超过 2 条则折叠为一条占位（内含 items）。"""
    out: list[Any] = []
    i = 0
    seq = list(events or [])
    n = len(seq)
    while i < n:
        ev = seq[i]
        a = str(ev.get("activity") or "").strip().upper()
        if a in _ROUTING_COLLAPSE:
            group: list[dict] = []
            while i < n and (
                str(seq[i].get("activity") or "").strip().upper() in _ROUTING_COLLAPSE
            ):
                group.append(seq[i])
                i += 1
            if len(group) <= 2:
                out.extend(group)
            else:
                out.append({"_routing_collapsed": True, "items": group})
        else:
            out.append(ev)
            i += 1
    return out


def _routing_collapsed_block_html(items: list[dict]) -> str:
    cnt = len(items)
    summary = html_module.escape("··· {} routing events (click to expand)".format(cnt))
    inner = "".join(_trace_row_html(ev) for ev in items)
    return (
        '<details style="margin:0;padding:0;border:none;">'
        '<summary style="cursor:pointer;list-style-position:outside;font-family:ui-monospace,Consolas,monospace;'
        "font-size:11px;color:#444c56;padding:1px 8px;font-style:italic;line-height:1.4;"
        'outline:none">{}</summary>'
        '<div style="padding:0;margin:0">{}</div>'
        "</details>"
    ).format(summary, inner)


def _lap_outcome_badge(oc_raw: str) -> tuple[str, str, str]:
    """闭合圈结果 pill：(显示文案, bg, fg)"""
    oc = str(oc_raw or "?").strip().upper()
    if oc == "FINISH":
        return "FINISH", "#1a3a1f", "#3fb950"
    if oc == "SCRAP":
        return "SCRAP", "#3a1010", "#f85149"
    if oc == "RETURN":
        return "RETURN", "#2d2640", "#a78bfa"
    if oc == "IN_PROGRESS":
        return "IN_PROGRESS", "#1a2332", "#79c0ff"
    return oc_raw or "?", "#21262d", "#e6edf3"


def _trace_conformance_span(
    fg: dict[str, str],
    lf: Any,
    *,
    lap_outcome: str | None = None,
    flow_anomaly: bool = False,
    all_steps_reached: bool = False,
) -> tuple[str, str, str]:
    """(图标, 主文案不带 emoji 前缀, CSS color)"""
    lf_set: set[str] = set(lf or [])
    lab, _col, status = _lap_conformance_label_color_status(
        fg,
        lf_set,
        lap_outcome=lap_outcome,
        flow_anomaly=flow_anomaly,
        all_steps_reached=all_steps_reached,
    )
    icon = {"NORMAL": "\u2713", "REWORK": "\u21ba", "ANOMALY": "\u26a0", "SKIPPED": "\u26a0", "SCRAP": "\u2717"}.get(
        status,
        "\u2014",
    )
    cmap = {
        "NORMAL": "#3fb950",
        "REWORK": "#ffa657",
        "ANOMALY": "#f85149",
        "SKIPPED": "#f85149",
        "SCRAP": "#f85149",
    }
    ccol = cmap.get(status, "#8b949e")
    for ch in ("\u2713", "\u21ba", "\u26a0", "\u2717"):
        if lab.startswith(ch):
            lab = lab[len(ch) :].strip()
            break
    body = lab or {
        "NORMAL": "Normal",
        "REWORK": "Rework",
        "ANOMALY": "Flow anomaly",
        "SKIPPED": "Skipped",
        "SCRAP": "Scrap",
    }.get(status, "")
    return icon, body, ccol


def _closed_lap_header_html(
    lap_index: int,
    outcome: str,
    duration_sec: float,
    fg: dict[str, str],
    lf: Any,
    flow: dict[str, Any] | None = None,
) -> str:
    res_txt, res_bg, res_fg = _lap_outcome_badge(outcome)
    flow_d = flow or {}
    reached = flow_d.get("reached") or []
    ic, cf_body, cf_col = _trace_conformance_span(
        fg,
        lf,
        lap_outcome=outcome,
        flow_anomaly=bool(flow_d.get("anomaly")),
        all_steps_reached=bool(reached) and all(reached),
    )
    esc_res = html_module.escape(res_txt)
    dur = "{:.1f}s".format(float(duration_sec))
    lap_n = lap_index
    conf_plain = "{} {}".format(ic, cf_body).strip()
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;'
        'padding:8px 8px 6px 8px;border-bottom:1px solid #21262d;margin-bottom:2px;">'
        '<span style="font-size:12px;font-weight:700;color:#e6edf3">{}</span>'
        '<span style="font-size:11px;padding:1px 7px;border-radius:10px;font-weight:600;'
        'background:{};color:{}">{}</span>'
        '<span style="font-size:11px;color:#6e7681">{}</span>'
        '<span style="font-size:12px;font-weight:600;color:{};">{}</span>'
        "</div>"
    ).format(
        html_module.escape("Lap {}".format(lap_n)),
        res_bg,
        res_fg,
        esc_res,
        html_module.escape(dur),
        cf_col,
        html_module.escape(conf_plain),
    )


def _open_lap_header_html(lap_index: int) -> str:
    _, res_bg, res_fg = _lap_outcome_badge("IN_PROGRESS")
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;'
        'padding:8px 8px 6px 8px;border-bottom:1px solid #21262d;margin-bottom:2px;">'
        '<span style="font-size:12px;font-weight:700;color:#e6edf3">{}</span>'
        '<span style="font-size:11px;padding:1px 7px;border-radius:10px;font-weight:600;'
        'background:{};color:{}">IN_PROGRESS</span>'
        '<span style="font-size:11px;color:#79c0ff;font-weight:600">\u2014 In progress</span>'
        "</div>"
    ).format(html_module.escape("Lap {}".format(lap_index)), res_bg, res_fg)


def _trace_row_html(ev: dict) -> str:
    """单行 Complete trace（grid：time | component_id | activity）。"""
    t_raw = str(ev.get("time") or "").strip()
    t = t_raw[:26] if len(t_raw) > 26 else t_raw
    cid = str(ev.get("component_id") or "").strip()
    act = str(ev.get("activity") or "").strip()
    st = trace_activity_color(act)
    color = st["color"]
    opacity = float(st["opacity"])
    bold = st["bold"]
    font_weight = "600" if bold else "400"
    time_color = color if bold else "#6e7681"

    au = act.strip().upper()
    route_low = au in ("PASS", "RETURN", "TRANSFER", "CHECKOUT")
    comp_c = trace_component_id_color(cid)
    route_fwd = au == "FORWARD"
    if route_low:
        lh, fz = "1.5", "11px"
        pad = "1px 8px"
    elif route_fwd:
        lh, fz = "1.55", "11px"
        pad = "2px 8px"
    elif au in ("LOAD", "PROCESS", "UNLOAD", "FAIL", "FINISH", "SCRAP", "START"):
        lh, fz = "1.7", "12px"
        pad = "3px 8px"
    else:
        lh, fz = "1.65", "12px"
        pad = "2px 8px"
    return (
        '<div style="display:grid;grid-template-columns:90px 140px minmax(80px,1fr);'
        "gap:0;padding:{pad};opacity:{op};font-family:ui-monospace,Consolas,monospace;"
        'font-size:{fz};line-height:{lh}">'
        '<span style="color:{time_color};font-weight:{fw}">{et}</span>'
        '<span style="color:{comp_color};font-weight:400">{ec}</span>'
        '<span style="color:{act_color};font-weight:{fw}">{ea}</span>'
        "</div>"
    ).format(
        pad=pad,
        op=opacity,
        fz=fz,
        lh=lh,
        time_color=time_color,
        fw=font_weight,
        et=html_module.escape(t),
        comp_color=comp_c,
        ec=html_module.escape(cid),
        act_color=color,
        ea=html_module.escape(act),
    )


def _complete_trace_legend_html() -> str:
    """5 项图例（语义分组）。"""
    items = [
        (
            "LOAD / UNLOAD",
            ("#79c0ff", "#d2a8ff"),
            None,
        ),
        ("PROCESS", ("#56d364",), None),
        ("FAIL", ("#f85149",), None),
        (
            "FINISH / SCRAP",
            ("#3fb950", "#ff7b72"),
            None,
        ),
        ("PASS / RETURN …", ("#6e7681",), "0.65"),
    ]
    chunks: list[str] = []
    for label, cols, op in items:
        swatches = "".join(
            '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
            'background:{};margin-right:4px;vertical-align:middle;"></span>'.format(c)
            for c in cols
        )
        sty = (
            "display:inline-flex;align-items:center;gap:4px;margin-right:14px;"
            "white-space:nowrap;color:#8b949e;font-size:11px"
        )
        if op:
            sty += ";opacity:{}".format(op)
        chunks.append(
            '<span style="{}">{}{}</span>'.format(
                sty,
                swatches,
                html_module.escape(label),
            )
        )
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px 0;'
        'margin:4px 0 12px 0;padding:0 8px 10px 8px;border-bottom:1px solid #30363d">'
        "{}</div>"
    ).format("".join(chunks))


def _complete_trace_grid_header_html() -> str:
    """与事件行对齐的列标题。"""
    return (
        '<div style="display:grid;grid-template-columns:90px 140px minmax(80px,1fr);'
        "gap:0;padding:2px 8px;font-family:ui-monospace,Consolas,monospace;"
        'font-size:12px;line-height:1.7;color:#8b949e;font-weight:600;margin:0">'
        "<span>time</span>"
        "<span>component_id</span>"
        "<span>activity</span>"
        "</div>"
    )


def format_complete_trace_meaningful_html(replay: dict[str, Any]) -> str:
    rows: list[str] = []
    laps = replay.get("laps") or []

    header_block = (
        _complete_trace_grid_header_html()
        + '<div style="margin:0 0 10px 0;border-bottom:1px solid #30363d;padding-bottom:8px;"></div>'
    )

    def append_spacer() -> None:
        rows.append('<div style="height:8px;"></div>')

    for i, lap in enumerate(laps, start=1):
        oc = lap.get("outcome") or "?"
        dur = float(lap.get("duration_sec", 0) or 0)
        fg = lap.get("final_grid") or {}
        lf = lap.get("lap_failed_stations") or set()
        rows.append(
            _closed_lap_header_html(
                i,
                str(oc),
                dur,
                dict(fg) if isinstance(fg, dict) else {},
                lf,
                lap.get("flow") if isinstance(lap.get("flow"), dict) else None,
            )
        )
        seq = [
            ev
            for ev in (lap.get("events") or [])
            if event_is_meaningful(ev)
        ]
        for chunk in collapse_routing_events(seq):
            if isinstance(chunk, dict) and chunk.get("_routing_collapsed"):
                rows.append(_routing_collapsed_block_html(chunk["items"]))
            else:
                rows.append(_trace_row_html(chunk))
        append_spacer()

    if replay.get("lap_open") and replay.get("current_lap_events"):
        idx = len(laps) + 1
        rows.append(_open_lap_header_html(idx))
        seq_cur = [
            ev
            for ev in replay["current_lap_events"]
            if event_is_meaningful(ev)
        ]
        for chunk in collapse_routing_events(seq_cur):
            if isinstance(chunk, dict) and chunk.get("_routing_collapsed"):
                rows.append(_routing_collapsed_block_html(chunk["items"]))
            else:
                rows.append(_trace_row_html(chunk))

    block = header_block + "".join(rows)
    return (
        '<div style="background:#0d1117;padding:12px 14px;border-radius:8px;'
        'border:1px solid #30363d">{}</div>'
    ).format(block)


def build_table_row(part_id: str, steps: list[dict]) -> dict[str, Any]:
    rep = replay_part_trace(steps)
    hcx = bool(rep.get("has_cycle_context"))
    lap_open = bool(rep.get("lap_open"))
    row: dict[str, Any] = {"Part ID": part_id}
    for slot in SLOTS:
        row[SLOT_HEADERS[slot]] = part_track_grid_cell_display(
            slot,
            rep["display_grid"].get(slot, "NOT_DONE"),
            lap_open=lap_open,
            has_cycle_context=hcx,
        )
    conf_text, conf_style = conformance_column_display(rep)
    row["Conformance"] = conf_text
    row["_conf_style"] = conf_style
    row["Current Location"] = rep["current_location"]
    row["_replay"] = rep
    row["_steps"] = steps
    return row


def build_session_table_rows(parts: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for p in parts or []:
        pid = str(p.get("part_id") or "").strip()
        if not pid:
            continue
        steps = list(p.get("steps") or [])
        rows.append(build_table_row(pid, steps))

    rows.sort(
        key=lambda row: _natural_part_id_key(str(row.get("Part ID") or ""))
    )
    return rows


def filter_parts_to_replay_kpi_progress(parts: list[dict], kpi: Any) -> list[dict]:
    """``run_mode == replay`` 时只保留已回放到的工步（与 ``chart_time_unix`` 一致，见 ``kpi_calculator``）。"""
    if not isinstance(kpi, dict) or (kpi.get("run_mode") or "") != "replay":
        return parts
    try:
        cap = float(kpi.get("chart_time_unix") or 0.0)
    except (TypeError, ValueError):
        return parts
    if cap <= 0.0:
        return parts
    out: list[dict] = []
    for p in parts or []:
        st_list = list(p.get("steps") or [])
        kept: list[dict] = []
        for s in st_list:
            ts = s.get("timestamp")
            if ts is None:
                continue
            try:
                tsv = float(ts)
            except (TypeError, ValueError):
                continue
            if tsv <= cap + 1e-4:
                kept.append(s)
        if not kept:
            continue
        q: dict[str, Any] = dict(p)
        q["steps"] = kept
        q["flow"] = " \u2192 ".join(
            "[{}] {}@{}".format(
                x.get("time", ""),
                x.get("component_id", ""),
                x.get("activity", ""),
            )
            for x in kept
        )
        out.append(q)
    return out

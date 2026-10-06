"""
Factory Floor 平面图（Plotly）：几何与样式与 ``code/monitoring.py`` 中 Factory Floor 一致。
用于 Digital Twin；不含 Stage 彩带；工位固定为 IDLE 绿色、不展示运行状态文案。
"""
from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go

# ── 与 code/monitoring.py 相同 ─────────────────────────────────────────────
machines_conf = [
    {"name": "M1-1", "x": 2, "id": "station11"},
    {"name": "M2-1", "x": 6, "id": "station21"},
    {"name": "M2-2", "x": 8, "id": "station22"},
    {"name": "M3-1", "x": 12, "id": "station31"},
    {"name": "M4-1", "x": 16, "id": "station41"},
    {"name": "M5-1", "x": 18, "id": "station51"},
    {"name": "M5-2", "x": 20, "id": "station52"},
    {"name": "M6-1", "x": 24, "id": "station61"},
    {"name": "M7-1", "x": 28, "id": "station71"},
]

draw_mergers = [
    {"x": 4, "y": 2, "name": "Merger 1"},
    {"x": 10, "y": 0, "name": "Merger 2"},
    {"x": 14, "y": 2, "name": "Merger 3"},
    {"x": 22, "y": 0, "name": "Merger 4"},
    {"x": 26, "y": 2, "name": "Merger 5"},
]

draw_splitters = [
    {"x": 10, "y": 2, "name": "Splitter 1"},
    {"x": 4, "y": 0, "name": "Splitter 2"},
    {"x": 22, "y": 2, "name": "Splitter 3"},
    {"x": 14, "y": 0, "name": "Splitter 4"},
    {"x": 26, "y": 0, "name": "Splitter 5"},
]

gates_draw = [{"x": m["x"], "name": m["name"].replace("M", "Gate ")} for m in machines_conf]
gates_draw.insert(0, {"x": 0, "name": "Corner 2"})

# 与 code/monitoring.SEQ_MAP 同内容；修改时请两处同步
SEQ_MAP: dict[tuple[str, str], tuple[float, float, str]] = {
    ("corner2", "RETURN"): (0.0, 0.0, "dx"),
    ("corner2", "START"): (0.0, 0.0, "dx"),
    ("corner2", "TRANSFER"): (0.0, 2.0, "down"),
    ("station11", "LOAD"): (2.0, 2.5, "sx"),
    ("station11", "PASS"): (2.0, 2.0, "sx"),
    ("station11", "PROCESS"): (2.0, 3.5, "down"),
    ("station11", "FAIL"): (2.0, 3.5, "down"),
    ("station11", "BLOCK"): (2.0, 3.5, "down"),
    ("station11", "UNLOAD"): (2.0, 2.5, "sx"),
    ("station11", "TRANSFER"): (4.0, 2.0, "sx"),
    ("station21", "LOAD"): (6.0, 2.5, "sx"),
    ("station21", "PASS"): (6.0, 2.0, "sx"),
    ("station21", "PROCESS"): (6.0, 3.5, "down"),
    ("station21", "FAIL"): (6.0, 3.5, "down"),
    ("station21", "BLOCK"): (6.0, 3.5, "down"),
    ("station21", "UNLOAD"): (6.0, 2.5, "sx"),
    ("station21", "TRANSFER"): (7.0, 2.0, "sx"),
    ("station22", "LOAD"): (8.0, 2.5, "sx"),
    ("station22", "PASS"): (8.0, 2.0, "sx"),
    ("station22", "PROCESS"): (8.0, 3.5, "down"),
    ("station22", "FAIL"): (8.0, 3.5, "down"),
    ("station22", "BLOCK"): (8.0, 3.5, "down"),
    ("station22", "UNLOAD"): (8.0, 2.5, "sx"),
    ("station22", "TRANSFER"): (10.0, 2.0, "sx"),
    ("splitter1", "FORWARD"): (11.0, 2.0, "sx"),
    ("splitter1", "RETURN"): (10.0, 0.5, "up"),
    ("splitter1", "TRANSFER"): (10.0, 2.0, "sx"),
    ("station31", "LOAD"): (12.0, 2.5, "sx"),
    ("station31", "PASS"): (12.0, 2.0, "sx"),
    ("station31", "PROCESS"): (12.0, 3.5, "down"),
    ("station31", "FAIL"): (12.0, 3.5, "down"),
    ("station31", "BLOCK"): (12.0, 3.5, "down"),
    ("station31", "UNLOAD"): (12.0, 2.5, "sx"),
    ("station31", "TRANSFER"): (14.0, 2.0, "sx"),
    ("station41", "LOAD"): (16.0, 2.5, "sx"),
    ("station41", "PASS"): (16.0, 2.0, "sx"),
    ("station41", "PROCESS"): (16.0, 3.5, "down"),
    ("station41", "FAIL"): (16.0, 3.5, "down"),
    ("station41", "BLOCK"): (16.0, 3.5, "down"),
    ("station41", "UNLOAD"): (16.0, 2.5, "sx"),
    ("station41", "TRANSFER"): (17.0, 2.0, "sx"),
    ("station51", "LOAD"): (18.0, 2.5, "sx"),
    ("station51", "PASS"): (18.0, 2.0, "sx"),
    ("station51", "PROCESS"): (18.0, 3.5, "down"),
    ("station51", "FAIL"): (18.0, 3.5, "down"),
    ("station51", "BLOCK"): (18.0, 3.5, "down"),
    ("station51", "UNLOAD"): (18.0, 2.5, "sx"),
    ("station51", "TRANSFER"): (19.0, 2.0, "sx"),
    ("station52", "LOAD"): (20.0, 2.5, "sx"),
    ("station52", "PASS"): (20.0, 2.0, "sx"),
    ("station52", "PROCESS"): (20.0, 3.5, "down"),
    ("station52", "FAIL"): (20.0, 3.5, "down"),
    ("station52", "BLOCK"): (20.0, 3.5, "down"),
    ("station52", "UNLOAD"): (20.0, 2.5, "sx"),
    ("station52", "TRANSFER"): (22.0, 2.0, "sx"),
    ("splitter3", "FORWARD"): (23.0, 2.0, "sx"),
    ("splitter3", "RETURN"): (22.0, 0.5, "up"),
    ("splitter3", "TRANSFER"): (22.0, 2.0, "sx"),
    ("station61", "LOAD"): (24.0, 2.5, "sx"),
    ("station61", "PASS"): (24.0, 2.0, "sx"),
    ("station61", "PROCESS"): (24.0, 3.5, "down"),
    ("station61", "FAIL"): (24.0, 3.5, "down"),
    ("station61", "BLOCK"): (24.0, 3.5, "down"),
    ("station61", "UNLOAD"): (24.0, 2.5, "sx"),
    ("station61", "TRANSFER"): (26.0, 2.0, "sx"),
    ("station71", "LOAD"): (28.0, 2.5, "sx"),
    ("station71", "PASS"): (28.0, 2.0, "sx"),
    ("station71", "PROCESS"): (28.0, 3.5, "down"),
    ("station71", "FAIL"): (28.0, 3.5, "down"),
    ("station71", "BLOCK"): (28.0, 3.5, "down"),
    ("station71", "UNLOAD"): (28.0, 2.5, "sx"),
    ("station71", "TRANSFER"): (29.0, 2.0, "sx"),
    ("corner1", "RETURN"): (30.0, 2.0, "sx"),
    ("corner1", "TRANSFER"): (26.0, 0.0, "dx"),
    ("splitter5", "FORWARD"): (26.0, 0.0, "sx"),
    ("splitter5", "RETURN"): (26.0, 1.5, "down"),
    ("splitter5", "TRANSFER"): (26.0, 0.0, "sx"),
    ("splitter5", "CHECKOUT"): (26.0, 0.0, "sx"),
    ("splitter5", "FINISH"): (26.0, 0.0, "sx"),
    ("splitter5", "SCRAP"): (26.0, 0.0, "sx"),
    ("splitter4", "FORWARD"): (14.0, 0.0, "sx"),
    ("splitter4", "RETURN"): (14.0, 1.5, "down"),
    ("splitter4", "TRANSFER"): (14.0, 0.0, "sx"),
    # SP2 @ x=4 bottom: FORWARD/TRANSFER 沿下轨向东（朝 M2），勿落到 C2 (0,0)
    ("splitter2", "FORWARD"): (6.0, 0.0, "sx"),
    ("splitter2", "RETURN"): (4.0, 1.5, "down"),
    ("splitter2", "TRANSFER"): (4.0, 0.0, "sx"),
}

NODE_FIXED_DIR: dict[tuple[float, float], str] = {}
for (_comp, _act), (x, y, d) in SEQ_MAP.items():
    key = (x, y)
    if key not in NODE_FIXED_DIR:
        NODE_FIXED_DIR[key] = d
    else:
        priority = {"down": 0, "up": 1, "sx": 2, "dx": 3}
        if priority.get(d, 9) < priority.get(NODE_FIXED_DIR[key], 9):
            NODE_FIXED_DIR[key] = d

MACH_Y = 3.5
BG = "#ffffff"
# ui_theme.STATUS_COLORS["IDLE"] — 与 monitoring 默认绿一致
MACHINE_MARKER_COLOR = "#22c55e"


def _short_node_label(name: str) -> str:
    t = str(name or "").strip()
    if not t:
        return t
    if t.startswith("Splitter "):
        return "SP{}".format(t.split(" ", 1)[1])
    if t.startswith("Merger "):
        return "MG{}".format(t.split(" ", 1)[1])
    if t.startswith("Corner "):
        return "C{}".format(t.split(" ", 1)[1])
    if t.startswith("Gate "):
        return "G{}".format(t.split(" ", 1)[1])
    return t


def get_sequence_info(component_id: object | None, activity: object | None):
    return SEQ_MAP.get(
        (str(component_id or "").strip(), str(activity or "").strip()),
        (None, None, None),
    )


def _fallback_sequence(component_id: object | None):
    """Neo4j 活动未在 SEQ_MAP 中时，落到工位竖线下方或首个已知点。"""
    cid = str(component_id or "").strip().lower()
    if not cid:
        return None, None, None
    for m in machines_conf:
        if m["id"] == cid:
            return float(m["x"]), MACH_Y, "sx"
    picks = [(x, y, d) for (c, _a), (x, y, d) in SEQ_MAP.items() if c == cid]
    if picks:
        return picks[0]
    return None, None, None


def _part_xy_dir(component_id: object | None, activity: object | None):
    tx, ty, d = get_sequence_info(component_id, activity)
    if tx is None:
        tx, ty, d = _fallback_sequence(component_id)
    if tx is None:
        return None, None, None
    nk = (tx, ty)
    fixed = NODE_FIXED_DIR.get(nk, d)
    return tx, ty, fixed


def _build_static_traces() -> list:
    traces = []
    RAIL = "#334155"
    N_BG = "#e2e8f0"
    N_BR = "#0284c7"
    RAIL_TOP = 2.0
    RAIL_BOT = 0.0
    CONN_START = 2.0
    CONN_END = 3.5

    for xs, ys in [
        ([-0.04, 30.04], [RAIL_TOP, RAIL_TOP]),
        ([30.04, 0.0], [RAIL_BOT, RAIL_BOT]),
        ([0.0, 0.0], [RAIL_BOT, RAIL_TOP]),
        ([30.0, 30.0], [RAIL_TOP, RAIL_BOT]),
    ]:
        traces.append(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=RAIL, width=3),
                showlegend=False,
                hoverinfo="skip",
            )
        )
    for vx in [4, 10, 14, 22, 26]:
        traces.append(
            go.Scatter(
                x=[vx, vx],
                y=[RAIL_BOT, RAIL_TOP],
                mode="lines",
                line=dict(color=RAIL, width=3),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    for m in machines_conf:
        traces.append(
            go.Scatter(
                x=[m["x"], m["x"]],
                y=[CONN_START, CONN_END],
                mode="lines",
                line=dict(color="#94a3b8", width=1.75),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    def _arrow(x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        angle = math.degrees(math.atan2(dy, dx))
        return go.Scatter(
            x=[x0, x1],
            y=[y0, y1],
            mode="lines+markers",
            line=dict(color=RAIL, width=1.75),
            marker=dict(
                symbol="arrow",
                size=10,
                angle=angle - 90,
                color=RAIL,
                line=dict(color=RAIL, width=1),
            ),
            showlegend=False,
            hoverinfo="skip",
        )

    top_nodes_x = sorted(
        set(
            [0]
            + [m["x"] for m in machines_conf]
            + [n["x"] for n in draw_mergers if n["y"] == RAIL_TOP]
            + [n["x"] for n in draw_splitters if n["y"] == RAIL_TOP]
            + [30]
        )
    )
    for i in range(len(top_nodes_x) - 1):
        mid = (top_nodes_x[i] + top_nodes_x[i + 1]) / 2.0
        traces.append(_arrow(mid + 0.01, RAIL_TOP, mid - 0.01, RAIL_TOP))

    bot_nodes_x = sorted(
        set(
            [0]
            + [n["x"] for n in draw_mergers if n["y"] == RAIL_BOT]
            + [n["x"] for n in draw_splitters if n["y"] == RAIL_BOT]
            + [30]
        )
    )
    for i in range(len(bot_nodes_x) - 1):
        mid = (bot_nodes_x[i] + bot_nodes_x[i + 1]) / 2.0
        traces.append(_arrow(mid - 0.01, RAIL_BOT, mid + 0.01, RAIL_BOT))

    for vx in [10, 22, 30]:
        traces.append(_arrow(vx, 1.0 + 0.01, vx, 1.0 - 0.01))
    for vx in [0, 4, 14, 26]:
        traces.append(_arrow(vx, 1.0 - 0.01, vx, 1.0 + 0.01))

    ms_data = pd.DataFrame(draw_mergers + draw_splitters)
    top_list = ["Merger 1", "Merger 3", "Merger 5", "Splitter 1", "Splitter 3"]
    ms_pos = ["top center" if n in top_list else "bottom center" for n in ms_data["name"]]
    traces.append(
        go.Scatter(
            x=ms_data["x"],
            y=ms_data["y"],
            mode="markers+text",
            marker=dict(size=22, color=N_BG, line=dict(color=N_BR, width=2)),
            text=[_short_node_label(x) for x in ms_data["name"]],
            textposition=ms_pos,
            textfont=dict(size=15, color="#334155"),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    df_gates = pd.DataFrame(gates_draw)
    gate_ys = [RAIL_TOP if x > 0 else RAIL_BOT for x in df_gates["x"]]
    traces.append(
        go.Scatter(
            x=df_gates["x"],
            y=gate_ys,
            mode="markers+text",
            marker=dict(size=20, color=N_BG, line=dict(color=N_BR, width=2)),
            text=[_short_node_label(x) for x in df_gates["name"]],
            textposition="bottom center",
            textfont=dict(size=15, color="#334155"),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    return traces


_STATIC_TRACES_CACHE: list | None = None
_MACHINE_LABEL_TRACES_CACHE: list | None = None
_FIGURE_LAYOUT_CACHE: dict | None = None


def _machine_label_traces() -> list:
    out: list = []
    for m in machines_conf:
        out.append(
            go.Scatter(
                x=[m["x"]],
                y=[MACH_Y],
                mode="markers+text",
                marker=dict(
                    symbol="square",
                    size=46,
                    color=MACHINE_MARKER_COLOR,
                    line=dict(color="#000000", width=2),
                ),
                text=[f"<b>{m['name']}</b>"],
                textposition="top center",
                textfont=dict(size=18, color="#1e293b"),
                showlegend=False,
                hoverinfo="skip",
            )
        )
    return out


def _figure_layout(*, height: int) -> dict:
    return dict(
        xaxis=dict(visible=False, range=[-1, 31]),
        yaxis=dict(visible=False, range=[-0.9, 5.0]),
        height=height,
        plot_bgcolor=BG,
        paper_bgcolor=BG,
        margin=dict(t=10, b=10, l=10, r=10),
        showlegend=False,
    )


def _static_figure_data() -> list:
    global _STATIC_TRACES_CACHE, _MACHINE_LABEL_TRACES_CACHE
    if _STATIC_TRACES_CACHE is None:
        _STATIC_TRACES_CACHE = list(_build_static_traces())
    if _MACHINE_LABEL_TRACES_CACHE is None:
        _MACHINE_LABEL_TRACES_CACHE = _machine_label_traces()
    return list(_STATIC_TRACES_CACHE) + list(_MACHINE_LABEL_TRACES_CACHE)


def part_markers_from_parts_last_step(parts: list[dict]) -> list[dict]:
    """与 ``code/monitoring.process_event_state`` 一致：用 **每条 part 最后一条工步** 的 ``(component_id, activity)`` 在 ``SEQ_MAP`` 上标位。

    这样分流器/角落/道岔上的运输过程也会显示；仅用 KPI ``station_live`` 会漏掉「已离开工位、尚未到下一站」的零件。
    """
    out: list[dict] = []
    for p in parts or []:
        steps = p.get("steps") or []
        if not steps:
            continue
        le = steps[-1]
        pid = str(p.get("part_id") or "").strip()
        if not pid:
            continue
        out.append(
            {
                "part_id": pid,
                "component_id": str(le.get("component_id") or "").strip().lower(),
                "activity": str(le.get("activity") or "TRANSFER").strip(),
            }
        )
    return out


def part_markers_from_kpi_station_live(kpi: dict) -> list[dict]:
    """无 Neo4j 全量路径时的回退：仅 9 个工位上的 ``current_part_id``，运输途中可能为空（不要用做主路径）。

    避免 ``get_session_parts_latest_locations`` 读图里「全会话最后一条」导致全程停在终态的问题。
    """
    sl = kpi.get("station_live")
    if not isinstance(sl, dict) or not sl:
        return []
    # 与 kpi 中状态机到 SEQ_MAP 活动的大致对应（仅用于选坐标）
    _st_to_act = {
        "BUSY": "PROCESS",
        "BLOCKED": "BLOCK",
        "FAIL": "FAIL",
        "IDLE": "TRANSFER",
    }
    out: list[dict] = []
    for comp_id, row in sl.items():
        if not isinstance(row, dict):
            continue
        pid = str(row.get("current_part_id") or "").strip()
        if not pid:
            continue
        stt = str(row.get("current_state") or "IDLE").upper()
        act = _st_to_act.get(stt, "TRANSFER")
        out.append(
            {
                "part_id": pid,
                "component_id": str(comp_id or "").strip().lower(),
                "activity": act,
            }
        )
    return out


def _pallet_positions(part_markers: list[dict]) -> list[dict]:
    """同节点多 Part 时显示 ``+N``，避免文本堆叠。"""
    groups: dict[tuple[float, float, str], list[dict]] = defaultdict(list)
    for m in part_markers:
        qx, qy, d = _part_xy_dir(m.get("component_id"), m.get("activity"))
        if qx is None:
            continue
        groups[(round(qx, 5), round(qy, 5), d)].append(m)

    display_pallets: list[dict] = []
    for (qx, qy, d), items in groups.items():
        items = sorted(items, key=lambda x: str(x.get("part_id") or ""))
        acts = [str(m.get("activity") or "").strip().upper() for m in items]
        all_empty = all(a in ("FINISH", "SCRAP") for a in acts)
        color = "#b8956a" if all_empty else "#7a4f2a"
        ids = [str(m.get("part_id") or "").strip() for m in items if str(m.get("part_id") or "").strip()]
        text = ids[0] if len(ids) <= 1 else "+{}".format(len(ids))
        hover = ", ".join(ids) if ids else text
        display_pallets.append(
            {"x": qx, "y": qy, "id": text, "hover": hover, "color": color}
        )
    return display_pallets


def build_factory_floor_figure(
    *,
    part_markers: list[dict] | None = None,
    sim_state: dict | None = None,
    height: int = 440,
) -> go.Figure:
    fig = go.Figure(data=_static_figure_data())

    if sim_state is not None:
        import twin.factory_floor_sim as factory_floor_sim

        pallets = factory_floor_sim.display_pallets_from_sim(sim_state)
    else:
        pallets = _pallet_positions(part_markers or [])
    if pallets:
        df_p = pd.DataFrame(pallets)
        fig.add_trace(
            go.Scatter(
                x=df_p["x"],
                y=df_p["y"],
                mode="markers+text",
                marker=dict(
                    symbol="circle",
                    size=26,
                    color=df_p["color"],
                    line=dict(color="#ffffff", width=1),
                ),
                text=df_p["id"],
                textposition="middle center",
                textfont=dict(size=10, color="white"),
                customdata=df_p["hover"],
                hovertemplate="<b>%{customdata}</b><extra></extra>",
                showlegend=False,
            )
        )

    global _FIGURE_LAYOUT_CACHE
    if _FIGURE_LAYOUT_CACHE is None or _FIGURE_LAYOUT_CACHE.get("height") != height:
        _FIGURE_LAYOUT_CACHE = _figure_layout(height=height)
    fig.update_layout(**_FIGURE_LAYOUT_CACHE)
    return fig

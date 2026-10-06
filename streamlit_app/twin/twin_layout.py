"""
装配线 Digital Twin 平面图：与现场 event_log 的 component_id 一致（station* / splitter* / corner*）。
垂直连通顶轨与底轨，与现场拓扑一致（合流点不单独绘制）。

SVG 内不放长标题/脚注，避免在 Streamlit 中与节点标签视觉重叠。
"""
from __future__ import annotations

import html
import math
from collections import defaultdict
from typing import Iterable

LOGGED_COMPONENT_IDS: frozenset[str] = frozenset(
    [
        "corner1",
        "corner2",
        "splitter1",
        "splitter2",
        "splitter3",
        "splitter4",
        "splitter5",
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
)

# Part 标记位置：与日志 ``component_id`` 一致，锚定该组件在图上的中心（``part_marker_position``）。
# 不再使用轨段中点；corner2 上 START/RETURN/TRANSFER 均在 C2；工站 UNLOAD/TRANSFER 也在该工站节点上。
POSITION_MAP: dict[str, str] = {
    "corner2": "node_C2",
    "station11": "node_st11",
    "station21": "node_st21",
    "station22": "node_st22",
    "station31": "node_st31",
    "station41": "node_st41",
    "station51": "node_st51",
    "station52": "node_st52",
    "station61": "node_st61",
    "station71": "node_st71",
    "corner1": "node_C1",
    "splitter1": "edge_st22_st31",
    "splitter2": "edge_C2_bottom",
    "splitter3": "edge_st52_st61",
    "splitter4": "edge_S4_bottom",
    "splitter5": "edge_C1_S5out",
}

_NODE_PLACE_TO_COMPONENT: dict[str, str] = {
    "node_C2": "corner2",
    "node_st11": "station11",
    "node_st21": "station21",
    "node_st22": "station22",
    "node_st31": "station31",
    "node_st41": "station41",
    "node_st51": "station51",
    "node_st52": "station52",
    "node_st61": "station61",
    "node_st71": "station71",
    "node_C1": "corner1",
}

# 工站 UNLOAD/TRANSFER 时仍用略深的填充色区分「离开工位」观感（位置仍在工站中心）。
_STATION_TRANSIT_ACTIVITIES: frozenset[str] = frozenset({"TRANSFER", "UNLOAD"})

# Part Track / 教学 Expected process model：工艺进度一格 = 物理上一道主工序。
# 现场是「单工站、双轨位」时，event_log 仍用两个 component_id（便于区分上下线）：
#   · station21 + station22 → 物理同一 **第 2 道工站**（2-1 / 2-2 轨）
#   · station51 + station52 → 物理同一 **第 5 道工站**（5-1 / 5-2 轨）
# 例如 p3：station21 PASS→TRANSFER→station22 LOAD，仍在同一 ST21/22 格内流转，不视为多道工序。
# corner/splitter 不参与工序列；下层回流等 **物理回路** 不单独构成 Rework，Rework 只看 **stage 序是否回退**。
PROCESS_STAGE_ORDER: tuple[str, ...] = (
    "ST11",
    "ST21/22",
    "ST31",
    "ST41",
    "ST51",
    "ST61",
    "ST71",
)

_STATION_TO_STAGE: dict[str, str] = {
    "station11": "ST11",
    "station21": "ST21/22",
    "station22": "ST21/22",
    "station31": "ST31",
    "station41": "ST41",
    "station51": "ST51",
    "station52": "ST51",
    "station61": "ST61",
    "station71": "ST71",
}


def component_to_process_stage(component_id: str) -> str | None:
    """日志里的 station* → 工艺工序格（21/22、51/52 为同一物理工站的双位 ID）；非加工节点返回 None。"""
    return _STATION_TO_STAGE.get((component_id or "").strip())


def stage_entry_sequence(steps: list[dict]) -> list[str]:
    """每次进入新工序段记一条（同格内连续事件合并），含 LOAD/UNLOAD 等 — **物流 + 工艺** 混合序列。"""
    out: list[str] = []
    prev: str | None = None
    for s in steps:
        st = component_to_process_stage(str(s.get("component_id") or ""))
        if not st:
            continue
        if st != prev:
            out.append(st)
        prev = st
    return out


# 仅贡献「工艺进度」的 activity：用于 rollback / process_stage_path / 矩阵 ↺（排除物流噪音）。
# Assumption（答辩需说明）: 每个实际占用主线格的事件链中至少会出现 PROCESS 或 PASS；否则纯
# LOAD→UNLOAD 的工站不会进入干净序列，极端情况下 rollback 可能偏差。
STAGE_SEQUENCE_ACTIVITIES: frozenset[str] = frozenset({"PROCESS", "PASS"})


def stage_entry_sequence_clean(steps: list[dict]) -> list[str]:
    """从 **PROCESS / PASS** 推导主线工序序；连续同一 stage 合并。

    供 ``has_stage_rework_loop``、FINISH 分段与 Conformance 展示；不把 TRANSFER、LOAD、
    UNLOAD、RETURN 等记入阶段跳转，避免假 rollback。"""
    out: list[str] = []
    last: str | None = None
    for s in steps:
        act = str(s.get("activity") or "").strip().upper()
        if act not in STAGE_SEQUENCE_ACTIVITIES:
            continue
        st = component_to_process_stage(str(s.get("component_id") or ""))
        if not st:
            continue
        if st != last:
            out.append(st)
            last = st
    return out

VIEW_W = 1560

# 与 build_twin_svg / component_center 共用的几何（单源）
Y_STAGE_TOP = 6.0
Y_STAGE_BOT = 50.0
TOP_Y = 120.0
# 底轨 y：原 ~352 时顶轨–底轨竖段过长，iframe 内易裁切；上移底轨缩短竖线、压低 viewBox 高度，拓扑不变。
BOTTOM_CORNER_Y = 268.0
BOT_Y = BOTTOM_CORNER_Y + 12.0  # 280
ST_Y = TOP_Y - 44.0  # 76，工站行中心 y
STATION_W = 80.0
STATION_H = 50.0
STATION_RX = 10.0
CORNER_SIZE = 48.0
CORNER_RX = 10.0
SPLIT_RX = 16.0
SPLIT_RY = 18.0
# viewBox 上下留白（不对称：上略留以容纳 Stage 字，下收紧减少图内「空黑」）
CANVAS_PAD_TOP = 22.0
CANVAS_PAD_BOTTOM = 12.0

# Layout 顶栏：与工艺 ST11…ST71 及现场阶段划分一致（x 与 twin 节点对齐）
# (x0, x1, 标题, 副标题含 ST 代号与 Looping / Non-Looping)
LAYOUT_STAGE_BANDS: tuple[tuple[float, float, str, str], ...] = (
    (92.0, 255.0, "Stage 1", "ST11 · Non-Looping"),
    (255.0, 557.0, "Stage 2", "ST21/22 · Looping"),
    (557.0, 685.0, "Stage 3", "ST31 · Non-Looping"),
    (685.0, 1162.0, "Stage 4", "ST41/51/52 · Looping"),
    (1162.0, 1293.0, "Stage 5", "ST61 · Non-Looping"),
    (1293.0, 1520.0, "Stage 6", "ST71 · QC · Looping"),
)

# Stage 1,3,5 蓝；2,4 紫；6金（QC）
_STAGE_TOP_BORDER_COLORS: tuple[str, ...] = (
    "#4a6fa5",
    "#6a4a8a",
    "#4a6fa5",
    "#6a4a8a",
    "#4a6fa5",
    "#8a6a3a",
)

_STAGE_HEADER_DIVIDERS_X: tuple[float, ...] = (255.0, 557.0, 685.0, 1162.0, 1293.0)

_TRACK_STROKE = "#3a4a6a"
_TRACK_STROKE_W = 1.5


def _twin_content_bbox() -> tuple[float, float, float, float]:
    """内容轴对齐包围盒（不含画布 padding）。"""
    min_y = Y_STAGE_TOP
    max_y = max(
        Y_STAGE_BOT,
        ST_Y + STATION_H / 2.0,
        BOT_Y + SPLIT_RY,
        BOT_Y + CORNER_SIZE / 2.0,
        TOP_Y + CORNER_SIZE / 2.0,
    )
    min_x = 0.0
    max_x = float(VIEW_W)
    return (min_x, min_y, max_x, max_y)


def twin_svg_view_box() -> tuple[float, float, float, float]:
    """viewBox：宽度全幅，高度 = 内容高 + CANVAS_PAD_TOP + CANVAS_PAD_BOTTOM。"""
    _mnx, min_y, _mxx, max_y = _twin_content_bbox()
    h = (max_y - min_y) + CANVAS_PAD_TOP + CANVAS_PAD_BOTTOM
    return (0.0, min_y - CANVAS_PAD_TOP, float(VIEW_W), h)


def _layout_stage_overlay_svg() -> str:
    """工位行上方的 Stage 分区带（与 ``LAYOUT_STAGE_BANDS`` 一致）。"""
    y_band_top = Y_STAGE_TOP
    y_band_bot = Y_STAGE_BOT
    chunks: list[str] = ['<g id="twin-stage-bands" pointer-events="none">']
    for i, (x0, x1, title, sub) in enumerate(LAYOUT_STAGE_BANDS):
        w = max(4.0, x1 - x0)
        cx = (x0 + x1) / 2.0
        accent = _STAGE_TOP_BORDER_COLORS[i % len(_STAGE_TOP_BORDER_COLORS)]
        h = y_band_bot - y_band_top
        chunks.append(
            '<rect x="{:.1f}" y="{:.0f}" width="{:.1f}" height="{:.0f}" rx="4" '
            'fill="#0d1117"/>'.format(x0, y_band_top, w, h)
        )
        chunks.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" '
            'stroke="{}" stroke-width="2" stroke-linecap="butt"/>'.format(
                x0, y_band_top, x1, y_band_top, accent
            )
        )
        chunks.append(
            '<text x="{:.1f}" y="{:.0f}" fill="#e0e6f0" font-size="13" font-weight="600" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{}</text>'.format(
                cx, y_band_top + 17.0, _esc(title)
            )
        )
        chunks.append(
            '<text x="{:.1f}" y="{:.0f}" fill="#7a9bc0" font-size="10" font-weight="400" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{}</text>'.format(
                cx, y_band_top + 32.0, _esc(sub)
            )
        )
    for bx in _STAGE_HEADER_DIVIDERS_X:
        chunks.append(
            '<line x1="{:.1f}" y1="{:.0f}" x2="{:.1f}" y2="{:.0f}" '
            'stroke="#1e2a3a" stroke-width="1" stroke-opacity="0.6"/>'.format(
                bx, y_band_top, bx, y_band_bot + 1.0
            )
        )
    chunks.append("</g>")
    return "".join(chunks)


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _layout_metrics():
    """与 build_twin_svg 绘图坐标完全一致（供高亮圆心查询）。"""
    return {
        "top_y": TOP_Y,
        "bot_y": BOT_Y,
        "st_y": ST_Y,
        "bottom_corner_y": BOTTOM_CORNER_Y,
    }


def part_marker_position(
    component_id: str | None, activity: str | None
) -> tuple[float, float] | None:
    """Anchor the marker on the twin **node** for this event’s ``component_id`` (log-aligned).

    Corners, stations, and splitters all use ``component_center`` — no segment midpoints
    (e.g. corner2 START/TRANSFER and station UNLOAD stay on that component’s icon).
    ``activity`` is ignored for positioning (kept for call-site compatibility).
    """
    _ = activity
    cid = (component_id or "").strip().lower()
    if not cid:
        return None
    return component_center(cid)


def component_center(component_id: str | None) -> tuple[float, float] | None:
    """返回 twin 图上用于高亮脉冲的锚点；未知节点返回 None。"""
    if not component_id:
        return None
    cid = component_id.strip().lower()
    g = _layout_metrics()
    top_y, bot_y, sy = g["top_y"], g["bot_y"], g["st_y"]
    # 上下轨成对共线：垂直连通处顶轨与底轨 splitter 对齐（同 x）
    xm1, xs1, xm3, xs3, xm5 = 255.0, 515.0, 685.0, 1115.0, 1295.0
    centers: dict[str, tuple[float, float]] = {
        "corner2": (100.0, float(bot_y)),
        "station11": (175.0, sy),
        "station21": (335.0, sy),
        "station22": (425.0, sy),
        "splitter1": (xs1, float(top_y)),
        "station31": (600.0, sy),
        "station41": (770.0, sy),
        "station51": (885.0, sy),
        "station52": (1000.0, sy),
        "splitter3": (xs3, float(top_y)),
        "station61": (1210.0, sy),
        "station71": (1375.0, sy),
        "corner1": (1460.0, float(top_y)),
        "splitter5": (xm5, float(bot_y)),
        "splitter4": (xm3, float(bot_y)),
        "splitter2": (xm1, float(bot_y)),
    }
    return centers.get(cid)


_STATION_PREFIX = "station"


def _part_marker_fill(m: dict) -> tuple[str, float]:
    """在工站停驻：站主题蓝；在途/轨上/非站物流：深 slate，避免高亮白刺眼、与深色底对比柔和。"""
    cid = (m.get("component_id") or "").strip().lower()
    act_u = str(m.get("activity") or "").strip().upper()
    if cid.startswith(_STATION_PREFIX) and act_u in _STATION_TRANSIT_ACTIVITIES:
        return "#475569", 1.0
    if cid.startswith(_STATION_PREFIX):
        return "#4a6fa5", 1.0
    return "#475569", 1.0


def _part_marker_stroke_and_label_fill(fill_hex: str) -> tuple[str, str]:
    """圆环描边与标签填色：蓝站用浅描边；slate 在途用冷灰描边 + 浅灰字，可读且不刺眼。"""
    if fill_hex == "#4a6fa5":
        return "#bfdbfe", "#f1f5f9"
    return "#94a3b8", "#e2e8f0"


def _part_marker_label(part_id: str) -> str:
    """Short label for map (full id unless very long)."""
    s = (part_id or "").strip()
    if not s:
        return "?"
    if len(s) > 14:
        return s[:12] + "\u2026"
    return s


# 同坐标零件数 ≥ 此值时合并为「+n」聚合点（tooltip 仍列全部）
_PART_MARKER_CLUSTER_MIN = 4
_PART_RING_BASE = 13.0
_PART_RING_SCALE = 3.6


def _part_markers_svg(markers: list[dict]) -> str:
    """多 Part 位置：锚在工站/角点/分流器节点中心；重合处环形散开；密集时聚合为 +n。"""
    if not markers:
        return ""
    grouped: dict[tuple[int, int], list[tuple[int, dict]]] = defaultdict(list)
    for i, m in enumerate(markers):
        base = part_marker_position(m.get("component_id"), m.get("activity"))
        if not base:
            continue
        key = (int(round(base[0])), int(round(base[1])))
        grouped[key].append((i, m))
    chunks: list[str] = []
    for _key, items in grouped.items():
        m0 = items[0][1]
        base = part_marker_position(m0.get("component_id"), m0.get("activity"))
        if not base:
            continue
        bx, by = base[0], base[1]
        n = len(items)
        if n >= _PART_MARKER_CLUSTER_MIN:
            tip_lines: list[str] = []
            for _j, (_idx, m) in enumerate(items):
                tip_cid = (m.get("component_id") or "").strip()
                tip_lines.append(
                    "{} @ {} · {} · {}".format(
                        m.get("part_id") or "",
                        tip_cid,
                        m.get("activity") or "",
                        m.get("time") or "",
                    )
                )
            tip_all = "\n".join(tip_lines)
            lab = _esc("+{}".format(n))
            chunks.append(
                '<g class="twin-part-marker twin-part-cluster" pointer-events="all">'
                "<title>{}</title>"
                '<circle cx="{:.1f}" cy="{:.1f}" r="11.0" fill="#334155" fill-opacity="0.96" '
                'stroke="#e2e8f0" stroke-width="1.5" stroke-opacity="0.9"/>'
                '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" dominant-baseline="central" '
                'font-size="10.5" font-weight="700" '
                'font-family="Segoe UI, system-ui, sans-serif" '
                'fill="#f8fafc" stroke="#0a0e1a" stroke-width="0.55" paint-order="stroke fill">'
                "{}</text>"
                "</g>".format(
                    _esc(tip_all),
                    bx,
                    by,
                    bx,
                    by,
                    lab,
                )
            )
            continue
        ring_r = max(_PART_RING_BASE, _PART_RING_BASE + _PART_RING_SCALE * max(0, n - 2))
        for j, (_idx, m) in enumerate(items):
            if n > 1:
                ang = 2 * math.pi * j / n
                dx, dy = ring_r * math.cos(ang), ring_r * math.sin(ang)
            else:
                dx, dy = 0.0, 0.0
            px, py = bx + dx, by + dy
            fill, op = _part_marker_fill(m)
            ring_stroke, lab_fill = _part_marker_stroke_and_label_fill(fill)
            tip_cid = (m.get("component_id") or "").strip()
            tip = "{} @ {} · {} · {}".format(
                m.get("part_id") or "",
                tip_cid,
                m.get("activity") or "",
                m.get("time") or "",
            )
            raw_lab = _part_marker_label(str(m.get("part_id") or ""))
            lab = _esc(raw_lab)
            fs = 8.0 if len(raw_lab) <= 4 else (7.0 if len(raw_lab) <= 7 else 6.0)
            chunks.append(
                '<g class="twin-part-marker" pointer-events="all">'
                "<title>{}</title>"
                '<circle cx="{:.1f}" cy="{:.1f}" r="7.0" fill="{}" fill-opacity="{:.2f}" '
                'stroke="{}" stroke-width="1.35" stroke-opacity="0.95"/>'
                '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" dominant-baseline="central" '
                'font-size="{}" font-weight="650" '
                'font-family="Segoe UI, system-ui, sans-serif" '
                'fill="{}" stroke="#0f172a" stroke-width="0.4" stroke-opacity="0.75" paint-order="stroke fill">'
                "{}</text>"
                "</g>".format(
                    _esc(tip),
                    px,
                    py,
                    fill,
                    op,
                    ring_stroke,
                    px,
                    py,
                    fs,
                    lab_fill,
                    lab,
                )
            )
    return "".join(chunks)


def build_twin_svg(
    *,
    highlight_id: str | None = None,
    part_markers: list[dict] | None = None,
) -> str:
    """纯拓扑图（无标题、无页脚长文），便于经 ``components.v1.html`` iframe 嵌入。

    ``part_markers`` 非空时绘制多 Part 实时位置；此时忽略 ``highlight_id`` 的轨道加粗，
    仍可为单 Part 模式保留 ``highlight_id`` 脉冲（不传 markers 即可）。
    """
    markers = part_markers or []
    use_multi = len(markers) > 0
    hl = "" if use_multi else (highlight_id or "").strip().lower()
    g = _layout_metrics()
    top_y, bot_y, st_y = g["top_y"], g["bot_y"], g["st_y"]
    vbx, vby, vbw, vbh = twin_svg_view_box()

    def hl_stroke(cid: str | None, base: str) -> str:
        if cid and hl and cid.lower() == hl:
            return "#f4d03f"
        return base

    def hl_sw(cid: str | None) -> str:
        return "2.4" if (cid and hl and cid.lower() == hl) else "1.5"

    ch = CORNER_SIZE / 2.0
    hw, hh = STATION_W / 2.0, STATION_H / 2.0

    loop = """
  <g id="twin-track-loop" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="butt" stroke-linejoin="miter">
    <path d="M 100 {ty} L 1460 {ty}"/>
    <path d="M 1460 {ty} L 1460 {by}"/>
    <path d="M 1460 {by} L 100 {by}" stroke-dasharray="6 3"/>
    <path d="M 100 {by} L 100 {ty}"/>
  </g>
""".format(
        ty=top_y,
        by=bot_y,
        stroke=_TRACK_STROKE,
        sw=_TRACK_STROKE_W,
    )

    def station(cid: str, label_ui: str, sub: str, x: float, y: float) -> str:
        s = hl_stroke(cid, "#4a6fa5")
        sw = hl_sw(cid)
        return (
            '<g id="twin-{cid}" class="twin-node twin-station" data-component="{cid}">'
            '<rect x="{x:.1f}" y="{y:.1f}" width="{w:.0f}" height="{h:.0f}" rx="{rx}" '
            'fill="#1e2a3a" stroke="{stroke}" stroke-width="{sw}"/>'
            '<text x="{cx:.1f}" y="{y1:.1f}" fill="#e0e6f0" font-size="14" font-weight="600" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{lab}</text>'
            '<text x="{cx:.1f}" y="{y2:.1f}" fill="#7a9bc0" font-size="11" font-weight="400" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{sub}</text></g>'
        ).format(
            cid=cid,
            x=x - hw,
            y=y - hh,
            w=STATION_W,
            h=STATION_H,
            rx=STATION_RX,
            cx=x,
            y1=y - 7,
            y2=y + 11,
            lab=_esc(label_ui),
            sub=_esc(sub),
            stroke=s,
            sw=sw,
        )

    def splitter(cid: str, n: str, x: float, y: float, *, io: bool = False) -> str:
        bf, bs, bt = (
            ("#2a3f2a", "#4a8a4a", "#7acc7a")
            if io
            else ("#2a1e3a", "#6a4a8a", "#aa88cc")
        )
        s = hl_stroke(cid, bs)
        sw = hl_sw(cid)
        ry, rx = SPLIT_RY, SPLIT_RX
        return (
            '<g id="twin-{cid}" class="twin-node twin-splitter" data-component="{cid}">'
            '<polygon points="{pts}" fill="{bf}" stroke="{stroke}" stroke-width="{sw}"/>'
            '<text x="{x:.1f}" y="{y:.1f}" fill="{bt}" font-size="11" font-weight="600" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{lab}</text></g>'
        ).format(
            cid=cid,
            pts="{},{} {},{} {},{} {},{}".format(
                x,
                y - ry,
                x + rx,
                y,
                x,
                y + ry,
                x - rx,
                y,
            ),
            bf=bf,
            stroke=s,
            sw=sw,
            bt=bt,
            x=x,
            y=y + 4,
            lab=_esc(n),
        )

    def corner(cid: str, lab: str, x: float, y: float, *, entry: bool = False) -> str:
        bf, bs, bt = (
            ("#2a3f2a", "#4a8a4a", "#7acc7a")
            if entry
            else ("#3a2a1a", "#8a6a3a", "#ccaa7a")
        )
        s = hl_stroke(cid, bs)
        sw = hl_sw(cid)
        return (
            '<g id="twin-{cid}" class="twin-node twin-corner" data-component="{cid}">'
            '<rect x="{x:.1f}" y="{y:.1f}" width="{sz:.0f}" height="{sz:.0f}" rx="{rx}" '
            'fill="{bf}" stroke="{stroke}" stroke-width="{sw}"/>'
            '<text x="{cx:.1f}" y="{ty:.1f}" fill="{bt}" font-size="11" font-weight="600" '
            'font-family="Segoe UI, system-ui, sans-serif" text-anchor="middle">{tlab}</text></g>'
        ).format(
            cid=cid,
            x=x - ch,
            y=y - ch,
            sz=CORNER_SIZE,
            rx=CORNER_RX,
            cx=x,
            ty=y + 4,
            tlab=_esc(lab),
            bf=bf,
            stroke=s,
            sw=sw,
            bt=bt,
        )

    nodes: list[str] = []
    nodes.append(corner("corner2", "C2-CheckIn", 100, bot_y, entry=True))
    nodes.append(station("station11", "st11", "1-1", 175, st_y))
    nodes.append(station("station21", "st21", "2-1", 335, st_y))
    nodes.append(station("station22", "st22", "2-2", 425, st_y))
    nodes.append(splitter("splitter1", "S1", 515, top_y))
    nodes.append(station("station31", "st31", "3-1", 600, st_y))
    nodes.append(station("station41", "st41", "4-1", 770, st_y))
    nodes.append(station("station51", "st51", "5-1", 885, st_y))
    nodes.append(station("station52", "st52", "5-2", 1000, st_y))
    nodes.append(splitter("splitter3", "S3", 1115, top_y))
    nodes.append(station("station61", "st61", "6-1", 1210, st_y))
    nodes.append(station("station71", "st71", "QC", 1375, st_y))
    nodes.append(corner("corner1", "C1", 1460, top_y, entry=False))
    x_m1, x_s1, x_m3, x_s3, x_m5 = 255.0, 515.0, 685.0, 1115.0, 1295.0
    nodes.append(splitter("splitter5", "S5-Checkout", x_m5, bot_y, io=True))
    nodes.append(splitter("splitter4", "S4", x_m3, bot_y))
    nodes.append(splitter("splitter2", "S2", x_m1, bot_y))

    spurs = """
  <g id="twin-track-spurs" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="butt" stroke-linejoin="miter">
    <path d="M {x_m1} {ty} L {x_m1} {by}"/>
    <path d="M {x_s1} {ty} L {x_s1} {by}"/>
    <path d="M {x_m3} {ty} L {x_m3} {by}"/>
    <path d="M {x_s3} {ty} L {x_s3} {by}"/>
    <path d="M {x_m5} {ty} L {x_m5} {by}"/>
  </g>
""".format(
        ty=top_y,
        by=bot_y,
        x_m1=x_m1,
        x_s1=x_s1,
        x_m3=x_m3,
        x_s3=x_s3,
        x_m5=x_m5,
        stroke=_TRACK_STROKE,
        sw=_TRACK_STROKE_W,
    )

    pulse = ""
    xy = component_center(hl)
    if xy and hl:
        px, py = xy
        pulse = (
            '<g id="twin-part-pulse" pointer-events="none">'
            '<circle cx="{:.1f}" cy="{:.1f}" r="12" fill="#f4d03f" fill-opacity="0.35">'
            '<animate attributeName="r" values="10;22;10" dur="1.8s" repeatCount="indefinite"/>'
            '<animate attributeName="fill-opacity" values="0.5;0.15;0.5" dur="1.8s" repeatCount="indefinite"/>'
            "</circle>"
            '<circle cx="{:.1f}" cy="{:.1f}" r="5" fill="#f9e79f" fill-opacity="0.9"/></g>'
        ).format(px, py, px, py)

    markers_svg = _part_markers_svg(markers) if use_multi else ""

    defs = """
  <defs>
    <style type="text/css"><![CDATA[
      .twin-node text { user-select: none; }
      .twin-part-marker text { user-select: none; }
      .twin-part-marker circle { filter: drop-shadow(0 1px 2px rgba(0,0,0,0.75)); }
    ]]></style>
  </defs>
"""

    body = "".join(nodes)
    stages_svg = _layout_stage_overlay_svg()
    svg_style = "width:100%;height:auto;display:block;vertical-align:top;background:#0a0e1a"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vbx:.1f} {vby:.1f} {vbw:.1f} {vbh:.1f}" '
        'preserveAspectRatio="xMidYMid meet" style="{svg_style}">{defs}'
        '<rect x="{vbx:.1f}" y="{vby:.1f}" width="{vbw:.1f}" height="{vbh:.1f}" fill="#0a0e1a"/>'
        "{loop}{spurs}{stages}{body}{pulse}{markers}</svg>"
    ).format(
        vbx=vbx,
        vby=vby,
        vbw=vbw,
        vbh=vbh,
        svg_style=svg_style,
        defs=defs,
        loop=loop,
        spurs=spurs,
        stages=stages_svg,
        body=body,
        pulse=pulse,
        markers=markers_svg,
    )


def twin_diagram_height_px() -> int:
    """若仍用 ``components.v1.html`` 固定高度 iframe：按 viewBox 比例 × ``VIEW_W`` 估算（约等于图在1560px 宽时像素高）。

    Digital Twin 页已改为 ``st.markdown`` 内嵌 SVG，高度随内容自适应；本函数供其它嵌入场景备用。
    """
    _vb = twin_svg_view_box()
    ratio = _vb[3] / _vb[2] if _vb[2] else 0.35
    pad = 16
    return max(260, int(math.ceil(ratio * float(VIEW_W))) + pad)


def build_twin_html_document(svg_inner: str) -> str:
    return """<!DOCTYPE html><html><head><meta charset="utf-8"/>
<style>html,body{{margin:0;padding:0;background:#0a0e1a;height:auto;min-height:0;}}
body{{overflow:hidden;}}
.twin-svg-frame{{border-radius:12px;border:1px solid #1e2a3a;overflow:hidden;line-height:0;background:#0a0e1a;}}
svg{{max-width:100%;width:100%;height:auto;display:block;vertical-align:top;}}</style>
</head><body><div class="twin-svg-frame">{}</div></body></html>""".format(
        svg_inner,
    )


def component_ids_for_legend() -> Iterable[str]:
    return sorted(LOGGED_COMPONENT_IDS)

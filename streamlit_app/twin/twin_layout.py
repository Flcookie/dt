"""
Process-stage model shared by Part Track (plus the logged component list its views use).

Maps the line's logged ``component_id`` values (station* / splitter* / corner*) onto the
mainline process stages. The SVG floor drawing that used to live here was replaced by
``twin.factory_floor_plotly``.
"""
from __future__ import annotations

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

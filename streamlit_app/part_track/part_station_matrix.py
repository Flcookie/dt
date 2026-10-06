"""Part × 工站进度矩阵：✔ 曾到达、⏳ 最后事件所在工站。"""
from __future__ import annotations

from collections.abc import Iterable

import twin.twin_layout as twin_layout


def mainline_track_columns() -> list[str]:
    """工艺对齐工序列（Expected path：ST11→…→ST71，并行段合并）。"""
    return list(twin_layout.PROCESS_STAGE_ORDER)


def progress_anchor_component_id(steps: list[dict], columns: list[str]) -> str | None:
    """当前主线工序格；最后事件在搬运节点上时取最近一次加工工序。"""
    if not steps or not columns:
        return None
    colset = frozenset(columns)
    for s in reversed(steps):
        st = twin_layout.component_to_process_stage(str(s.get("component_id") or ""))
        if st and st in colset:
            return st
    return None


def ordered_station_columns(
    parts_with_steps: list[tuple[str, list[dict]]],
    preferred_order: Iterable[str],
) -> list[str]:
    """列顺序：先按 ``preferred_order`` 中出现在数据里的工站，其余按字母序追加。"""
    seen: set[str] = set()
    for _pid, steps in parts_with_steps:
        for s in steps:
            c = str(s.get("component_id") or "").strip()
            if c:
                seen.add(c)
    pref_list = list(preferred_order)
    primary = [c for c in pref_list if c in seen]
    rest = sorted(seen - set(primary))
    return primary + rest


def cells_for_steps(steps: list[dict], columns: list[str]) -> dict[str, str]:
    """兼容旧接口：等价于 ``cells_progress_matrix``。"""
    return cells_progress_matrix(steps, columns)


def cells_progress_matrix(steps: list[dict], columns: list[str]) -> dict[str, str]:
    """✓ 已过该工序格 · ○ 当前锚点工序 · ↺ 工序格重复进入 · ✕ Scrap/FAIL 失效锚点（仅当末事件为 SCRAP 或 FAIL）。"""
    episodes = twin_layout.stage_entry_sequence_clean(steps)
    out: dict[str, str] = {}
    last_act = str(steps[-1].get("activity") or "").strip().upper() if steps else ""
    colset = frozenset(columns)
    o_col = progress_anchor_component_id(steps, columns)
    scrap_col: str | None = None
    if last_act == "SCRAP":
        cr = twin_layout.component_to_process_stage(
            str(steps[-1].get("component_id") or "")
        )
        scrap_col = cr if cr and cr in colset else o_col
    elif last_act == "FAIL":
        cr = twin_layout.component_to_process_stage(
            str(steps[-1].get("component_id") or "")
        )
        scrap_col = cr if cr and cr in colset else o_col
    for col in columns:
        if scrap_col and col == scrap_col:
            out[col] = "✕"
            continue
        visit_n = sum(1 for x in episodes if x == col)
        if visit_n == 0:
            out[col] = ""
            continue
        if o_col and col == o_col:
            out[col] = "○"
            continue
        if visit_n >= 2:
            out[col] = "↺"
        else:
            out[col] = "✓"
    return out


def mainline_progress_ratio(columns: list[str], current_cid: str | None) -> float:
    """相对「列顺序」的位置（0–1），用于进度条示意；未知工站返回 0。"""
    if not columns or not (current_cid or "").strip():
        return 0.0
    cid = current_cid.strip()
    if cid not in columns:
        return 0.0
    return (columns.index(cid) + 1) / float(len(columns))


def mainline_progress_ratio_for_steps(
    steps: list[dict], columns: list[str] | None = None
) -> float:
    """按主线列与路径推断锚点后的进度比例（最后事件在 corner/splitter 时仍能反映已到的加工站）。"""
    cols = columns if columns is not None else mainline_track_columns()
    anchor = progress_anchor_component_id(steps, cols)
    return mainline_progress_ratio(cols, anchor)

"""Part Track 概览行：事实层字段 + Conformance 规则层标签回显。"""
from __future__ import annotations

import datetime
import re
from typing import Any

import part_track.flow_classification as flow_classification
import part_track.part_station_matrix as part_station_matrix
import twin.twin_layout as twin_layout


def _step_epoch_seconds(step: dict) -> float | None:
    """Best-effort time for cycle duration (CSV ``time`` ISO or Neo4j ``timestamp``)."""
    ts = step.get("timestamp")
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            pass
    t = step.get("time")
    if t:
        try:
            s = str(t).strip().replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s).timestamp()
        except (ValueError, TypeError, OSError):
            pass
    return None


def _fail_count_in_segment(seg: list[dict]) -> int:
    return sum(
        1
        for s in seg
        if str(s.get("activity") or "").strip().upper() == "FAIL"
    )


def segment_has_mainline_fail(seg: list[dict]) -> bool:
    """本 cycle 内是否出现过 **主线工站** 上的 FAIL（与 ``kpi_calculator._trace_has_mainline_fail`` 一致）。

    splitter/corner、非映射工位上的 FAIL **不算**质量 Rework；纯 ST2→ST3→ST2 / routing 回流无 FAIL 也为 False。
    """
    for s in seg:
        if str(s.get("activity") or "").strip().upper() != "FAIL":
            continue
        cid = str(s.get("component_id") or "").strip()
        if twin_layout.component_to_process_stage(cid) is not None:
            return True
    return False


def cycle_quality_label(
    seg: list[dict], *, info: dict[str, Any] | None = None
) -> str:
    """本圈 **质量层**：**Reworked** 仅 ``segment_has_mainline_fail``。

    **路径偏离**只靠 ``stage_rework_evidence(seg)``（事实段内工序回流），**不**用
    ``flow_classification.OUTCOME_REWORK`` / ``FAIL_OPEN``，避免流程分类污染质量标签。
    """
    if not seg:
        return "—"
    if any(str(s.get("activity") or "").strip().upper() == "SCRAP" for s in seg):
        return "⛔ Scrap"
    inf = info if info is not None else flow_classification.classify_flow_from_steps(seg)
    oc = str(inf.get("outcome") or "").strip()
    if oc == flow_classification.OUTCOME_SCRAP:
        return "⛔ Scrap"
    if segment_has_mainline_fail(seg):
        return "⚠️ Reworked"
    loop, _ = flow_classification.stage_rework_evidence(seg)
    if loop:
        return "⚠ Path deviation"
    return "✔ First pass"


def cycle_duration_display(seg: list[dict]) -> str:
    """Elapsed wall time for the lap; closed at FINISH shows total seconds, else running estimate."""
    if not seg:
        return "—"
    epochs = [x for x in (_step_epoch_seconds(s) for s in seg) if x is not None]
    if len(epochs) < 1:
        return "—"
    t0, t1 = epochs[0], epochs[-1]
    dt = max(0.0, t1 - t0)
    last_act = str(seg[-1].get("activity") or "").strip().upper()
    if last_act == "FINISH":
        return "{:.0f}s (closed)".format(dt)
    return "{:.0f}s (running)".format(dt)


def format_path_rework_visual(seg: list[dict]) -> str:
    """Physical-ish chain with FAIL❌ / FINISH✓ pins (consecutive duplicate stations collapsed)."""
    parts: list[str] = []
    prev_bare: str | None = None
    for s in seg:
        cid = str(s.get("component_id") or "").strip()
        if not cid:
            continue
        act = str(s.get("activity") or "").strip().upper()
        if act == "FAIL":
            parts.append("{}❌".format(cid))
            prev_bare = None
            continue
        if act == "FINISH":
            parts.append("FINISH✓")
            prev_bare = "__finish__"
            continue
        if cid == prev_bare:
            continue
        parts.append(cid)
        prev_bare = cid
    if not parts:
        return "—"
    return " → ".join(parts)


def production_cycle_identity(life: list[dict]) -> tuple[int, int, bool]:
    """(current_cycle_index_1based, n_laps_with_finish, last_lap_is_open)."""
    segs = flow_classification._finish_delimited_segments(life)
    if not segs:
        return (0, 0, False)
    n = len(segs)
    n_fin = sum(
        1
        for s in segs
        if s and str(s[-1].get("activity") or "").strip().upper() == "FINISH"
    )
    last_open = not (
        segs[-1] and str(segs[-1][-1].get("activity") or "").strip().upper() == "FINISH"
    )
    return (n, n_fin, last_open)


def conformance_label_en(outcome: str) -> str:
    """Vs process model: Flow = what happened; Conformance = expected or not."""
    return {
        flow_classification.OUTCOME_NORMAL: "Conformant",
        flow_classification.OUTCOME_REWORK: "Deviated (allowed)",
        flow_classification.OUTCOME_SCRAP: "Deviated",
        flow_classification.OUTCOME_OPEN: "Incomplete",
        flow_classification.OUTCOME_FAIL_OPEN: "Incomplete (error)",
    }.get(outcome or "", "—")


def production_cycle_segments(full_steps: list[dict]) -> list[list[dict]]:
    """FINISH-delimited production cycles (process-mining style segmentation). Same as ``_finish_delimited_segments``."""
    return flow_classification._finish_delimited_segments(full_steps)


def production_cycle_column_label(life: list[dict]) -> str:
    """Readable cycle line: *current lap* and how many FINISH-closed laps (easy to say in review)."""
    cur, n_fin, last_open = production_cycle_identity(life)
    if cur < 1:
        return "—"
    parts = [
        "Cycle {}".format(cur),
        "{} completed".format(n_fin),
    ]
    if last_open:
        parts.append("in progress")
    return " · ".join(parts)


def cycle_rows_for_detail(
    life: list[dict], *, preferred_stations: frozenset[str]
) -> list[dict[str, Any]]:
    """Per-cycle rows for Streamlit detail: time, FPY type, FAILs, deviation, rework path."""
    segs = production_cycle_segments(life)
    rows: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        inf = flow_classification.classify_flow_from_steps(seg)
        last_act = str(seg[-1].get("activity") or "").strip().upper() if seg else ""
        closed = last_act == "FINISH"
        status = "✔ Finished" if closed else "⏳ Running"
        if last_act == "SCRAP":
            status = "⛔ Scrap"
        dev = " · ".join(inf.get("reasons") or []) or "—"
        t0, t1 = start_last_wallclock(seg)
        rows.append(
            {
                "Cycle": "C{}".format(i + 1),
                "Status": status,
                "Cycle time": cycle_duration_display(seg),
                "Cycle type": cycle_quality_label(seg, info=inf),
                "FAILs": str(_fail_count_in_segment(seg)),
                "Flow": flow_classification.flow_type_badge(inf),
                "Progress": mainline_progress_text(seg, preferred_stations),
                "Deviation": dev,
                "Path (rework view)": format_path_rework_visual(seg),
                "Start → end": "{} → {}".format(t0, t1),
                "Physical path": format_journey_highlight_current(
                    seg, trailing_note=""
                ),
            }
        )
    return rows


def _ui_cycle_steps(full_steps: list[dict]) -> list[dict]:
    """Events for **current process cycle** in the Part Track table.

    Prefer the open tail after the last FINISH (new lap). If the trace ends on FINISH and there
    is no trailing activity yet, use the last finish-delimited segment so the row still shows
    Finished / 7/7 instead of an empty slice.
    """
    tail = flow_classification._open_tail_after_last_finish(full_steps)
    if tail:
        return tail
    if not full_steps:
        return []
    segs = flow_classification._finish_delimited_segments(full_steps)
    return segs[-1] if segs else list(full_steps)


def classification_for_display_cycle(life: list[dict]) -> dict[str, Any]:
    """Flow / Conformance for the **current** lap (same slice as Part Track row).

    Only **FINISH** closes a cycle; a FAIL in an **earlier** lap does not change Flow for the
    current open lap.
    """
    return flow_classification.classify_flow_from_steps(_ui_cycle_steps(life))


def conformance_display_badge(outcome: str) -> str:
    """方案5：Conformance 列带简要符号。"""
    o = outcome or ""
    icon = {
        flow_classification.OUTCOME_NORMAL: "✅",
        flow_classification.OUTCOME_REWORK: "🔶",
        flow_classification.OUTCOME_SCRAP: "⛔",
        flow_classification.OUTCOME_OPEN: "⏳",
        flow_classification.OUTCOME_FAIL_OPEN: "⚠️",
    }.get(o, "—")
    return "{} {}".format(icon, conformance_label_en(o))


def mainline_progress_text(
    steps: list[dict], preferred: frozenset[str], part_id: str = ""
) -> str:
    """横向进度：``n/7 ▓▓▓░░░░`` — **Expected 工序格**（ST11…ST71，21/22 与 51/52 合并）；``preferred`` 仅 API 兼容。"""
    _ = (preferred, part_id)
    if not steps:
        return "—"
    cols = part_station_matrix.mainline_track_columns()
    anchor = part_station_matrix.progress_anchor_component_id(steps, cols)
    total = len(cols)
    if anchor is None:
        n = 0
    else:
        n = cols.index(anchor) + 1
    n = max(0, min(total, n))
    bar = "▓" * n + "░" * (total - n)
    return "{}/{} {}".format(n, total, bar)


def mainline_progress_labeled_for_current_cycle(
    steps: list[dict],
    preferred: frozenset[str],
    *,
    cycle_index: int,
    part_id: str = "",
) -> str:
    """Mainline bar prefixed with **C n** so progress reads as this lap, not whole part."""
    core = mainline_progress_text(steps, preferred, part_id=part_id)
    if core == "—":
        return core
    ci = max(1, cycle_index)
    return "C{} · {}".format(ci, core)


def current_station_display(steps: list[dict]) -> str:
    """语义当前工站：最后一条**工站锚定**事件（非 corner/splitter 上纯 TRANSFER/RETURN）。"""
    if not steps:
        return "—"
    last_act = str(steps[-1].get("activity") or "").strip().upper()
    last_c = str(steps[-1].get("component_id") or "").strip()
    if last_act == "FINISH":
        return "Finished"
    if last_act == "SCRAP":
        return "Scrap"
    _anchor_acts = frozenset(
        {"LOAD", "PROCESS", "UNLOAD", "PASS", "FAIL", "BLOCK"}
    )
    for s in reversed(steps):
        cid = str(s.get("component_id") or "").strip()
        if not twin_layout.component_to_process_stage(cid):
            continue
        act = str(s.get("activity") or "").strip().upper()
        if act in _anchor_acts:
            return cid or "—"
    return last_c or "—"


def current_status_display(steps: list[dict], info: dict[str, Any]) -> str:
    """由最后事件 + Flow 结局推导展示状态（事件推断）。"""
    if not steps:
        return "—"
    last_act = str(steps[-1].get("activity") or "").strip().upper()
    outcome = info.get("outcome") or ""
    if last_act == "FINISH":
        if (
            outcome == flow_classification.OUTCOME_REWORK
            and segment_has_mainline_fail(steps)
        ):
            return "Finished (Reworked)"
        return "Finished"
    if last_act == "SCRAP":
        return "Scrapped"
    if outcome == flow_classification.OUTCOME_FAIL_OPEN or last_act == "FAIL":
        return "FAIL"
    if outcome == flow_classification.OUTCOME_REWORK:
        if segment_has_mainline_fail(steps):
            return "Reworking"
        return "Processing"
    if last_act == "BLOCK":
        return "Waiting"
    if outcome == flow_classification.OUTCOME_OPEN and last_act not in (
        "FINISH",
        "SCRAP",
        "FAIL",
    ):
        _cols = part_station_matrix.mainline_track_columns()
        _anchor = part_station_matrix.progress_anchor_component_id(steps, _cols)
        if _anchor and _cols and _anchor == _cols[-1]:
            return "Awaiting FINISH"
    if last_act in ("PROCESS", "LOAD", "UNLOAD", "START", "TRANSFER"):
        return "Processing"
    return "Processing"


def passed_stations_summary(steps: list[dict]) -> str:
    cids = [
        str(s.get("component_id") or "").strip()
        for s in steps
        if str(s.get("component_id") or "").strip()
    ]
    return "{} stations · {} steps".format(len(set(cids)), len(steps))


def start_last_wallclock(steps: list[dict]) -> tuple[str, str]:
    if not steps:
        return "—", "—"
    t0 = steps[0].get("time")
    t1 = steps[-1].get("time")
    return (str(t0) if t0 else "—", str(t1) if t1 else "—")


def format_journey_highlight_current(
    steps: list[dict], *, trailing_note: str = " · current"
) -> str:
    """Physical chain; last id in ``[ ]``. ``trailing_note`` empty omits the suffix (e.g. per-cycle list)."""
    cids = [
        str(s.get("component_id") or "").strip()
        for s in steps
        if str(s.get("component_id") or "").strip()
    ]
    if not cids:
        return "—"
    segs = []
    for i, c in enumerate(cids):
        if i == len(cids) - 1:
            segs.append("[{}]".format(c))
        else:
            segs.append(c)
    base = " → ".join(segs)
    return base + trailing_note if trailing_note else base


def compact_station_label(station_display: str) -> str:
    """station71 → ST71；splitter* → Split；corner2 → C2；Finished/Scrap 保留。"""
    s = (station_display or "").strip()
    if not s or s == "—":
        return "—"
    if s in ("Finished", "Scrap"):
        return s
    low = s.lower()
    if low.startswith("station"):
        tail = s[7:].strip()
        if tail:
            return "ST{}".format(tail)
        return s
    if "splitter" in low:
        return "Split"
    if low.startswith("corner") and len(s) > 6:
        return "C{}".format(s[6:])
    return s


def _mvp_cycle_bundle(
    life: list[dict],
    steps: list[dict],
    info: dict[str, Any],
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    cycle_life = _ui_cycle_steps(life)
    cycle_steps = _ui_cycle_steps(steps)
    info_cycle = (
        classification_for_display_cycle(life) if cycle_life else info
    )
    return cycle_life, cycle_steps, info_cycle


def format_cycle_time_mvp(seg: list[dict]) -> str:
    """当前 lap（和 ``_ui_cycle_steps`` / Progress / Status 同一事件切片）的墙钟时长。

    取该段内**第一条**与**最后一条**具备可解析 ``time``/``timestamp`` 的事件求差；
    不用「中间子集的首尾」以免首段缺时间会缩短时长。
    """
    if not seg:
        return "—"
    t0: float | None = None
    for s in seg:
        t0 = _step_epoch_seconds(s)
        if t0 is not None:
            break
    t1: float | None = None
    for s in reversed(seg):
        t1 = _step_epoch_seconds(s)
        if t1 is not None:
            break
    if t0 is None or t1 is None:
        return "—"
    dt = max(0.0, t1 - t0)
    m, sec = int(dt // 60), int(dt % 60)
    if m and sec:
        return "{}m {}s".format(m, sec)
    if m:
        return "{}m".format(m)
    return "{}s".format(sec)


def _reason_is_quality_only(reason: str) -> bool:
    """Flow 理由里仅与 FAIL/质量闭环相关的，不进入路径摘要行。"""
    r = (reason or "").strip().lower()
    if not r:
        return False
    if "fail then finish" in r:
        return True
    if "fail with process recovery" in r:
        return True
    if "fail — not finish" in r or "await process recovery" in r:
        return True
    if "multiple fail" in r:
        return True
    return False


def _mvp_path_conformance_line(reason: str) -> str:
    """只格式化**路径/工序**类原因（回流、scrap）；FAIL 相关在上游已过滤。"""
    r = (reason or "").strip()
    if not r:
        return ""
    low = r.lower()
    if "re-entered after upstream" in low:
        m = re.search(r"`([^`]+)`", r)
        st = m.group(1) if m else ""
        return (
            "⚠ Re-entered after upstream ({})".format(st)
            if st
            else "⚠ Stage re-entry (loop)"
        )
    if "path contains scrap" in low:
        return "✖ Scrap on path"
    if r == "Not FINISH yet":
        return ""
    return "⚠ Deviated path"


def mvp_conformance_insight(info: dict[str, Any], cycle_life: list[dict]) -> str:
    """路径/工序摘要（标准路由、re-entry 等）；FAIL/质量语义见 **Status** / Flow。"""
    if not cycle_life:
        return "—"
    acts = {str(s.get("activity") or "").strip().upper() for s in cycle_life}
    if "SCRAP" in acts:
        return "✖ Scrap on path"
    oc = info.get("outcome") or ""
    reasons = [str(x).strip() for x in (info.get("reasons") or []) if str(x).strip()]
    path_reasons = [r for r in reasons if not _reason_is_quality_only(r)]

    lines: list[str] = []
    for r in path_reasons:
        line = _mvp_path_conformance_line(r)
        if line:
            lines.append(line)

    seen: set[str] = set()
    uniq: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    lines = uniq

    if lines:
        if len(lines) == 1:
            return lines[0]
        return " · ".join(lines[:2])

    if oc == flow_classification.OUTCOME_SCRAP:
        return "✖ Scrap on path"

    # 仅剩质量类 OUTCOME（如 FAIL_OPEN / FAIL→FINISH），路径上无工序回流 → 不重复讲 FAIL
    if oc == flow_classification.OUTCOME_FAIL_OPEN:
        return "✔ Standard path"

    if oc == flow_classification.OUTCOME_REWORK:
        if segment_has_mainline_fail(cycle_life):
            return "✔ Standard path"
        loop_ev, _ = flow_classification.stage_rework_evidence(cycle_life)
        if loop_ev:
            return "⚠ Loop detected"
        return "✔ Standard path"

    if oc == flow_classification.OUTCOME_NORMAL:
        return "✔ Standard path"

    if oc == flow_classification.OUTCOME_OPEN:
        return "✔ Standard path"

    return "✔ Standard path"


def mvp_cycle_context_display(life: list[dict]) -> str:
    """当前第几圈 + 上一圈收口质量：**Rework = 该圈内主线 FAIL**，不用 Flow 的「纯回流无 FAIL」判 rework。"""
    segs = production_cycle_segments(life)
    if not segs:
        return "—"
    k = len(segs)
    open_tail = not (
        segs[-1]
        and str(segs[-1][-1].get("activity") or "").strip().upper() == "FINISH"
    )
    now = "C{}".format(k)
    if open_tail:
        now_l = "{} (open)".format(now)
    else:
        now_l = "{} (closed)".format(now)
    if k < 2:
        return "{} · no prior lap".format(now_l)
    prev = segs[k - 2]
    la = str(prev[-1].get("activity") or "").strip().upper()
    prev_acts = {str(s.get("activity") or "").strip().upper() for s in prev}
    if "SCRAP" in prev_acts or la == "SCRAP":
        prev_l = "prev ⛔ Scrap"
    elif la != "FINISH":
        prev_l = "prev ⏳ not closed"
    elif segment_has_mainline_fail(prev):
        prev_l = "prev ⚠ Rework (FAIL)"
    else:
        prev_l = "prev ✔ First pass"
    return "{} · {}".format(now_l, prev_l)


def mvp_status_line(steps: list[dict], info: dict[str, Any]) -> str:
    """**仅质量**：主线 FAIL → Rework(FAIL)；本圈已 FINISH 且无主线 FAIL → First pass；否则 Running；Scrap 单独。"""
    if not steps:
        return "—"
    last_act = str(steps[-1].get("activity") or "").strip().upper()
    oc = info.get("outcome") or ""
    has_fail = segment_has_mainline_fail(steps)
    scrap = last_act == "SCRAP" or oc == flow_classification.OUTCOME_SCRAP
    if not scrap:
        scrap = any(
            str(s.get("activity") or "").strip().upper() == "SCRAP" for s in steps
        )
    if scrap:
        return "🔴 Scrap"
    if has_fail:
        return "🟡 Rework (FAIL)"
    if last_act == "FINISH":
        return "🟢 First pass"
    return "🔵 Running"


def build_part_overview_row_mvp(
    part_id: str,
    steps: list[dict],
    info: dict[str, Any],
    *,
    preferred_stations: frozenset[str],
    lifecycle_steps: list[dict] | None = None,
) -> dict[str, Any]:
    """极简总览行：Part / Cycle / Status / …（Cycle = 当前圈 + 上一圈结论）。"""
    life = lifecycle_steps if lifecycle_steps is not None else steps
    cycle_life, cycle_steps, info_cycle = _mvp_cycle_bundle(life, steps, info)
    station_raw = current_station_display(cycle_life)
    return {
        "Part": part_id,
        "Cycle": mvp_cycle_context_display(life),
        "Status": mvp_status_line(cycle_life, info_cycle),
        "Current Station": compact_station_label(station_raw),
        "Progress": mainline_progress_text(
            cycle_steps, preferred_stations, part_id=part_id
        ),
        "Cycle time": format_cycle_time_mvp(cycle_life),
    }


def build_mvp_path_expander_row(
    part_id: str,
    steps: list[dict],
    info: dict[str, Any],
    *,
    lifecycle_steps: list[dict] | None = None,
) -> dict[str, str]:
    """供 MVP 表下方 expander：当前圈的 process_stage_path + 物理链（缩略）。"""
    life = lifecycle_steps if lifecycle_steps is not None else steps
    cycle_life, _, info_cycle = _mvp_cycle_bundle(life, steps, info)
    proc = (info_cycle.get("process_stage_path") or "").strip() or "—"
    proc_d = flow_classification.truncate_path(proc, max_len=100)
    phys = format_path_rework_visual(cycle_life)
    if not phys or phys == "—":
        phys = (info_cycle.get("station_path") or "").strip() or "—"
    phys_d = flow_classification.truncate_path(phys, max_len=140)
    return {
        "Part": part_id,
        "Process path": proc_d,
        "Physical trace": phys_d,
    }


def build_part_overview_row(
    part_id: str,
    steps: list[dict],
    info: dict[str, Any],
    *,
    preferred_stations: frozenset[str],
    include_observed_path: bool = True,
    lifecycle_steps: list[dict] | None = None,
) -> dict[str, Any]:
    """主表一行：Part Track 当前圈摘要。

    ``steps`` 可为「Process level」过滤后事件，用于进度与矩阵；``lifecycle_steps`` 为全量事件时
    用于 FINISH 分段。**当前站 / 进度 / Flow / Deviation / cleaned path** 均基于
    **当前工艺圈**（仅 **FINISH** 闭合一圈；FAIL/回流不改变周期边界）。``info`` 仍保留在签名上
    以便调用方兼容；行内展示以 ``classification_for_display_cycle(life)`` 为准。
    """
    life = lifecycle_steps if lifecycle_steps is not None else steps
    cycle_life = _ui_cycle_steps(life)
    cycle_steps = _ui_cycle_steps(steps)
    info_cycle = (
        classification_for_display_cycle(life)
        if cycle_life
        else info
    )
    start_t, last_t = start_last_wallclock(cycle_life)
    dev = " · ".join(info_cycle.get("reasons") or [])
    cur_c, _, _ = production_cycle_identity(life)
    ci = cur_c if cur_c > 0 else 1
    row: dict[str, Any] = {
        "Part ID": part_id,
        "Cycles": production_cycle_column_label(life),
        "Cycle type (this lap)": cycle_quality_label(
            cycle_life, info=info_cycle
        ),
        "Current Status": current_status_display(cycle_life, info_cycle),
        "Current Station": current_station_display(cycle_life),
        "Progress (this cycle)": mainline_progress_labeled_for_current_cycle(
            cycle_steps,
            preferred_stations,
            cycle_index=ci,
            part_id=part_id,
        ),
        "Flow Type": flow_classification.flow_type_badge(info_cycle),
        "Passed (this cycle)": passed_stations_summary(cycle_life),
        "Start Time": start_t,
        "Last Update": last_t,
        "Deviation (this cycle)": dev if dev else "—",
    }
    if include_observed_path:
        row["Observed path (physical)"] = format_journey_highlight_current(cycle_life)
        row["Process path (cleaned)"] = info_cycle.get("process_stage_path") or "—"
    return row

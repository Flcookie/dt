"""Part 路径结局分类：Normal / Rework / Scrap / 进行中（与 week6 纪要一致，规则版）。

**物理回路 ≠ 工艺返工**；**一轮 FINISH 后的新一圈** 不与上一圈拼接判返工（FINISH 分段）；**rollback**
只看 ``twin_layout.stage_entry_sequence_clean``（**PROCESS/PASS** -only、去重），不把 LOAD/TRANSFER
等记入阶段序列。**仅当每一步都有有效** ``timestamp`` 时才按时间排序；**FAIL** 后仍有 **PROCESS/PASS** 或 **≥2 次 FAIL**
而未 **FINISH** → **Rework**；仅单次 FAIL 且末事件后无恢复 → **FAIL_OPEN**。
"""
from __future__ import annotations

from typing import Any

import twin.twin_layout as twin_layout

OUTCOME_SCRAP = "scrap"
OUTCOME_REWORK = "rework"
OUTCOME_NORMAL = "normal"
OUTCOME_OPEN = "open"
# FAIL on path, last event not FINISH — show as **FAIL** in UI (not Scrap, not Rework label).
OUTCOME_FAIL_OPEN = "fail_open"

OUTCOME_LABEL = {
    OUTCOME_SCRAP: "Scrap",
    OUTCOME_REWORK: "Rework",
    OUTCOME_NORMAL: "Normal",
    OUTCOME_OPEN: "In progress",
    OUTCOME_FAIL_OPEN: "FAIL",
}

# Back-compat for older code/tests referencing OUTCOME_LABEL_ZH
OUTCOME_LABEL_ZH = OUTCOME_LABEL

# 方案5.md · 差距2：Workflow 颜色分类（表格列展示）
OUTCOME_BADGE_ZH = {
    OUTCOME_NORMAL: "🟢",
    OUTCOME_REWORK: "🟡",
    OUTCOME_SCRAP: "🔴",
    OUTCOME_OPEN: "⚪",
    OUTCOME_FAIL_OPEN: "⚠️",
}


def flow_type_badge(info_or_outcome) -> str:
    if isinstance(info_or_outcome, dict):
        o = info_or_outcome.get("outcome") or ""
        base = info_or_outcome.get("label_zh") or OUTCOME_LABEL.get(o, "—")
    else:
        o = info_or_outcome or ""
        base = OUTCOME_LABEL.get(o, str(o))
    icon = OUTCOME_BADGE_ZH.get(o, "⚪")
    return "{} {}".format(icon, base)


flow_type_badge_zh = flow_type_badge


def station_sequence_from_steps(steps: list[dict]) -> list[str]:
    return [str(s.get("component_id") or "").strip() or "—" for s in steps]


def _sort_steps_chronological(steps: list[dict]) -> list[dict]:
    """按 ``timestamp`` 排序**仅当每一步都有有效时间戳**；否则保持原序（避免部分缺 ts 时序被打乱）。"""
    if len(steps) < 2:
        return list(steps)
    parsed: list[float] = []
    for s in steps:
        ts = s.get("timestamp")
        if ts is None:
            return list(steps)
        try:
            parsed.append(float(ts))
        except (TypeError, ValueError):
            return list(steps)
    enumerated = list(enumerate(steps))
    return [
        s
        for _, s in sorted(enumerated, key=lambda ix: (parsed[ix[0]], ix[0]))
    ]


def _has_process_pass_after_last_fail(steps: list[dict]) -> bool:
    """最近一次 FAIL 之后是否仍有 PROCESS/PASS（返工恢复进行中，而非卡在 FAIL 行）。"""
    last_fail = -1
    for i, s in enumerate(steps):
        if str(s.get("activity") or "").strip().upper() == "FAIL":
            last_fail = i
    if last_fail < 0:
        return False
    tail = steps[last_fail + 1 :]
    for s in tail:
        if str(s.get("activity") or "").strip().upper() in (
            "PROCESS",
            "PASS",
        ):
            return True
    return False


# Keys must match ``twin_layout.PROCESS_STAGE_ORDER`` labels.
_MAINLINE_STAGE_RANK: dict[str, int] = {
    "ST11": 0,
    "ST21/22": 1,
    "ST31": 2,
    "ST41": 3,
    "ST51": 4,
    "ST61": 5,
    "ST71": 6,
}


def _mainline_rank(stage_id: str) -> int | None:
    return _MAINLINE_STAGE_RANK.get(stage_id)


def _finish_delimited_segments(steps: list[dict]) -> list[list[dict]]:
    """Segments that each end with a FINISH row (last segment may have no FINISH yet).

    **Cycle boundary = FINISH** (plant log often has CHECKOUT on the same node immediately before
    FINISH; FAIL / RETURN / routing do **not** close a cycle).
    """
    out: list[list[dict]] = []
    cur: list[dict] = []
    for s in steps:
        cur.append(s)
        if str(s.get("activity") or "").strip().upper() == "FINISH":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _open_tail_after_last_finish(steps: list[dict]) -> list[dict]:
    """Events strictly after the last FINISH; full steps if none (first pass)."""
    last_i = -1
    for i, s in enumerate(steps):
        if str(s.get("activity") or "").strip().upper() == "FINISH":
            last_i = i
    if last_i < 0:
        return list(steps)
    return steps[last_i + 1 :]


def stage_rework_evidence(steps: list[dict]) -> tuple[bool, str]:
    """Mainline rollback detection with FINISH-aware scope.

    - Trace **ends with FINISH**: any finish-delimited cycle may show rollback
      (earlier bad lap still counts for the closed history).
    - **Still in progress**: only the **open tail after the last FINISH** is checked, so
      a legitimate second lap after checkout does not combine with ST71→ST11 transport
      as a false “ST21 re-entry after ST11 upstream”.
    """
    if not steps:
        return False, ""
    last_act = str(steps[-1].get("activity") or "").strip().upper()
    if last_act == "FINISH":
        for seg in _finish_delimited_segments(steps):
            st = twin_layout.stage_entry_sequence_clean(seg)
            hit, msg = has_stage_rework_loop(st)
            if hit:
                return True, msg
        return False, ""
    tail = _open_tail_after_last_finish(steps)
    st = twin_layout.stage_entry_sequence_clean(tail)
    return has_stage_rework_loop(st)


def has_stage_rework_loop(stage_entries: list[str]) -> tuple[bool, str]:
    """**Process rollback** on the fixed mainline model — not “any loop” in the plant layout.

    True when a part **re-enters** a mainline stage cell **after** visiting a **strictly earlier**
    mainline stage (e.g. ST41→ST31→ST41). That is **stage-level rework**, independent of whether
    the physical conveyor used a return belt / splitter path between visits.

    Does **not** flag ST41→**ST51**→ST41 (forward branch then back on the U-line); parallel
    lanes merged into one stage (ST21/22, ST51/52) do not count as rollback when swapping
    within the same stage.
    """
    last_pos: dict[str, int] = {}
    for idx, sid in enumerate(stage_entries):
        if not sid or sid == "—":
            continue
        si = _mainline_rank(sid)
        if sid in last_pos:
            prev = last_pos[sid]
            between = stage_entries[prev + 1 : idx]
            if not any(b != sid for b in between):
                last_pos[sid] = idx
                continue
            if si is None:
                last_pos[sid] = idx
                continue
            saw_upstream = False
            for b in between:
                if b == sid:
                    continue
                br = _mainline_rank(b)
                if br is not None and br < si:
                    saw_upstream = True
                    break
            if saw_upstream:
                return True, "Stage cell `{}` re-entered after upstream visit".format(
                    sid
                )
        last_pos[sid] = idx
    return False, ""


def _classify_base_fields(
    stations: list[str],
    path_str: str,
    stage_path_str: str,
) -> dict[str, Any]:
    return {
        "station_path": path_str,
        "station_ids": stations,
        "process_stage_path": stage_path_str,
    }


def classify_flow_from_steps(steps: list[dict]) -> dict[str, Any]:
    """``steps`` 与 ``neo4j_backend.query_part_flow`` 去重后的 ``steps`` 项结构一致。

    **判定顺序**：**SCRAP** → 末 **FINISH**（FAIL 或段内 rollback → **Rework**，否则 **Normal**）→
    末非 **FINISH** 且有 **FAIL**：若最后一次 FAIL 之后仍有 **PROCESS/PASS** → **Rework**（恢复中），
    否则 **FAIL_OPEN** → 否则 open tail rollback → **Rework** → **Open**。Rollback 见
    ``stage_rework_evidence``。

    - **Scrap**：路径上显式 **SCRAP**。**FAIL** alone ≠ Scrap。
    - **FAIL_OPEN**：存在 **FAIL**，尚未 **FINISH**，且非多次 FAIL、且最后一次 FAIL 之后**没有** PROCESS/PASS。
    - **Rework**：段内 rollback、**FAIL 后 FINISH**、**FAIL 后已继续 PROCESS/PASS**、或 **≥2 次 FAIL**（未 FINISH）。
    - **Normal**：末 **FINISH**，无 FAIL、无段内 rollback。
    - **Open**：进行中，无 FAIL、无 open tail 内 rollback。

    ``process_stage_path`` 使用 **PROCESS/PASS-only** 干净序列；``station_path`` 仍为原始 component
    链。仅使用 **当前 Neo4j 已有事件**。
    """
    steps = _sort_steps_chronological(steps)
    stations = station_sequence_from_steps(steps)
    path_str = " → ".join(stations) if stations else "—"
    stage_entries_full = twin_layout.stage_entry_sequence_clean(steps)
    stage_path_str = " → ".join(stage_entries_full) if stage_entries_full else "—"
    base = _classify_base_fields(stations, path_str, stage_path_str)
    acts_upper = [str(s.get("activity") or "").strip().upper() for s in steps]
    reasons: list[str] = []
    last_act = acts_upper[-1] if acts_upper else ""
    has_fail = any(a == "FAIL" for a in acts_upper)

    if any(a == "SCRAP" for a in acts_upper):
        reasons.append("Path contains SCRAP")
        return {
            "outcome": OUTCOME_SCRAP,
            "label_zh": OUTCOME_LABEL[OUTCOME_SCRAP],
            "reasons": reasons,
            **base,
        }

    stage_loop, sl_reason = stage_rework_evidence(steps)
    rework = stage_loop
    if sl_reason:
        reasons.append(sl_reason)

    if last_act == "FINISH":
        if has_fail or rework:
            if has_fail:
                reasons.append("FAIL then FINISH (rework closed)")
            return {
                "outcome": OUTCOME_REWORK,
                "label_zh": OUTCOME_LABEL[OUTCOME_REWORK],
                "reasons": reasons,
                **base,
            }
        return {
            "outcome": OUTCOME_NORMAL,
            "label_zh": OUTCOME_LABEL[OUTCOME_NORMAL],
            "reasons": [],
            **base,
        }

    if has_fail and last_act != "FINISH":
        fail_count = sum(1 for a in acts_upper if a == "FAIL")
        if fail_count >= 2:
            reasons.append(
                "Multiple FAIL events (unstable rework; not FINISH yet)"
            )
            return {
                "outcome": OUTCOME_REWORK,
                "label_zh": OUTCOME_LABEL[OUTCOME_REWORK],
                "reasons": reasons,
                **base,
            }
        if _has_process_pass_after_last_fail(steps):
            reasons.append(
                "FAIL with process recovery in progress (not FINISH yet)"
            )
            return {
                "outcome": OUTCOME_REWORK,
                "label_zh": OUTCOME_LABEL[OUTCOME_REWORK],
                "reasons": reasons,
                **base,
            }
        reasons.append(
            "FAIL — not FINISH yet (await process recovery, FINISH, or SCRAP)"
        )
        return {
            "outcome": OUTCOME_FAIL_OPEN,
            "label_zh": OUTCOME_LABEL[OUTCOME_FAIL_OPEN],
            "reasons": reasons,
            **base,
        }

    if rework:
        return {
            "outcome": OUTCOME_REWORK,
            "label_zh": OUTCOME_LABEL[OUTCOME_REWORK],
            "reasons": reasons,
            **base,
        }

    reasons.append("Not FINISH yet")
    return {
        "outcome": OUTCOME_OPEN,
        "label_zh": OUTCOME_LABEL[OUTCOME_OPEN],
        "reasons": reasons,
        **base,
    }


def truncate_path(path: str, *, max_len: int = 72) -> str:
    if len(path) <= max_len:
        return path
    return path[: max_len - 1] + "…"


def format_trace_scheme_b(
    part_id: str,
    steps: list[dict],
    *,
    max_stations: int = 20,
) -> str:
    """老师方案 B：工站 **序列**（非树），含 rework 重复；第二行 ``↑`` 标最后工位（当前）。"""
    cids = [str(s.get("component_id") or "").strip() for s in steps]
    cids = [c for c in cids if c]
    if not cids:
        return "{}: (no stations)".format(part_id)
    truncated = len(cids) > max_stations
    tail = cids[-max_stations:] if truncated else list(cids)
    segments = ["[{}]".format(c) for c in tail]
    sep = " → "
    line1_body = sep.join(segments)
    if truncated:
        line1_body = "… → " + line1_body
    leader = "{}: ".format(part_id)
    line1 = leader + line1_body
    prefix_elide = leader + ("… → " if truncated else "")
    pos = len(prefix_elide)
    for i in range(len(segments) - 1):
        pos += len(segments[i]) + len(sep)
    line2 = " " * pos + "↑ current"
    return line1 + "\n" + line2

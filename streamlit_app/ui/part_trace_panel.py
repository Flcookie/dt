"""Part Trace overview + conformance; standalone page or Digital Twin embed."""
from __future__ import annotations

import html as html_module
import time

import pandas as pd
import streamlit as st

import part_track.flow_classification as flow_classification
import part_track.flow_conformance_engine as fce
import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import part_track.part_station_matrix as part_station_matrix
import part_track.part_track_conformance as part_track_conformance
import part_track.part_track_model as part_track_model
import twin.twin_layout as twin_layout


def _twin_session_summary_column_names() -> list[str]:
    return ["Part ID"] + list(fce.FLOW_STEP_HEADERS_M) + [
        "Location",
        "Trace",
    ]


# 与 code/flow.py FLOW CONFORMANCE 一致（图例 + 单元格色）
_TWIN_FLOW_CLR_STEP_DONE = "#1a6b3c"
_TWIN_FLOW_CLR_STEP_REWORK = "#e67e22"
_TWIN_FLOW_CLR_STEP_EMPTY = "#e8e8e8"
_TWIN_FLOW_CLR_QC = "#8e44ad"
_TWIN_FLOW_CLR_SKIPPED_BORDER = "#ff9999"
_TWIN_FLOW_CLR_COL_ANOMALY = "#fff0f0"
_TWIN_FLOW_CLR_SCRAP = "#4b5563"
_TWIN_FLOW_CLR_COL_NORMAL = "#ffffff"
_TWIN_FLOW_Q_TRACE = "dt_pt_trace"
_DT_TRACE_ROWS_SESSION_KEY = "_dt_trace_rows_pt"
_VIEW_TRACE_CELL = "View"
_PT_FLASH_SEC = 2.0
_PT_FLASH_CHECKOUT_BG = "rgba(34, 197, 94, 0.38)"
_PT_FLASH_SCRAP_BG = "rgba(239, 68, 68, 0.38)"


def _twin_flow_maybe_open_trace_from_query(rows_pt: list[dict]) -> None:
    qp = st.query_params
    if _TWIN_FLOW_Q_TRACE not in qp:
        return
    raw = qp.get(_TWIN_FLOW_Q_TRACE)
    try:
        ix = int(raw)
        if 0 <= ix < len(rows_pt):
            st.session_state["ptc_twin_trace_modal_idx"] = ix
    except (TypeError, ValueError):
        pass
    try:
        del qp[_TWIN_FLOW_Q_TRACE]
    except Exception:
        pass


def _twin_flow_step_cell_inner_html(step_i: int, rep: dict) -> str:
    dg = rep.get("display_step_grid") or {}
    stt = dg.get(step_i, "NOT_DONE")
    lap_open = bool(rep.get("lap_open"))
    hcx = bool(rep.get("has_cycle_context"))
    fs = rep.get("flow_state") or {}
    n_rework_list = fs.get("n_rework") or [1] * fce.N_STEPS
    try:
        n_rew = int(n_rework_list[step_i])
    except (TypeError, ValueError, IndexError):
        n_rew = 1
    if stt == "SCRAP":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_SCRAP};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;'>"
            "<span style='font-size:1.1rem;line-height:1;'>🗑</span>"
            "</div>"
        )
    if stt == "REWORK":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_STEP_REWORK};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;'>"
            "<span style='font-size:1.8rem;line-height:1;'>↺</span>"
            f"<span style='position:absolute;font-size:0.55rem;font-weight:bold;"
            f"margin-top:2px;'>{n_rew}</span>"
            "</div>"
        )
    if stt == "QC":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_QC};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;font-size:0.75rem;'>"
            "🔍"
            "</div>"
        )
    if stt == "DONE":
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_DONE};"
            "margin:auto;'></div>"
        )
    if stt == "NOT_DONE" and hcx and not lap_open:
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
            f"margin:auto;border:2px solid {_TWIN_FLOW_CLR_SKIPPED_BORDER};'></div>"
        )
    if stt == "NOT_DONE":
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
            "margin:auto;border:1px solid #ccc;'></div>"
        )
    return (
        f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
        "margin:auto;border:1px solid #ccc;'></div>"
    )


def _twin_flow_slot_cell_inner_html(slot: str, rep: dict) -> str:
    dg = rep.get("display_grid") or {}
    stt = dg.get(slot, "NOT_DONE")
    lap_open = bool(rep.get("lap_open"))
    hcx = bool(rep.get("has_cycle_context"))
    if stt == "SCRAP":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_SCRAP};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;'>"
            "<span style='font-size:1.1rem;line-height:1;'>🗑</span>"
            "</div>"
        )
    if stt == "REWORK":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_STEP_REWORK};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;'>"
            "<span style='font-size:1.8rem;line-height:1;'>↺</span>"
            "</div>"
        )
    if stt == "QC":
        return (
            f"<div style='position:relative;width:28px;height:28px;border-radius:4px;"
            f"background:{_TWIN_FLOW_CLR_QC};margin:auto;display:flex;"
            "align-items:center;justify-content:center;color:white;font-size:0.75rem;'>"
            "🔍"
            "</div>"
        )
    if stt == "DONE":
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_DONE};"
            "margin:auto;'></div>"
        )
    if stt == "NOT_DONE" and hcx and not lap_open:
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
            f"margin:auto;border:2px solid {_TWIN_FLOW_CLR_SKIPPED_BORDER};'></div>"
        )
    if stt == "NOT_DONE":
        return (
            f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
            "margin:auto;border:1px solid #ccc;'></div>"
        )
    return (
        f"<div style='width:28px;height:28px;border-radius:4px;background:{_TWIN_FLOW_CLR_STEP_EMPTY};"
        "margin:auto;border:1px solid #ccc;'></div>"
    )


def _ensure_part_trace_flash_state() -> None:
    if "_pt_part_lap_sig" not in st.session_state:
        st.session_state._pt_part_lap_sig = {}
    if "_pt_row_flash_until" not in st.session_state:
        st.session_state._pt_row_flash_until = {}


def _part_trace_row_flash_bg(part_id: str) -> str | None:
    until_map = st.session_state.get("_pt_row_flash_until") or {}
    entry = until_map.get(part_id)
    if not entry:
        return None
    try:
        kind, until = entry[0], float(entry[1])
    except (TypeError, ValueError, IndexError):
        return None
    if time.time() > until:
        return None
    if kind == "checkout":
        return _PT_FLASH_CHECKOUT_BG
    if kind == "scrap":
        return _PT_FLASH_SCRAP_BG
    return None


def _update_part_trace_row_flashes(rows_pt: list[dict]) -> None:
    """Detect FINISH / SCRAP transitions; brief row highlight (not persistent)."""
    _ensure_part_trace_flash_state()
    now = time.time()
    prev = dict(st.session_state._pt_part_lap_sig)
    flashes = dict(st.session_state._pt_row_flash_until)
    new_sig: dict[str, str] = {}
    for r in rows_pt:
        pid = str(r.get("Part ID") or "").strip()
        if not pid:
            continue
        rep = r.get("_replay") or {}
        laps = rep.get("laps") or []
        last_out = str(laps[-1].get("outcome") or "").upper() if laps else ""
        dg = rep.get("display_grid") or {}
        is_scrap = last_out == "SCRAP" or any(
            dg.get(s) == "SCRAP" for s in part_track_conformance.SLOTS
        )
        sig = "{}|{}|{}".format(int(bool(rep.get("lap_open"))), last_out, int(is_scrap))
        new_sig[pid] = sig
        if pid in prev and prev[pid] != sig:
            if not rep.get("lap_open") and last_out == "FINISH":
                flashes[pid] = ("checkout", now + _PT_FLASH_SEC)
            elif is_scrap:
                flashes[pid] = ("scrap", now + _PT_FLASH_SEC)
    flashes = {
        k: v
        for k, v in flashes.items()
        if isinstance(v, (list, tuple))
        and len(v) >= 2
        and now <= float(v[1])
    }
    st.session_state._pt_part_lap_sig = new_sig
    st.session_state._pt_row_flash_until = flashes


def _twin_flow_legend_html() -> str:
    """图例：工序格状态（Done=工位已过；无 lap 级 Finished，整圈 FINISH 仅行闪绿）。"""
    d = _TWIN_FLOW_CLR_STEP_DONE
    r = _TWIN_FLOW_CLR_STEP_REWORK
    e = _TWIN_FLOW_CLR_STEP_EMPTY
    scrap = _TWIN_FLOW_CLR_SCRAP
    return (
        "<div style='display:flex;gap:16px;margin-bottom:10px;font-size:13px;"
        "align-items:center;flex-wrap:wrap;color:#64748b;'>"
        "<span style='display:inline-flex;align-items:center;gap:6px;'>"
        f"<span style='width:14px;height:14px;border-radius:3px;background:{d};"
        "display:inline-block;'></span>Done</span>"
        "<span style='display:inline-flex;align-items:center;gap:6px;'>"
        f"<span style='position:relative;width:14px;height:14px;border-radius:3px;"
        f"background:{r};display:inline-flex;align-items:center;justify-content:center;"
        "color:white;font-size:11px;line-height:1;'>↺</span>Rework</span>"
        "<span style='display:inline-flex;align-items:center;gap:6px;'>"
        f"<span style='position:relative;width:14px;height:14px;border-radius:3px;"
        f"background:{_TWIN_FLOW_CLR_QC};display:inline-flex;align-items:center;"
        "justify-content:center;color:white;font-size:10px;line-height:1;'>🔍</span>QC</span>"
        "<span style='display:inline-flex;align-items:center;gap:6px;'>"
        f"<span style='width:14px;height:14px;border-radius:3px;background:{e};"
        "border:1px solid #cbd5e1;display:inline-block;'></span>Not Done</span>"
        "<span style='display:inline-flex;align-items:center;gap:6px;'>"
        f"<span style='width:14px;height:14px;border-radius:3px;background:{scrap};"
        "display:inline-flex;align-items:center;justify-content:center;color:white;"
        "font-size:9px;line-height:1;'>🗑</span>Scrapped</span>"
        "</div>"
    )


def _twin_flow_row_td_background(rep: dict, conf_style: str, part_id: str = "") -> str:
    """Row background: flash only. Rework/scrap/anomaly show in slot cells, not whole row."""
    _ = rep, conf_style
    flash = _part_trace_row_flash_bg(part_id) if part_id else None
    if flash:
        return flash
    return _TWIN_FLOW_CLR_COL_NORMAL


def render_digital_twin_flow_conformance_table(rows_pt: list[dict]) -> None:
    """Digital Twin：工艺格矩阵 + Complete trace 弹窗。"""
    _update_part_trace_row_flashes(rows_pt)
    st.markdown(_twin_flow_legend_html(), unsafe_allow_html=True)

    _cell = (
        "<div style='padding:8px 4px;background:#1e293b;color:white;font-size:14px;"
        "font-weight:600;border-radius:4px;text-align:center;min-height:2.5rem;"
        "display:flex;align-items:center;justify-content:center;white-space:nowrap;'>{}</div>"
    )
    _hdr_slot = (
        "<div style='padding:6px 2px;background:#1e293b;color:white;font-size:13px;"
        "font-weight:600;border-radius:4px;text-align:center;min-height:2.5rem;"
        "display:flex;align-items:center;justify-content:center;"
        "white-space:nowrap;line-height:1.15;'>{}</div>"
    )

    n_steps = fce.N_STEPS
    # Part / Location / Trace 收窄；M5 两列略加宽以免表头换行
    _step_w = [0.34, 0.36, 0.34, 0.36, 0.46, 0.36, 0.46, 0.34, 0.34]
    col_weights = [0.46] + _step_w + [0.50, 0.46]
    hdr = st.columns(col_weights, gap="small", vertical_alignment="center")
    with hdr[0]:
        st.markdown(_cell.format("Part"), unsafe_allow_html=True)
    for j in range(n_steps):
        lab = html_module.escape(fce.FLOW_STEP_HEADERS_M[j])
        with hdr[j + 1]:
            st.markdown(_hdr_slot.format(lab), unsafe_allow_html=True)
    with hdr[n_steps + 1]:
        st.markdown(_hdr_slot.format("Location"), unsafe_allow_html=True)
    with hdr[n_steps + 2]:
        st.markdown(_hdr_slot.format("Trace"), unsafe_allow_html=True)

    st.markdown(
        "<div style='height:10px;line-height:0;font-size:0;' aria-hidden='true'>&nbsp;</div>",
        unsafe_allow_html=True,
    )

    if not rows_pt:
        return

    for i, r in enumerate(rows_pt):
        rep = r["_replay"]
        cs = r.get("_conf_style", "neutral")
        row_bg = _twin_flow_row_td_background(rep, cs, str(r.get("Part ID") or ""))
        pid = html_module.escape(str(r.get("Part ID") or ""))
        loc_txt = html_module.escape(str(r.get("Current Location") or ""))

        cols = st.columns(col_weights, gap="small", vertical_alignment="center")
        with cols[0]:
            st.markdown(
                "<div style='padding:6px 4px;background:#334155;color:white;font-weight:600;"
                "font-size:14px;border-radius:4px;text-align:center;white-space:nowrap;'>{}</div>".format(
                    pid
                ),
                unsafe_allow_html=True,
            )
        for j in range(n_steps):
            inner = _twin_flow_step_cell_inner_html(j, rep)
            with cols[j + 1]:
                st.markdown(
                    "<div style='padding:6px 4px;background:{};text-align:center;border-radius:4px;'>{}</div>".format(
                        row_bg, inner
                    ),
                    unsafe_allow_html=True,
                )
        with cols[n_steps + 1]:
            st.markdown(
                "<div style='padding:6px 4px;background:{};font-size:14px;color:#1e293b;"
                "border-radius:4px;text-align:center;display:flex;align-items:center;"
                "justify-content:center;min-height:2.4rem;white-space:nowrap;"
                "overflow:hidden;text-overflow:ellipsis;'>{}</div>".format(
                    row_bg, loc_txt
                ),
                unsafe_allow_html=True,
            )
        with cols[n_steps + 2]:
            if st.button(
                _VIEW_TRACE_CELL,
                key="dt_pt_trace_btn_{}".format(i),
                type="secondary",
                use_container_width=True,
                disabled=str(r.get("Part ID") or "") == "—",
                help="View complete trace for this part",
            ):
                st.session_state["ptc_twin_trace_modal_idx"] = i
                # 按钮在 Digital Twin 的 @st.fragment 内时，默认只重跑 fragment；
                # 弹窗在 fragment 外打开，需整页 rerun。
                st.rerun()


def render_pending_complete_trace_dialog() -> None:
    """在 **fragment 外** 调用，避免 ``st.dialog`` 嵌在 fragment 里导致异常刷新或跳转感。"""
    rows = st.session_state.get(_DT_TRACE_ROWS_SESSION_KEY)
    ix = st.session_state.get("ptc_twin_trace_modal_idx")
    if ix is None or not isinstance(rows, list):
        return
    if not (0 <= ix < len(rows)):
        return
    r = rows[ix]
    _open_twin_complete_trace_dialog(str(r.get("Part ID") or ""), r.get("_replay") or {})


def _twin_trace_dialog_dismiss() -> None:
    st.session_state.pop("ptc_twin_trace_modal_idx", None)
    st.session_state.pop("ptc_twin_trace_idx", None)


@st.dialog("Complete trace", width="large", on_dismiss=_twin_trace_dialog_dismiss)
def _open_twin_complete_trace_dialog(_part_id: str, rep: dict) -> None:
    st.markdown(
        part_track_conformance.format_complete_trace_meaningful_html(rep),
        unsafe_allow_html=True,
    )


def bootstrap_part_trace_session_state(*, from_query_params: bool) -> None:
    if "part_trace_part_id" not in st.session_state:
        st.session_state.part_trace_part_id = ""
    if from_query_params and "part_trace_qp_injected" not in st.session_state:
        st.session_state.part_trace_qp_injected = True
        _qp = st.query_params.get("part_id")
        if _qp:
            _p0 = str(_qp).strip()
            if _p0:
                st.session_state["part_trace_part_id"] = _p0


def _filter_steps_for_level(
    steps: list[dict], event_level: str
) -> list[dict]:
    if event_level.startswith("Process"):
        return [
            s
            for s in steps
            if s.get("activity") in neo4j_backend.PROCESS_LEVEL_ACTIVITIES
        ]
    return list(steps)


def _flow_matrix_label(info: dict) -> str:
    o = info.get("outcome") or ""
    return flow_classification.OUTCOME_LABEL.get(o, info.get("label_zh") or "—")


def _render_outcome_banner(info: dict) -> None:
    outcome = info.get("outcome") or ""
    reasons = info.get("reasons") or []
    msg = "**Flow type**: {}".format(flow_classification.flow_type_badge(info))
    if outcome == flow_classification.OUTCOME_SCRAP:
        st.error(msg)
    elif outcome == flow_classification.OUTCOME_FAIL_OPEN:
        st.error(msg)
    elif outcome == flow_classification.OUTCOME_REWORK:
        st.warning(msg)
    elif outcome == flow_classification.OUTCOME_NORMAL:
        st.success(msg)
    else:
        st.info(msg)
    if reasons:
        st.caption("**Deviation**: " + " · ".join(reasons))
    with st.expander("Physical vs process path (text)", expanded=False):
        st.caption(
            "**Observed path (physical)** — raw `component_id` chain (splitter / corner / …)."
        )
        st.code(info.get("station_path") or "—", language="text")
        st.caption(
            "**Process path (cleaned)** — PROCESS/PASS mainline stages only; used for rollback / Flow."
        )
        st.code(info.get("process_stage_path") or "—", language="text")


def _mvp_process_paths_expander(path_rows: list[dict]) -> None:
    if not path_rows:
        return
    with st.expander("Process paths — this lap", expanded=False):
        st.caption(
            "**Process path** = PROCESS/PASS mainline (same basis as conformance rules); "
            "**Physical trace** = raw `component_id` chain, including FAIL❌ / FINISH✓."
        )
        st.dataframe(
            path_rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Part": st.column_config.TextColumn("Part", width="small"),
                "Process path": st.column_config.TextColumn(
                    "Process path", width="large"
                ),
                "Physical trace": st.column_config.TextColumn(
                    "Physical trace", width="large"
                ),
            },
        )


def _render_progress_bar(columns: list[str], steps: list[dict]) -> None:
    cur = (steps[-1].get("component_id") or "").strip() if steps else ""
    anchor = part_station_matrix.progress_anchor_component_id(steps, columns)
    r = part_station_matrix.mainline_progress_ratio(columns, anchor)
    st.caption(
        "Mainline {} cells · anchor **{}** · last node **{}**".format(
            len(columns),
            anchor or "—",
            cur or "—",
        )
    )
    st.progress(min(1.0, r))


def _part_track_mvp_column_config() -> dict:
    """多零件汇总：MVP 列（Part / Cycle / Status / … / Cycle time）。"""
    return {
        "Part": st.column_config.TextColumn("Part", width="small"),
        "Cycle": st.column_config.TextColumn(
            "Cycle",
            width="medium",
            help=(
                "**C n** = the n-th FINISH-delimited lap; **(open)** / **(closed)** whether the last lap has FINISH yet. "
                "**prev** = when the previous lap is FINISH-closed: **⚠ Rework (FAIL)** only if that lap had a **mainline station FAIL**; "
                "otherwise **✔ First pass** (same as KPI logic; not inferred from cycle numbers)."
            ),
        ),
        "Status": st.column_config.TextColumn(
            "Status",
            width="small",
            help=(
                "**Quality only**: 🟢 First pass (lap closed with no mainline FAIL), 🟡 Rework (FAIL), "
                "🔵 Running, 🔴 Scrap. Path / stage detail in **Process paths — this lap** below."
            ),
        ),
        "Current Station": st.column_config.TextColumn(
            "Current Station",
            width="small",
            help="Abbreviated current station (ST* / Split / C*).",
        ),
        "Progress": st.column_config.TextColumn(
            "Progress",
            width="large",
            help="**n/7** with **▓** for completed cells and **░** for not reached (light stripe look); same mainline order as the stage matrix.",
        ),
        "Cycle time": st.column_config.TextColumn(
            "Cycle time",
            width="small",
            help=(
                "Wall-clock span within **this lap** (same FINISH-delimited slice as Cycle Cn and Progress), "
                "from first to last event with a **parseable** time."
            ),
        ),
    }


def _part_track_overview_column_config(*, slim_overview: bool) -> dict:
    """Column help text aligned with FINISH-delimited cycles + FPY-style lap type."""
    col_cfg: dict = {
        "Part ID": st.column_config.TextColumn("Part ID", width="small"),
        "Cycles": st.column_config.TextColumn(
            "Cycles",
            width="medium",
            help="**Cycle n · k completed** — current lap index and FINISH-closed count; **in progress** if last lap not FINISH yet.",
        ),
        "Cycle type (this lap)": st.column_config.TextColumn(
            "Cycle type",
            width="small",
            help=(
                "**✔ First pass** — no mainline FAIL and no path anomaly; **⚠️ Reworked** — only when this lap has a **mainline station FAIL**; "
                "**⚠ Path deviation** — e.g. ST backflow / splitter routing **without** mainline FAIL; **⛔ Scrap**."
            ),
        ),
        "Current Status": st.column_config.TextColumn(
            "Status",
            help="From **current** lap events + classify on that lap only.",
        ),
        "Current Station": st.column_config.TextColumn("Station"),
        "Progress (this cycle)": st.column_config.TextColumn(
            "Progress",
            width="medium",
            help="Mainline **n/7** bar for **this cycle** (prefix **C n ·**).",
        ),
        "Flow Type": st.column_config.TextColumn(
            "Flow Type",
            width="medium",
            help="Observed outcome for **this cycle**.",
        ),
        "Passed (this cycle)": st.column_config.TextColumn(
            "Passed",
            width="small",
            help="Distinct stations in **this** lap.",
        ),
        "Start Time": st.column_config.TextColumn(
            "Start",
            help="First event time in **this** lap.",
        ),
        "Last Update": st.column_config.TextColumn(
            "Last",
            help="Last event time in **this** lap.",
        ),
        "Deviation (this cycle)": st.column_config.TextColumn(
            "Deviation",
            width="medium",
            help="Rules engine reasons for **this** lap.",
        ),
    }
    if not slim_overview:
        col_cfg["Observed path (physical)"] = st.column_config.TextColumn(
            "Observed path (physical)",
            width="large",
            help="**This cycle** — raw stations.",
        )
        col_cfg["Process path (cleaned)"] = st.column_config.TextColumn(
            "Process path (cleaned)",
            width="medium",
            help="**This cycle** — PROCESS/PASS mainline abstraction.",
        )
    return col_cfg


def _render_station_matrix(
    *,
    title: str,
    parts_payload: list[tuple[str, list[dict], str]],
) -> None:
    """parts_payload: (part_id, filtered steps, flow_class short label)."""
    cols = part_station_matrix.mainline_track_columns()
    if title:
        st.subheader(title)
    st.caption(
        "✓ passed · ○ anchor · ↺ **mainline** cell re-entry after **upstream stage** (not mere physical return) · "
        "✕ SCRAP/FAIL · blank = not reached. ST21/22 & ST51 merged as one stage each. "
        "Empty under Process level = no processing events after filter."
    )
    mat: list[dict] = []
    for pid, stp, fcl in parts_payload:
        cells = part_station_matrix.cells_progress_matrix(stp, cols)
        row = {"part_id": pid, "flow_class": fcl}
        row.update(cells)
        mat.append(row)
    st.dataframe(mat, use_container_width=True, hide_index=True)


def _render_part_detail_block(
    *,
    part_id: str,
    steps: list[dict],
    info: dict,
    raw_steps: list[dict] | None = None,
) -> None:
    life = raw_steps if raw_steps is not None else steps
    st.markdown("##### Part `{}`".format(part_id))
    st.caption(
        "**Layers**: (1) Events → (2) **Cycles** (closed by **FINISH**; FAIL stays inside a lap) "
        "→ (3) **Part** = all laps. Below: **Cycle map** + per-lap table."
    )
    cmap: list[str] = []
    for i, seg in enumerate(part_track_model.production_cycle_segments(life)):
        closed = (
            seg
            and str(seg[-1].get("activity") or "").strip().upper() == "FINISH"
        )
        inf_map = flow_classification.classify_flow_from_steps(seg)
        cq = part_track_model.cycle_quality_label(seg, info=inf_map)
        dur = part_track_model.cycle_duration_display(seg)
        flag = "✔ Finished" if closed else "⏳ Running"
        cmap.append(
            "- **C{}** — {} · **{}** · {}".format(i + 1, flag, cq, dur)
        )
    if cmap:
        st.markdown("**Cycle map**\n\n" + "\n".join(cmap))
    st.code(part_track_model.format_journey_highlight_current(steps), language=None)
    st.caption(
        "Filtered **physical** line above = current UI view; `[ ]` = last step in that view."
    )
    _render_outcome_banner(part_track_model.classification_for_display_cycle(life))
    cols = part_station_matrix.mainline_track_columns()
    if cols:
        _render_progress_bar(cols, steps)
        short_label = _flow_matrix_label(info)
        with st.expander("7-stage matrix (this part)", expanded=False):
            _render_station_matrix(
                title="",
                parts_payload=[(part_id, steps, short_label)],
            )
    seg_rows = part_track_model.cycle_rows_for_detail(
        life, preferred_stations=twin_layout.LOGGED_COMPONENT_IDS
    )
    with st.expander(
        "Production cycles (FINISH-delimited)",
        expanded=len(seg_rows) > 1,
    ):
        st.caption(
            "**Per-cycle** path, **cycle time**, **FPY type** (First pass vs Reworked from FAILs), "
            "**FAIL count**, deviation, and **rework path** (❌ / FINISH✓)."
        )
        if seg_rows:
            st.dataframe(seg_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No events.")
    with st.expander("Trace scheme B (two lines)", expanded=False):
        st.code(
            flow_classification.format_trace_scheme_b(part_id, steps),
            language=None,
        )


def render_part_trace_panel(
    *,
    from_query_params: bool = False,
    use_page_session: bool = False,
    use_coordinated_twin_session: bool = False,
    coordinated_twin_session_id: str | None = None,
    kpi_for_replay: dict | None = None,
    twin_preloaded_parts: list[dict] | None = None,
    twin_preloaded_rows: list[dict] | None = None,
    twin_preloaded_session_id: str | None = None,
) -> None:
    bootstrap_part_trace_session_state(from_query_params=from_query_params)
    force_all_parts = use_page_session

    if not force_all_parts:
        st.caption(
            "**Flow & Rework** follow **mainline process stages** (fixed model). **Physical loops** "
            "(return belt / splitter / corner) are transport-only unless they coincide with **stage rollback**."
        )

    if force_all_parts:
        event_level = "Process level"
    else:
        event_level = st.radio(
            "Detail",
            ["Process level", "All events"],
            horizontal=True,
            index=0,
            key="pt_event_level",
        )

    if force_all_parts:
        if use_coordinated_twin_session:
            picked_session_id = coordinated_twin_session_id
        else:
            if st.session_state.get("cp_data_source") == "live":
                picked_session_id = mqtt_backend.physical_kpi_session_id()
            else:
                _rps = mqtt_backend.get_replay_pipeline_session_id()
                picked_session_id = _rps or st.session_state.get(
                    "dt_resolved_session"
                )
    else:
        sessions = neo4j_backend.list_recent_sessions(40)
        opts = [("Latest session (by start_time)", None)]
        for s in sessions:
            sid = s.get("id") or ""
            desc = (s.get("description") or "").strip()
            label = sid[:12] + ("..." if len(sid) > 12 else "")
            if desc:
                label = "{} · {}".format(label, desc[:24])
            opts.append((label, sid or None))

        ix = st.selectbox(
            "Session",
            range(len(opts)),
            format_func=lambda i: opts[i][0],
            key="trace_session_ix",
        )
        picked_session_id = opts[ix][1]

    if not force_all_parts:
        part_id = st.text_input(
            "Part ID (empty = session summary)",
            key="part_trace_part_id",
        )
        page_size = st.selectbox(
            "Steps per page", [10, 25, 50], index=0, key="pt_page_size"
        )
    else:
        part_id = ""
        page_size = 10

    if "trace_page" not in st.session_state:
        st.session_state.trace_page = 0

    if not force_all_parts and st.button("Query", key="pt_query_btn"):
        st.session_state.trace_page = 0

    flow_key = None if force_all_parts else (part_id.strip() or None)
    _k = kpi_for_replay
    if _k is None:
        _k, _ = mqtt_backend.get_kpi_snapshot()

    _use_twin_preload = (
        use_page_session
        and use_coordinated_twin_session
        and force_all_parts
        and (
            twin_preloaded_parts is not None
            or twin_preloaded_rows is not None
        )
    )
    if _use_twin_preload:
        parts = list(twin_preloaded_parts)
        data = {
            "parts": parts,
            "session_id": twin_preloaded_session_id or picked_session_id,
            "error": None,
        }
        err = data.get("error")
    else:
        data = neo4j_backend.query_part_flow(flow_key, picked_session_id)
        err = data.get("error")
        if err and not use_page_session:
            st.warning(err)
        if not force_all_parts:
            st.text(data.get("session_id") or "-")
        parts = data.get("parts") or []
        parts = part_track_conformance.filter_parts_to_replay_kpi_progress(
            parts, _k
        )
    pref = twin_layout.LOGGED_COMPONENT_IDS
    show_summary = force_all_parts or not part_id.strip()

    if show_summary:
        if use_page_session:
            if twin_preloaded_rows is not None:
                rows_pt = twin_preloaded_rows
            else:
                rows_pt = part_track_conformance.build_session_table_rows(parts)
            if rows_pt:
                _twin_flow_maybe_open_trace_from_query(rows_pt)
                render_digital_twin_flow_conformance_table(rows_pt)
                st.session_state[_DT_TRACE_ROWS_SESSION_KEY] = rows_pt
            else:
                st.session_state.pop(_DT_TRACE_ROWS_SESSION_KEY, None)
                render_digital_twin_flow_conformance_table([])
            return

        if not force_all_parts:
            st.caption(
                "Each row = **current lap** (FINISH-delimited). "
                "**Status** summarizes quality for this lap; expand **Part detail** for per-lap breakdown and events."
            )
        overview_rows: list[dict] = []
        path_rows: list[dict] = []
        payload: list[tuple[str, list[dict], str]] = []
        by_pid: dict[str, tuple[list[dict], list[dict], dict]] = {}
        for p in parts:
            raw_steps = list(p.get("steps") or [])
            steps = _filter_steps_for_level(raw_steps, event_level)
            info = flow_classification.classify_flow_from_steps(raw_steps)
            short_label = _flow_matrix_label(info)
            pid = str(p.get("part_id") or "")
            overview_rows.append(
                part_track_model.build_part_overview_row_mvp(
                    pid,
                    steps,
                    info,
                    preferred_stations=pref,
                    lifecycle_steps=raw_steps,
                )
            )
            path_rows.append(
                part_track_model.build_mvp_path_expander_row(
                    pid,
                    steps,
                    info,
                    lifecycle_steps=raw_steps,
                )
            )
            payload.append((pid, steps, short_label))
            by_pid[pid] = (steps, raw_steps, info)

        col_cfg = _part_track_mvp_column_config()

        st.markdown("##### Part overview")

        def _block_overview_and_detail() -> None:
            if overview_rows:
                st.dataframe(
                    overview_rows,
                    use_container_width=True,
                    hide_index=True,
                    column_config=col_cfg,
                )
            else:
                st.dataframe(
                    pd.DataFrame(columns=list(col_cfg.keys())),
                    use_container_width=True,
                    hide_index=True,
                    column_config=col_cfg,
                )
            _mvp_process_paths_expander(path_rows)
            detail_opts = ["—"] + sorted(by_pid.keys())
            pick = st.selectbox(
                "Part detail",
                detail_opts,
                key="pt_detail_pick",
            )
            if pick and pick != "—":
                stp, rwa, inf = by_pid[pick]
                _render_part_detail_block(
                    part_id=pick,
                    steps=stp,
                    info=inf,
                    raw_steps=rwa,
                )

        def _block_matrix() -> None:
            st.markdown("##### All parts · stage matrix")
            _render_station_matrix(title="", parts_payload=payload)

        _block_overview_and_detail()
        with st.expander("Stage matrix (same data as Progress column)", expanded=False):
            _block_matrix()
        return

    if len(parts) == 1:
        p = parts[0]
        raw_steps = list(p.get("steps") or [])
        steps = _filter_steps_for_level(raw_steps, event_level)
        info = flow_classification.classify_flow_from_steps(raw_steps)
        pid = str(p.get("part_id") or "")
        st.markdown("##### Overview")
        st.dataframe(
            [
                part_track_model.build_part_overview_row(
                    pid,
                    steps,
                    info,
                    preferred_stations=pref,
                    lifecycle_steps=raw_steps,
                )
            ],
            use_container_width=True,
            hide_index=True,
            column_config=_part_track_overview_column_config(
                slim_overview=False
            ),
        )
        _render_part_detail_block(
            part_id=pid, steps=steps, info=info, raw_steps=raw_steps
        )
        total = len(steps)
        n_pages = max(1, (total + page_size - 1) // page_size)
        page = min(st.session_state.trace_page, n_pages - 1)
        start = page * page_size
        chunk = steps[start : start + page_size]
        st.markdown("##### Event steps")
        for row_idx, step in enumerate(chunk):
            abs_n = start + row_idx + 1
            c0, c1, c2, c3 = st.columns([1, 2, 2, 2])
            with c0:
                if step.get("is_entry"):
                    st.markdown("**Entry**")
                elif step.get("is_exit"):
                    st.markdown("**Exit**")
                else:
                    st.write(abs_n)
            with c1:
                act = str(step.get("activity", ""))
                au = act.upper()
                if au == "FINISH":
                    st.success(act)
                elif au == "SCRAP":
                    st.error(act)
                else:
                    st.write(act)
            with c2:
                st.write(step.get("component_id", ""))
            with c3:
                st.text(str(step.get("time", "")))
        st.text("Page {}/{} · {} steps".format(page + 1, n_pages, total))
        bc1, bc2, _ = st.columns([1, 1, 4])
        if bc1.button("Previous", key="pt_prev_btn"):
            st.session_state.trace_page = max(0, page - 1)
            st.rerun()
        if bc2.button("Next", key="pt_next_btn"):
            st.session_state.trace_page = min(n_pages - 1, page + 1)
            st.rerun()
        st.text(p.get("flow", ""))
    else:
        st.caption("Part not found or no data in this session — tables stay visible.")
        _ocfg = _part_track_overview_column_config(slim_overview=False)
        st.markdown("##### Overview")
        st.dataframe(
            pd.DataFrame(columns=list(_ocfg.keys())),
            use_container_width=True,
            hide_index=True,
            column_config=_ocfg,
        )
        st.markdown("##### Event steps")
        st.dataframe(
            pd.DataFrame(columns=["#", "Activity", "Station", "Time"]),
            use_container_width=True,
            hide_index=True,
        )

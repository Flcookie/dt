"""Factory floor sim_state — incremental ``process_event_state`` (aligned with ``code/monitoring.py``)."""
from __future__ import annotations

from typing import Any

import streamlit as st

import twin.factory_floor_plotly as factory_floor_plotly
import services.neo4j_backend as neo4j_backend

_SESSION_KEY = "_dt_factory_floor_sim"


def empty_sim_state() -> dict[str, Any]:
    return {
        "queues": {},
        "part_locs": {},
        "part_states": {},
        "machines": {m["id"]: "IDLE" for m in factory_floor_plotly.machines_conf},
        "machine_parts": {},
        "kpi": {
            "fail_events": {},
            "block_events": {},
            "completed": 0,
            "scrapped": 0,
            "total_checkouts": 0,
            "first_start_time": None,
            "last_event_time": None,
        },
    }


def process_event_state(
    queues: dict,
    part_locations: dict,
    part_states: dict,
    machines: dict,
    machine_parts: dict,
    kpi: dict,
    event: dict,
) -> tuple[dict, dict, dict, dict, dict, dict]:
    """Port of ``code/monitoring.process_event_state`` (part / queue / machine updates only)."""
    part_id = event["part_id"]
    comp_id = str(event["component_id"]).strip()
    act = str(event["activity"]).strip()

    if "time" in event and event["time"] is not None:
        t_val = event["time"]
        kpi["last_event_time"] = t_val
        if comp_id == "corner2" and act == "START":
            if kpi.get("first_start_time") is None:
                kpi["first_start_time"] = t_val

    if comp_id == "splitter5" and act == "CHECKOUT":
        kpi["total_checkouts"] = kpi.get("total_checkouts", 0) + 1

    target_x, target_y, target_dir = factory_floor_plotly.get_sequence_info(comp_id, act)

    if target_x is not None:
        nk = (target_x, target_y)
        fixed_dir = factory_floor_plotly.NODE_FIXED_DIR.get(nk, target_dir)
        old_nk = part_locations.get(part_id)

        if old_nk is not None and old_nk != nk:
            if old_nk in queues and part_id in queues[old_nk]["parts"]:
                queues[old_nk]["parts"].remove(part_id)

        queues.setdefault(nk, {"parts": [], "dir": fixed_dir})
        queues[nk]["dir"] = fixed_dir

        if part_id not in queues[nk]["parts"]:
            queues[nk]["parts"].append(part_id)

        part_locations[part_id] = nk

    if act == "LOAD":
        machines[comp_id] = "BUSY"
        machine_parts[comp_id] = part_id
    elif machine_parts.get(comp_id) == part_id:
        if act in ("PROCESS", "UNLOAD"):
            machines[comp_id] = "BUSY"
        elif act in ("BLOCK",):
            machines[comp_id] = "BLOCK"
            kpi["block_events"][comp_id] = kpi["block_events"].get(comp_id, 0) + 1
            machine_parts[comp_id] = part_id
        elif "FAIL" in act:
            machines[comp_id] = "FAIL"
            kpi["fail_events"][comp_id] = kpi["fail_events"].get(comp_id, 0) + 1
            machine_parts[comp_id] = part_id
        elif act in ("TRANSFER",):
            machines[comp_id] = "IDLE"
            machine_parts.pop(comp_id, None)

    part_states.setdefault(part_id, False)
    if comp_id == "splitter5" and act in ("FINISH", "SCRAP"):
        part_states[part_id] = True
        if act == "FINISH":
            kpi["completed"] = kpi.get("completed", 0) + 1
        else:
            kpi["scrapped"] = kpi.get("scrapped", 0) + 1
    elif act == "START":
        part_states[part_id] = False

    return queues, part_locations, part_states, machines, machine_parts, kpi


def apply_event(sim: dict[str, Any], event: dict) -> None:
    pid = str(event.get("part_id") or "").strip()
    if not pid:
        return
    (
        sim["queues"],
        sim["part_locs"],
        sim["part_states"],
        sim["machines"],
        sim["machine_parts"],
        sim["kpi"],
    ) = process_event_state(
        sim["queues"],
        sim["part_locs"],
        sim["part_states"],
        sim["machines"],
        sim["machine_parts"],
        sim["kpi"],
        event,
    )


def replay_events(sim: dict[str, Any], events: list[dict]) -> None:
    for ev in events:
        apply_event(sim, ev)


def cursor_after_events(events: list[dict]) -> tuple[float | None, str]:
    """Exclusive cursor ``(timestamp, event_id)`` after applying ``events`` (may be empty)."""
    if not events:
        return None, ""
    last = events[-1]
    try:
        ts = float(last.get("timestamp"))
    except (TypeError, ValueError):
        return None, str(last.get("event_id") or "")
    return ts, str(last.get("event_id") or "")


def events_after_cursor(
    events: list[dict],
    cursor_ts: float | None,
    cursor_id: str,
) -> list[dict]:
    """Python mirror of Neo4j cursor filter (for tests / dedup checks)."""
    cid = cursor_id or ""
    out: list[dict] = []
    for ev in sorted(
        events,
        key=lambda e: (float(e.get("timestamp") or 0), str(e.get("event_id") or "")),
    ):
        try:
            ts = float(ev.get("timestamp"))
        except (TypeError, ValueError):
            continue
        eid = str(ev.get("event_id") or "")
        if cursor_ts is None:
            out.append(ev)
        elif ts > cursor_ts or (ts == cursor_ts and eid > cid):
            out.append(ev)
    return out


def display_pallets_from_sim(sim: dict[str, Any]) -> list[dict]:
    """同节点多 Part 合并为 ``+N``（与 ``factory_floor_plotly._pallet_positions`` 一致）。

    FINISH/SCRAP 零件仍留在图上，空托盘色 ``#b8956a``。
    """
    from collections import defaultdict

    part_states = sim.get("part_states") or {}
    groups: dict[tuple[float, float], list[str]] = defaultdict(list)
    for (qx, qy), q_data in (sim.get("queues") or {}).items():
        for pid in q_data.get("parts") or []:
            pid = str(pid or "").strip()
            if pid:
                groups[(round(float(qx), 5), round(float(qy), 5))].append(pid)

    display_pallets: list[dict] = []
    for (qx, qy), pids in groups.items():
        pids = sorted(set(pids), key=lambda x: x.lower())
        all_empty = all(part_states.get(p, False) for p in pids)
        color = "#b8956a" if all_empty else "#7a4f2a"
        text = pids[0] if len(pids) <= 1 else "+{}".format(len(pids))
        hover = ", ".join(pids)
        display_pallets.append(
            {"x": qx, "y": qy, "id": text, "hover": hover, "color": color}
        )
    return display_pallets


def _replay_cap_unix(kpi: dict | None) -> float | None:
    if not isinstance(kpi, dict) or (kpi.get("run_mode") or "") != "replay":
        return None
    try:
        cap = float(kpi.get("chart_time_unix") or 0.0)
    except (TypeError, ValueError):
        return None
    return cap if cap > 0.0 else None


def _needs_reset(bundle: dict[str, Any], session_id: str, mode: str, replay_cap: float | None) -> bool:
    if bundle.get("session_id") != session_id or bundle.get("mode") != mode:
        return True
    if mode != "replay":
        return False
    old_cap = bundle.get("replay_cap")
    if replay_cap is None:
        return old_cap is not None
    if old_cap is None:
        return True
    return replay_cap < old_cap - 1e-4


def _fetch_events(
    session_id: str,
    *,
    cursor_ts: float | None,
    cursor_id: str,
    until_ts: float | None,
) -> list[dict]:
    return neo4j_backend.fetch_session_events_for_floor(
        session_id,
        since_ts=cursor_ts,
        since_event_id=cursor_id,
        until_ts=until_ts,
    )


def sync_factory_floor_sim(
    session_id: str | None,
    *,
    is_replay: bool,
    kpi: dict | None,
    neo_connected: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Maintain ``sim_state`` from Neo4j event stream (LIVE incremental / replay stepped).

    Returns ``(sim_state, status_message)``. No silent ``station_live`` fallback.
    """
    sid = (session_id or "").strip()
    mode = "replay" if is_replay else "live"
    replay_cap = _replay_cap_unix(kpi) if is_replay else None

    if not neo_connected:
        st.session_state.pop(_SESSION_KEY, None)
        return None, "Neo4j not connected — cannot load part positions."

    if not sid:
        st.session_state.pop(_SESSION_KEY, None)
        return None, None

    bundle: dict[str, Any] = dict(st.session_state.get(_SESSION_KEY) or {})
    reset = _needs_reset(bundle, sid, mode, replay_cap)

    if reset:
        st.session_state.pop(_SESSION_KEY, None)
        sim = empty_sim_state()
        until = replay_cap if mode == "replay" and replay_cap is not None else None
        events = _fetch_events(sid, cursor_ts=None, cursor_id="", until_ts=until)
        replay_events(sim, events)
        cursor_ts, cursor_id = cursor_after_events(events)
        bundle = {
            "session_id": sid,
            "mode": mode,
            "replay_cap": replay_cap,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "sim": sim,
        }
        st.session_state[_SESSION_KEY] = bundle
        return sim, None

    sim = bundle.get("sim")
    if not isinstance(sim, dict):
        sim = empty_sim_state()
        bundle["sim"] = sim

    cursor_ts = bundle.get("cursor_ts")
    cursor_id = str(bundle.get("cursor_id") or "")

    if mode == "replay":
        if replay_cap is None:
            st.session_state[_SESSION_KEY] = bundle
            return sim, None
        old_cap = float(bundle.get("replay_cap") or -1.0)
        if replay_cap > old_cap + 1e-4:
            events = _fetch_events(
                sid,
                cursor_ts=cursor_ts if cursor_ts is not None else None,
                cursor_id=cursor_id,
                until_ts=replay_cap,
            )
            if events:
                replay_events(sim, events)
                nts, nid = cursor_after_events(events)
                if nts is not None:
                    bundle["cursor_ts"] = nts
                    bundle["cursor_id"] = nid
            bundle["replay_cap"] = replay_cap
            bundle["sim"] = sim
    else:
        events = _fetch_events(
            sid,
            cursor_ts=cursor_ts if cursor_ts is not None else None,
            cursor_id=cursor_id,
            until_ts=None,
        )
        if events:
            replay_events(sim, events)
            nts, nid = cursor_after_events(events)
            if nts is not None:
                bundle["cursor_ts"] = nts
                bundle["cursor_id"] = nid
            bundle["sim"] = sim

    st.session_state[_SESSION_KEY] = bundle
    return sim, None

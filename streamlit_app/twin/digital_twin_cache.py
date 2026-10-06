"""Digital Twin fragment caches — avoid redundant Neo4j queries and part-trace replays."""
from __future__ import annotations

from typing import Any

import streamlit as st

import services.neo4j_backend as neo4j_backend
import part_track.part_track_conformance as ptc

_FLOOR_SESSION_KEY = "_dt_factory_floor_sim"
_PART_FLOW_CACHE_KEY = "_dt_part_flow_cache"
_PART_ROWS_CACHE_KEY = "_dt_part_trace_rows_cache"


def _floor_bundle() -> dict[str, Any]:
    raw = st.session_state.get(_FLOOR_SESSION_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _replay_chart_unix(kpi: dict | None) -> float:
    if not isinstance(kpi, dict) or (kpi.get("run_mode") or "") != "replay":
        return 0.0
    try:
        return float(kpi.get("chart_time_unix") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _part_flow_query_key(
    session_id: str,
    *,
    is_replay: bool,
    bundle: dict[str, Any],
) -> tuple[Any, ...]:
    """Neo4j ``query_part_flow`` invalidation: LIVE follows floor cursor; replay once per session."""
    if is_replay:
        return (session_id, "replay")
    return (
        session_id,
        "live",
        bundle.get("cursor_ts"),
        str(bundle.get("cursor_id") or ""),
    )


def _parts_fingerprint(parts: list[dict]) -> tuple[int, int]:
    n = len(parts or [])
    steps = sum(len(p.get("steps") or []) for p in (parts or []))
    return n, steps


def _rows_cache_key(
    query_key: tuple[Any, ...],
    parts: list[dict],
    *,
    chart_unix: float,
) -> tuple[Any, ...]:
    n, steps = _parts_fingerprint(parts)
    return query_key + (n, steps, round(chart_unix, 4))


def resolve_twin_part_trace(
    session_id: str | None,
    *,
    is_replay: bool,
    kpi: dict | None,
    neo_connected: bool,
) -> tuple[list[dict], list[dict], str | None]:
    """Return ``(filtered_parts, table_rows, session_id)`` with session-level caching."""
    if not neo_connected:
        st.session_state.pop(_PART_FLOW_CACHE_KEY, None)
        st.session_state.pop(_PART_ROWS_CACHE_KEY, None)
        return [], [], None

    sid = (session_id or "").strip()
    if not sid:
        st.session_state.pop(_PART_FLOW_CACHE_KEY, None)
        st.session_state.pop(_PART_ROWS_CACHE_KEY, None)
        return [], [], None

    bundle = _floor_bundle()
    query_key = _part_flow_query_key(sid, is_replay=is_replay, bundle=bundle)
    flow_cache: dict[str, Any] = dict(st.session_state.get(_PART_FLOW_CACHE_KEY) or {})

    if flow_cache.get("query_key") != query_key:
        pd = neo4j_backend.query_part_flow(None, sid)
        if pd.get("error"):
            st.session_state.pop(_PART_FLOW_CACHE_KEY, None)
            st.session_state.pop(_PART_ROWS_CACHE_KEY, None)
            return [], [], pd.get("session_id")
        flow_cache = {
            "query_key": query_key,
            "session_id": pd.get("session_id") or sid,
            "parts_raw": list(pd.get("parts") or []),
        }
        st.session_state[_PART_FLOW_CACHE_KEY] = flow_cache
        st.session_state.pop(_PART_ROWS_CACHE_KEY, None)

    parts_raw = list(flow_cache.get("parts_raw") or [])
    resolved_sid = str(flow_cache.get("session_id") or sid)
    chart_unix = _replay_chart_unix(kpi)
    parts = (
        ptc.filter_parts_to_replay_kpi_progress(parts_raw, kpi)
        if is_replay
        else parts_raw
    )

    rows_key = _rows_cache_key(query_key, parts, chart_unix=chart_unix)
    rows_cache: dict[str, Any] = dict(st.session_state.get(_PART_ROWS_CACHE_KEY) or {})
    if rows_cache.get("rows_key") == rows_key and isinstance(rows_cache.get("rows"), list):
        return parts, list(rows_cache["rows"]), resolved_sid

    rows = ptc.build_session_table_rows(parts)
    st.session_state[_PART_ROWS_CACHE_KEY] = {"rows_key": rows_key, "rows": rows}
    return parts, rows, resolved_sid

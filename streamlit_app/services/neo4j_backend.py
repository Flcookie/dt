"""Neo4j driver + queries aligned with neo4j_writer schema and web_api part_flow."""
from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import sys
import threading

_log = logging.getLogger(__name__)

from paths import ensure_paths

ensure_paths()
import common  # noqa: E402

# 与改进2 / 导师 process 视角一致的粗粒度活动（不含 TRANSFER、BLOCK 等）
PROCESS_LEVEL_ACTIVITIES = frozenset(
    {"START", "LOAD", "PROCESS", "UNLOAD", "FINISH", "SCRAP"}
)

def natural_part_id_key(pid: str) -> tuple:
    s = (pid or "").strip()
    m = re.match(r"^[Pp]?(\d+)$", s)
    if m:
        return (0, int(m.group(1)), s.lower())
    return (1, 0, s.lower())


def _overview_part_id_seed_from_config() -> list[str]:
    """Optional extra part IDs for overview rows (e.g. placeholders before first event).

    Set ``part_track_overview_ids`` in config to a list or comma-separated string.
    If unset or empty list, **no** seed — overview = only parts already in Neo4j for this session.
    """
    cfg = common.load_config()
    raw = cfg.get("part_track_overview_ids")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def merge_parts_with_overview_seed(parts: list[dict], *, single: bool) -> list[dict]:
    """Union Neo4j parts with optional configured seed IDs (placeholder rows: empty steps)."""
    if single:
        return parts
    seed = _overview_part_id_seed_from_config()
    if not seed:
        return sorted(parts, key=lambda p: natural_part_id_key(str(p["part_id"])))
    seen = {str(p["part_id"]): p for p in parts}
    union_ids = sorted(set(seen.keys()) | set(seed), key=natural_part_id_key)
    out: list[dict] = []
    for pid in union_ids:
        if pid in seen:
            out.append(seen[pid])
        else:
            out.append({"part_id": pid, "steps": [], "flow": ""})
    return out


def _lifecycle_label(activity: str) -> str:
    a = (activity or "").strip().upper()
    if a in ("START", "LOAD"):
        return "entry"
    if a in ("FINISH", "SCRAP"):
        return "exit"
    return "process"


from neo4j import GraphDatabase

_driver = None
_lock = threading.Lock()


def get_driver():
    global _driver
    with _lock:
        if _driver is None:
            cfg = common.load_config("config.json")["neo4j"]
            #  acquisition 过长会导致每页侧栏查 Neo4j 时长时间卡住（误以为 Streamlit 慢）
            _driver = GraphDatabase.driver(
                cfg["uri"],
                auth=(cfg["username"], cfg["password"]),
                max_connection_pool_size=50,
                connection_timeout=5.0,
                connection_acquisition_timeout=5.0,
            )
        return _driver


def close_driver():
    global _driver
    with _lock:
        if _driver:
            try:
                _driver.close()
            except Exception:
                pass
            _driver = None


def neo4j_status(*, include_event_count: bool = True) -> dict:
    try:
        d = get_driver()
        with d.session() as s:
            if include_event_count:
                r = s.run("MATCH (e:Event) RETURN count(e) AS c")
                rec = r.single()
                n = rec["c"] if rec else 0
                return {"connected": True, "event_count": n}
            s.run("RETURN 1 AS ok").consume()
        return {"connected": True}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def neo4j_ping() -> dict:
    """Light connectivity probe for high-frequency UI refresh (no graph scan)."""
    return neo4j_status(include_event_count=False)


def _clear_all_app_graph_tx(tx):
    r = tx.run("MATCH (e:Event) RETURN count(e) AS c")
    rec = r.single()
    n_event = rec["c"] if rec else 0
    r = tx.run("MATCH (s:Session) RETURN count(s) AS c")
    rec = r.single()
    n_session = rec["c"] if rec else 0
    tx.run("MATCH (e:Event) DETACH DELETE e")
    tx.run("MATCH (s:Session) DETACH DELETE s")
    tx.run("MATCH (n:Station) DETACH DELETE n")
    tx.run("MATCH (n:Entity) DETACH DELETE n")
    tx.run("MATCH (n:EntityType) DETACH DELETE n")
    tx.run("MATCH (n:Activity) DETACH DELETE n")
    return {"events": n_event, "sessions": n_session}


def clear_neo4j_graph():
    d = get_driver()
    with d.session() as session:
        counts = session.execute_write(_clear_all_app_graph_tx)
    return counts


def get_latest_session_info():
    try:
        d = get_driver()
        with d.session() as session:
            r = session.run(
                """
                MATCH (s:Session)
                OPTIONAL MATCH (e:Event)-[:IN_SESSION]->(s)
                WITH s, max(e.timestamp) AS mt
                RETURN s.id AS session_id
                ORDER BY mt DESC NULLS LAST, s.id DESC
                LIMIT 1
                """
            )
            rec = r.single()
            if rec:
                return rec["session_id"], ""
            return None, None
    except Exception:
        return None, None


def list_sessions_enriched(limit: int = 40) -> list[dict]:
    """Sessions for History / Replay / Export: counts, fallback end_time, labels."""
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (s:Session)
                OPTIONAL MATCH (e:Event)-[:IN_SESSION]->(s)
                WITH s,
                  count(e) AS ec,
                  min(e.time) AS first_ev_time,
                  max(e.time) AS last_ev_time,
                  min(e.timestamp) AS first_ts,
                  max(e.timestamp) AS last_ts
                RETURN s.id AS id,
                  s.start_time AS start_prop,
                  s.end_time AS end_prop,
                  ec AS event_count_live,
                  first_ev_time,
                  last_ev_time,
                  first_ts,
                  last_ts
                ORDER BY coalesce(last_ts, first_ts, 0.0) DESC
                LIMIT $lim
                """,
                lim=int(limit),
            )
            rows = []
            for rec in r:
                dct = dict(rec)
                rows.append(_normalize_session_row(dct))
            return rows
    except Exception:
        return []


def _normalize_session_row(dct: dict) -> dict:
    """Build display fields; coalesce end_time with last event time."""
    ec = int(dct.get("event_count_live") or 0)
    start_prop = dct.get("start_prop")
    end_prop = dct.get("end_prop")
    ft = dct.get("first_ev_time") or ""
    lt = dct.get("last_ev_time") or ""
    start_disp = _session_time_to_str(start_prop) or str(ft).strip() or ""
    end_stored = _session_time_to_str(end_prop) if end_prop else ""
    end_disp = end_stored or str(lt).strip() or ""
    if not end_disp and dct.get("last_ts") is not None:
        try:
            end_disp = datetime.datetime.fromtimestamp(
                float(dct["last_ts"])
            ).isoformat(sep="T", timespec="seconds")
        except (TypeError, ValueError, OSError):
            pass
    has_end_session = bool(end_stored)
    sid = str(dct.get("id") or "")
    live_interrupted = not has_end_session
    st = "completed" if has_end_session else "open"
    label = _format_session_label(
        sid,
        start_disp,
        end_disp,
        ec,
        live_interrupted,
    )
    dct["event_count"] = ec
    dct["start_display"] = start_disp
    dct["end_display"] = end_disp
    dct["status_badge"] = st
    dct["live_interrupted"] = live_interrupted
    dct["label"] = label
    dct["description"] = ""
    return dct


def _session_time_to_str(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "iso_format"):
        try:
            return str(v.iso_format())
        except Exception:
            pass
    s = str(v).strip()
    return s


def _parse_display_datetime(s: str) -> datetime.datetime | None:
    """Parse session / event time strings for minute-level labels (no timezone shown)."""
    raw = str(s or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if len(raw) > 10 and raw[10] == " ":
        raw = raw[:10] + "T" + raw[11:]
    try:
        return datetime.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def canonical_session_time_str(raw: str) -> str:
    """Normalize CSV/event time for Session metadata and duplicate fingerprint (stable string)."""
    s = str(raw or "").strip()
    if not s:
        return ""
    dt = _parse_display_datetime(s)
    if dt is None:
        return s
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if dt.microsecond:
        return dt.isoformat(sep="T")
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def session_time_fingerprint_match(stored: str, csv_time: str) -> bool:
    """True if graph/Session time string refers to the same instant as CSV first event."""
    a = str(stored or "").strip()
    b = str(csv_time or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ca = canonical_session_time_str(a)
    cb = canonical_session_time_str(b)
    if ca and cb and ca == cb:
        return True
    ta = _parse_event_time_key(a)
    tb = _parse_event_time_key(b)
    if ta > 0 and tb > 0 and abs(ta - tb) < 0.5:
        return True
    return False


def _format_session_range_minute(start_s: str, end_s: str) -> str:
    s_dt = _parse_display_datetime(start_s)
    e_dt = _parse_display_datetime(end_s)
    if not s_dt:
        return "? → ?"
    if not e_dt:
        e_dt = s_dt
    d0 = s_dt.strftime("%Y-%m-%d")
    t0 = s_dt.strftime("%H:%M")
    if e_dt.date() == s_dt.date():
        return "{}  {} → {}".format(d0, t0, e_dt.strftime("%H:%M"))
    return "{}  {} → {}  {}".format(
        d0, t0, e_dt.strftime("%Y-%m-%d"), e_dt.strftime("%H:%M")
    )


def _format_session_label(
    session_id: str,
    start_s: str,
    end_s: str,
    ec: int,
    live_interrupted: bool,
) -> str:
    sid = str(session_id or "")
    if sid.startswith("event_log_replay_"):
        tag = "[replay]"
    else:
        tag = "[session]"
    mid = _format_session_range_minute(start_s, end_s)
    core = "{}  {}  ({} events)".format(tag, mid, ec)
    if live_interrupted:
        return "{}  \u26a0 open".format(core)
    return core


def list_recent_sessions(limit: int = 40) -> list[dict]:
    """Recent sessions for Twin / trace selectors (id + description + start hint)."""
    enriched = list_sessions_enriched(limit)
    return [
        {
            "id": r["id"],
            "description": r.get("description") or "",
            "start_time": r.get("start_display") or r["id"],
        }
        for r in enriched
    ]


def find_csv_import_duplicate_info(
    first_event_time: str, event_count: int
) -> dict | None:
    """If some session already has the same first event time + event count, return its metadata.

    Uses the **first Event** in the session (by timestamp), not only Session.start_time, so detection
    still works when start_time was wrongly set to import wall-clock time.
    """
    ft = str(first_event_time).strip()
    try:
        n = int(event_count)
    except (TypeError, ValueError):
        return None
    if not ft or n <= 0:
        return None
    try:
        d = get_driver()
        with d.session() as s:
            cands = s.run(
                """
                MATCH (sess:Session)
                MATCH (e:Event)-[:IN_SESSION]->(sess)
                WITH sess, count(e) AS ec
                WHERE ec = $n
                MATCH (e2:Event)-[:IN_SESSION]->(sess)
                WITH sess, e2
                ORDER BY e2.timestamp ASC
                WITH sess, collect(e2)[0] AS first_e
                WHERE first_e IS NOT NULL
                RETURN sess.id AS id, sess.start_time AS start_prop, first_e.time AS first_ev_time
                """,
                n=n,
            )
            for rec in cands:
                sid = rec.get("id")
                if not sid:
                    continue
                fe = rec.get("first_ev_time")
                fe_s = _session_time_to_str(fe) if fe is not None else ""
                if fe_s and session_time_fingerprint_match(fe_s, ft):
                    return {
                        "id": str(sid),
                        "start_time": _session_time_to_str(rec.get("start_prop")),
                        "source_file": "",
                    }
    except Exception:
        return None
    return None


def find_csv_import_duplicate(first_event_time: str, event_count: int) -> str | None:
    """Return existing Session id if first event + event_count match (csv_import fingerprint)."""
    info = find_csv_import_duplicate_info(first_event_time, event_count)
    return info["id"] if info and info.get("id") else None


def count_session_events(session_id: str) -> int:
    """Fast IN_SESSION event count for one session (replay gate / UI validation)."""
    sid = (session_id or "").strip()
    if not sid:
        return 0
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $sid})
                RETURN count(e) AS c
                """,
                sid=sid,
            )
            rec = r.single()
            return int(rec["c"]) if rec and rec["c"] is not None else 0
    except Exception as ex:
        _log.warning("count_session_events(%s) failed: %s", sid, ex)
        return -1


def fetch_session_events_log_format(session_id: str) -> list[dict]:
    """Ordered events as dicts compatible with KPI / CSV (time, component_id, part_id, activity)."""
    sid = (session_id or "").strip()
    if not sid:
        return []
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $sid})
                OPTIONAL MATCH (e)-[:OCCURRED_AT]->(st:Station)
                OPTIONAL MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                OPTIONAL MATCH (e)-[:ACTS_ON]->(en:Entity)
                RETURN coalesce(
                    nullif(e.time, ''),
                    CASE
                      WHEN e.timestamp IS NOT NULL
                      THEN toString(
                        datetime({epochSeconds: toInteger(toFloat(e.timestamp))})
                      )
                      ELSE ''
                    END
                  ) AS time,
                  coalesce(e.component_id, st.sysId, '') AS component_id,
                  coalesce(e.part_id, en.sysId, '') AS part_id,
                  coalesce(e.activity, a.name, '') AS activity,
                  e.timestamp AS ts
                ORDER BY e.timestamp ASC
                """,
                sid=sid,
            )
            out = []
            for rec in r:
                out.append(
                    {
                        "time": _event_time_to_kpi_iso(rec["time"]),
                        "component_id": str(rec["component_id"] or "").strip(),
                        "part_id": str(rec["part_id"] or "").strip(),
                        "activity": str(rec["activity"] or "").strip(),
                    }
                )
            return out
    except Exception as ex:
        _log.warning("fetch_session_events_log_format(%s) failed: %s", sid, ex)
        return []


def fetch_session_events_for_floor(
    session_id: str,
    *,
    since_ts: float | None = None,
    since_event_id: str | None = None,
    until_ts: float | None = None,
) -> list[dict]:
    """Ordered session events for factory floor ``process_event_state``.

    Cursor ``(since_ts, since_event_id)`` is exclusive when both set.
    ``until_ts`` is inclusive when set.
    """
    sid = (session_id or "").strip()
    if not sid:
        return []
    try:
        since = float(since_ts) if since_ts is not None else None
    except (TypeError, ValueError):
        since = None
    try:
        until = float(until_ts) if until_ts is not None else None
    except (TypeError, ValueError):
        until = None
    since_id = (since_event_id or "").strip() or None
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $sid})
                OPTIONAL MATCH (e)-[:OCCURRED_AT]->(st:Station)
                OPTIONAL MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                OPTIONAL MATCH (e)-[:ACTS_ON]->(en:Entity)
                WHERE ($until_ts IS NULL OR e.timestamp <= $until_ts)
                  AND (
                    $since_ts IS NULL
                    OR e.timestamp > $since_ts
                    OR (
                      e.timestamp = $since_ts
                      AND coalesce(e.id, '') > coalesce($since_id, '')
                    )
                  )
                RETURN coalesce(
                    e.time,
                    toString(datetime({epochSeconds: toInteger(toFloat(e.timestamp))}))
                  ) AS time,
                  coalesce(e.component_id, st.sysId, '') AS component_id,
                  coalesce(e.part_id, en.sysId, '') AS part_id,
                  coalesce(e.activity, a.name, '') AS activity,
                  e.timestamp AS ts,
                  e.id AS event_id
                ORDER BY e.timestamp ASC, e.id ASC
                """,
                sid=sid,
                since_ts=since,
                since_id=since_id,
                until_ts=until,
            )
            out: list[dict] = []
            for rec in r:
                ts_raw = rec.get("ts")
                if ts_raw is None:
                    continue
                try:
                    ts_val = float(ts_raw)
                except (TypeError, ValueError):
                    continue
                out.append(
                    {
                        "time": _event_time_to_kpi_iso(rec.get("time")),
                        "component_id": str(rec.get("component_id") or "").strip(),
                        "part_id": str(rec.get("part_id") or "").strip(),
                        "activity": str(rec.get("activity") or "").strip(),
                        "timestamp": ts_val,
                        "event_id": str(rec.get("event_id") or ""),
                    }
                )
            return out
    except Exception:
        return []


def import_csv_session(
    rows: list[dict],
    source_filename: str,
    *,
    force_new_id: bool = False,
    display_name: str | None = None,
) -> tuple[str, int, str | None]:
    """Create csv_import Session and write events. Returns (session_id, n_events, duplicate_of_or_none)."""
    import neo4j_writer

    if not rows:
        raise ValueError("empty rows")
    sorted_rows = sorted(
        rows,
        key=lambda r: _parse_event_time_key(r.get("time")),
    )
    first_raw = str(sorted_rows[0].get("time") or "").strip()
    last_raw = str(sorted_rows[-1].get("time") or "").strip()
    if not first_raw:
        raise ValueError("first event has empty time")
    first_t = canonical_session_time_str(first_raw) or first_raw
    last_t = (
        canonical_session_time_str(last_raw) or last_raw or first_t
    )
    n = len(sorted_rows)
    dup = find_csv_import_duplicate(first_raw, n)
    if dup and not force_new_id:
        return dup, n, dup
    dt0 = _parse_display_datetime(first_raw)
    if dt0:
        base_id = common.new_event_log_session_id(dt0)
    else:
        base_id = common.new_event_log_session_id()
    sid = base_id
    if force_new_id and dup:
        sid = "{}_{}".format(base_id, int(datetime.datetime.now().timestamp()) % 100000)
    dn = (display_name or "").strip() or None
    neo4j_writer.start_session(
        sid,
        "csv_import",
        start_time_iso=first_t,
        end_time_iso=None,
        event_count=0,
        source_file=source_filename or "",
        status="running",
        display_name=dn,
    )
    batch = 250
    for i in range(0, n, batch):
        neo4j_writer.write_events_batch(sorted_rows[i : i + batch], sid)
    neo4j_writer.finalize_session(
        sid,
        last_t or datetime.datetime.now().isoformat(),
        status="completed",
        event_count=n,
    )
    return sid, n, None


def _parse_event_time_key(time_str) -> float:
    try:
        return datetime.datetime.fromisoformat(str(time_str).strip()).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _event_time_to_kpi_iso(val) -> str:
    """Normalize Neo4j driver / Cypher time values for KpiCalculator (ISO-8601)."""
    if val is None:
        return ""
    if not isinstance(val, str) and callable(getattr(val, "isoformat", None)):
        try:
            val = val.isoformat()
        except Exception:
            val = str(val)
    s = str(val).strip()
    if not s:
        return ""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) > 10 and s[10] == " ":
        s = s[:10] + "T" + s[11:]
    return s


def export_session_events_csv(session_id: str) -> str:
    evs = fetch_session_events_log_format(session_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "component_id", "part_id", "activity"])
    for e in evs:
        w.writerow(
            [
                e.get("time", ""),
                e.get("component_id", ""),
                e.get("part_id", ""),
                e.get("activity", ""),
            ]
        )
    # UTF-8 BOM so Excel on Windows splits columns correctly
    return "\ufeff" + buf.getvalue()


def get_session_kpi_snapshot(session_id: str | None = None) -> dict | None:
    """KPI from all events in a Neo4j session (offline). ``session_id`` None → latest session.

    Used when MQTT / ``.replay_kpi.json`` are unavailable (e.g. imported CSV, no worker running).
    """
    try:
        sid, _ = _resolve_session(session_id)
        if not sid:
            return None
        snap = _kpi_snapshot_for_session(sid)
        if not isinstance(snap, dict):
            return None
        out = dict(snap)
        out["run_mode"] = "history"
        out["session_id"] = sid
        return out
    except Exception:
        return None


def _kpi_snapshot_for_session(session_id: str) -> dict:
    """Build KPI for a session: read ordered events from Neo4j, replay via KpiCalculator, return get_snapshot().

    This is always computed on download (not read from MQTT or a stale sidecar file). If metrics are all zero
    but observation_time_sec > 0, events likely had empty activity/part_id in the graph — ensure Event rows
    or OF_ACTIVITY / ACTS_ON links match fetch_session_events_log_format.
    """
    import kpi_calculator as kc_mod

    evs = fetch_session_events_log_format(session_id)
    cfg = common.load_config("config.json")
    kcfg = cfg.get("kpi_config") or {}
    k = kc_mod.KpiCalculator(
        observation_time_mode="replay",
        finish_events=kcfg.get("finish_events", ["FINISH"]),
        scrap_events=kcfg.get("scrap_events", ["SCRAP"]),
    )
    for ev in evs:
        k.on_event(ev)
    return k.get_snapshot()


def export_session_kpi_log_csv(session_id: str) -> str:
    """Single KPI log: long-form CSV with system, stage, and station metrics."""
    snap = _kpi_snapshot_for_session(session_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["level", "entity", "metric", "value"])
    sysb = snap.get("system") or {}
    for key in sorted(sysb.keys()):
        w.writerow(["system", "", key, sysb[key]])
    w.writerow(["system", "", "observation_time_sec", snap.get("observation_time_sec", "")])
    w.writerow(["system", "", "simulation_time_iso", snap.get("simulation_time_iso", "")])
    stg = snap.get("stages") or {}
    for sk in sorted(stg.keys(), key=lambda x: int(str(x).replace("stage", "") or 0)):
        row = stg[sk]
        for mk in sorted(row.keys()):
            w.writerow(["stage", sk, mk, row[mk]])
    util = snap.get("utilization") or {}
    stprob = snap.get("state_probability") or {}
    slive = snap.get("station_live") or {}
    all_station_ids = sorted(set(util.keys()) | set(stprob.keys()) | set(slive.keys()))
    for stn in all_station_ids:
        w.writerow(["station", stn, "utilization", util.get(stn, "")])
        probs = stprob.get(stn) or {}
        for pk in sorted(probs.keys()):
            w.writerow(["station", stn, "state_prob_{}".format(pk), probs[pk]])
        live = slive.get(stn) or {}
        for lk in sorted(live.keys()):
            w.writerow(["station", stn, lk, live[lk]])
    return "\ufeff" + buf.getvalue()


def export_session_kpi_csv_pair(session_id: str) -> tuple[str, str]:
    """Return (system_kpi_csv, stages_kpi_csv) from KpiCalculator replay over session events."""
    snap = _kpi_snapshot_for_session(session_id)
    sysb = snap.get("system") or {}
    sys_buf = io.StringIO()
    sw = csv.writer(sys_buf)
    sw.writerow(
        [
            "num_completions",
            "num_scraps",
            "wip_instantaneous",
            "wip_average",
            "complete_rate",
            "scrap_rate",
            "avg_cycle_time_fin",
            "avg_cycle_time_all",
            "observation_time_sec",
        ]
    )
    sw.writerow(
        [
            sysb.get("num_completions", 0),
            sysb.get("num_scraps", 0),
            sysb.get("wip_instantaneous", 0),
            sysb.get("wip_average", 0),
            sysb.get("complete_rate", 0),
            sysb.get("scrap_rate", 0),
            sysb.get("avg_cycle_time_fin", 0),
            sysb.get("avg_cycle_time_all", 0),
            snap.get("observation_time_sec", 0),
        ]
    )
    stg_buf = io.StringIO()
    tw = csv.writer(stg_buf)
    tw.writerow(
        [
            "stage",
            "wip_instantaneous",
            "wip_average",
            "num_departures",
            "throughput",
            "avg_flow_time",
        ]
    )
    stg = snap.get("stages") or {}
    for key in sorted(stg.keys(), key=lambda x: int(str(x).replace("stage", "") or 0)):
        row = stg[key]
        tw.writerow(
            [
                key,
                row.get("wip_instantaneous", 0),
                row.get("wip_average", 0),
                row.get("num_departures", 0),
                row.get("throughput", 0),
                row.get("avg_flow_time", 0),
            ]
        )
    return sys_buf.getvalue(), stg_buf.getvalue()


def finalize_live_session(session_id: str, end_iso: str | None = None) -> None:
    """UI / Stop: close live session in Neo4j (separate from main_service process)."""
    import neo4j_writer

    sid = (session_id or "").strip()
    if not sid:
        return
    end = end_iso or datetime.datetime.now().isoformat()
    neo4j_writer.finalize_session(sid, end, status="completed")


def _resolve_session(
    session_id: str | None,
) -> tuple[str | None, str]:
    """Return (session_id, session_description) or (None, ''). Session nodes have no description property."""
    if session_id and session_id.strip():
        sid = session_id.strip()
        try:
            d = get_driver()
            with d.session() as s:
                r = s.run(
                    "MATCH (x:Session {id: $id}) RETURN x.id AS ok",
                    id=sid,
                )
                rec = r.single()
                if rec:
                    return sid, ""
                return None, ""
        except Exception:
            return None, ""
    return get_latest_session_info()


def query_part_flow(part_id: str | None, session_id: str | None = None) -> dict:
    """Part flow for one Session (latest if session_id is None) or top parts summary."""
    try:
        d = get_driver()
        session_id, session_description = _resolve_session(session_id)
        if not session_id:
            return {
                "parts": [],
                "session_id": None,
                "session_description": "",
                "error": "No session in graph.",
            }
        single = part_id and part_id.strip()
        parts: list[dict] = []
        with d.session() as session:
            if single:
                result = session.run(
                    """
                    MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $session_id})
                    MATCH (e)-[:OCCURRED_AT]->(s:Station)
                    MATCH (e)-[:ACTS_ON]->(en:Entity)
                    MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                    WHERE en.sysId = $part_id
                    WITH en.sysId AS part_id, s.sysId AS component_id, a.name AS activity, e.timestamp AS ts
                    ORDER BY ts
                    WITH part_id, collect({component_id: component_id, activity: activity, time: ts}) AS steps
                    RETURN part_id, steps
                    """,
                    session_id=session_id,
                    part_id=single,
                )
            else:
                result = session.run(
                    """
                    MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $session_id})
                    MATCH (e)-[:OCCURRED_AT]->(s:Station)
                    MATCH (e)-[:ACTS_ON]->(en:Entity)
                    MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                    WITH en.sysId AS part_id, s.sysId AS component_id, a.name AS activity, e.timestamp AS ts
                    ORDER BY part_id, ts
                    WITH part_id, collect({component_id: component_id, activity: activity, time: ts}) AS steps
                    RETURN part_id, steps
                    ORDER BY part_id
                    LIMIT 200
                    """,
                    session_id=session_id,
                )
            for r in result:
                pid = r["part_id"]
                if pid is None:
                    continue
                steps = r["steps"] or []
                deduped = []
                for i, s in enumerate(steps):
                    comp = (
                        s.get("component_id") or s.get("station") or ""
                        if isinstance(s, dict)
                        else ""
                    )
                    act = s.get("activity", "") if isinstance(s, dict) else ""
                    ts = s.get("time") if isinstance(s, dict) else None
                    if ts is not None:
                        try:
                            dt = datetime.datetime.fromtimestamp(ts)
                            time_str = dt.strftime("%H:%M:%S")
                        except Exception:
                            time_str = ""
                    else:
                        time_str = ""
                    if i == 0 or (
                        deduped
                        and (
                            comp != deduped[-1].get("component_id")
                            or act != deduped[-1].get("activity")
                        )
                    ):
                        lc = _lifecycle_label(str(act))
                        row = {
                            "component_id": str(comp),
                            "activity": str(act),
                            "time": time_str,
                            "lifecycle": lc,
                            "is_entry": lc == "entry",
                            "is_exit": lc == "exit",
                        }
                        if ts is not None:
                            row["timestamp"] = ts
                        deduped.append(row)
                if deduped:
                    parts.append(
                        {
                            "part_id": str(pid),
                            "steps": deduped,
                            "flow": " → ".join(
                                "[{}] {}@{}".format(
                                    x["time"], x["component_id"], x["activity"]
                                )
                                for x in deduped
                            ),
                        }
                    )
        parts = merge_parts_with_overview_seed(parts, single=bool(single))
        return {
            "parts": parts,
            "session_id": session_id,
            "session_description": session_description or "",
        }
    except Exception as e:
        return {"parts": [], "error": str(e)}


def get_session_parts_latest_locations(
    session_id: str | None = None, *, limit: int = 120
) -> tuple[list[dict], dict]:
    """当前 Session 下每个 Part **最后一条事件**对应的工位（Digital Twin 多标记用）。

    返回 ``(rows, meta)``。``rows`` 项含 ``part_id``, ``component_id``, ``activity``, ``time``。
    """
    out: list[dict] = []
    try:
        d = get_driver()
        sid, session_description = _resolve_session(session_id)
        if not sid:
            return [], {"error": "No session in graph."}
        with d.session() as session:
            result = session.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $session_id})
                MATCH (e)-[:OCCURRED_AT]->(s:Station)
                MATCH (e)-[:ACTS_ON]->(en:Entity)
                MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                WITH en.sysId AS part_id, e.timestamp AS ts, s.sysId AS component_id, a.name AS activity
                ORDER BY part_id, ts DESC
                WITH part_id, collect({cid: component_id, act: activity, t: ts})[0] AS last
                RETURN part_id, last.cid AS component_id, last.act AS activity, last.t AS ts
                ORDER BY part_id
                LIMIT $lim
                """,
                session_id=sid,
                lim=int(limit),
            )
            for r in result:
                pid = r.get("part_id")
                if pid is None:
                    continue
                ts = r.get("ts")
                if ts is not None:
                    try:
                        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                    except Exception:
                        time_str = ""
                else:
                    time_str = ""
                out.append(
                    {
                        "part_id": str(pid),
                        "component_id": str(r.get("component_id") or ""),
                        "activity": str(r.get("activity") or ""),
                        "time": time_str,
                    }
                )
        return out, {
            "session_id": sid,
            "session_description": session_description or "",
        }
    except Exception as e:
        return [], {"error": str(e)}


def get_part_last_component(part_id: str, session_id: str | None = None) -> dict:
    """当前 Session 下该 Part 在图中最后一条事件对应的 `component_id`（Digital Twin 高亮用）。"""
    pid = (part_id or "").strip()
    if not pid:
        return {
            "component_id": None,
            "activity": None,
            "time": None,
            "error": "empty part_id",
        }
    data = query_part_flow(pid, session_id)
    if data.get("error"):
        return {
            "component_id": None,
            "activity": None,
            "time": None,
            "error": data["error"],
        }
    parts = data.get("parts") or []
    if not parts:
        return {
            "component_id": None,
            "activity": None,
            "time": None,
            "session_id": data.get("session_id"),
            "session_description": data.get("session_description"),
            "hint": "No events for this part in the selected session",
        }
    steps = parts[0].get("steps") or []
    if not steps:
        return {
            "component_id": None,
            "activity": None,
            "time": None,
            "session_id": data.get("session_id"),
            "hint": "No step data",
        }
    last = steps[-1]
    return {
        "component_id": last.get("component_id"),
        "activity": last.get("activity"),
        "time": last.get("time"),
        "session_id": data.get("session_id"),
        "session_description": data.get("session_description"),
        "step_index": len(steps) - 1,
        "total_steps": len(steps),
    }


def get_part_activity_sequence(
    part_id: str, session_id: str | None = None
) -> tuple[list[str], dict]:
    """Ordered activity names (deduped-by-step view) for Conformance-style checks."""
    data = query_part_flow(part_id.strip(), session_id)
    if data.get("error"):
        return [], data
    parts = data.get("parts") or []
    if not parts:
        return [], data
    steps = parts[0].get("steps") or []
    acts = [s.get("activity", "") for s in steps]
    return acts, data


def ensure_indexes() -> dict:
    """幂等索引（Neo4j 4.4+）。返回执行情况供侧栏提示（改进4）。"""
    stmts = [
        "CREATE INDEX IF NOT EXISTS FOR (s:Session) ON (s.id)",
        "CREATE INDEX IF NOT EXISTS FOR (s:Session) ON (s.start_time)",
        "CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.timestamp)",
        "CREATE INDEX IF NOT EXISTS FOR (en:Entity) ON (en.sysId)",
        "CREATE INDEX IF NOT EXISTS FOR (st:Station) ON (st.sysId)",
        "CREATE INDEX IF NOT EXISTS FOR (a:Activity) ON (a.name)",
    ]
    errors: list[str] = []
    ran_ok = 0
    try:
        d = get_driver()
        with d.session() as s:
            for q in stmts:
                try:
                    s.run(q)
                    ran_ok += 1
                except Exception as e:
                    errors.append("{}: {}".format(q[:48], e))
    except Exception as e:
        errors.append(str(e))
    return {
        "ok": len(errors) == 0,
        "statements_ok": ran_ok,
        "total": len(stmts),
        "errors": errors,
    }


def count_session_events(session_id: str | None = None) -> int:
    """当前（或指定）Session 下 Event 数量 — 回放进度等。"""
    sid, _ = _resolve_session(session_id)
    if not sid:
        return 0
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(:Session {id: $sid})
                RETURN count(e) AS c
                """,
                sid=sid,
            )
            rec = r.single()
            return int(rec["c"]) if rec and rec["c"] is not None else 0
    except Exception:
        return 0


def get_station_ids_from_config() -> list[str]:
    cfg = common.load_config("config.json")
    stations: set[str] = set()
    for k in cfg.get("program_script_paths", {}):
        if str(k).startswith("station"):
            stations.add(str(k))
    for k in cfg.get("component_wips", {}):
        if str(k).startswith("station"):
            stations.add(str(k))
    if stations:
        return sorted(stations)
    return [
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


def query_global_timeline(
    session_id: str | None, limit: int = 500, offset: int = 0
) -> list[dict]:
    sid, _ = _resolve_session(session_id)
    if not sid:
        return []
    off = max(0, int(offset))
    lim = max(1, int(limit))
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(:Session {id: $sid})
                MATCH (e)-[:OCCURRED_AT]->(st:Station)
                MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                MATCH (e)-[:ACTS_ON]->(en:Entity)
                RETURN e.timestamp AS ts, st.sysId AS station, en.sysId AS part_id,
                       a.name AS activity
                ORDER BY e.timestamp
                SKIP $off
                LIMIT $lim
                """,
                sid=sid,
                off=off,
                lim=lim,
            )
            return [dict(rec) for rec in r]
    except Exception:
        return []


def query_activity_counts(session_id: str | None) -> list[dict]:
    sid, _ = _resolve_session(session_id)
    if not sid:
        return []
    try:
        d = get_driver()
        with d.session() as s:
            r = s.run(
                """
                MATCH (e:Event)-[:IN_SESSION]->(:Session {id: $sid})
                MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
                RETURN a.name AS activity, count(e) AS cnt
                ORDER BY cnt DESC
                """,
                sid=sid,
            )
            return [dict(rec) for rec in r]
    except Exception:
        return []


def query_station_events(
    station_sys_id: str,
    limit: int = 200,
    session_id: str | None = None,
) -> list[dict]:
    """Station perspective: recent events at one station in the given Session (or latest)."""
    session_id, _ = _resolve_session(session_id)
    if not session_id:
        return []
    d = get_driver()
    with d.session() as s:
        r = s.run(
            """
            MATCH (e:Event)-[:IN_SESSION]->(sess:Session {id: $session_id})
            MATCH (e)-[:OCCURRED_AT]->(st:Station {sysId: $sid})
            MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
            MATCH (e)-[:ACTS_ON]->(en:Entity)
            RETURN a.name AS activity, en.sysId AS part_id, e.timestamp AS ts
            ORDER BY e.timestamp DESC
            LIMIT $lim
            """,
            session_id=session_id,
            sid=station_sys_id,
            lim=limit,
        )
        rows = []
        for rec in r:
            rows.append(
                {
                    "activity": rec["activity"],
                    "part_id": rec["part_id"],
                    "ts": rec["ts"],
                }
            )
        return rows

from neo4j import GraphDatabase
import uuid
import json
import datetime

import common

# neo4j_writer - write events to graph DB, credentials from config + env
_config = common.load_config("config.json")
_neo4j_cfg = _config["neo4j"]
NEO4J_URI = _neo4j_cfg["uri"]
NEO4J_USER = _neo4j_cfg["username"]
NEO4J_PASSWORD = _neo4j_cfg["password"]

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

_last_event_per_part: dict[str, str] = {}
_last_process_event_per_part: dict[str, str] = {}
_last_global_event_id: str | None = None
_current_session_id: str | None = None

_DEFAULT_DF_PROCESS_ACTIVITIES = (
    "START",
    "LOAD",
    "PROCESS",
    "UNLOAD",
    "FINISH",
    "SCRAP",
)


def _df_process_activities_list() -> list[str]:
    """Uppercase names for Cypher IN $acts — classroom process view (exclude TRANSFER, BLOCK, …)."""
    raw = (
        (_config.get("process_mining") or {}).get("df_process_activities")
        or list(_DEFAULT_DF_PROCESS_ACTIVITIES)
    )
    out: list[str] = []
    for x in raw:
        u = str(x).strip().upper()
        if u:
            out.append(u)
    return out


def _df_process_on_write_enabled() -> bool:
    return bool((_config.get("process_mining") or {}).get("enable_df_process_on_write", True))


def _station_entity_df_flags() -> tuple[bool, bool]:
    pm = _config.get("process_mining") or {}
    st = bool(pm.get("enable_station_df_on_write", True))
    en = bool(pm.get("enable_entity_df_on_write", True))
    return st, en


def start_session(
    session_id: str,
    description: str = "",
    *,
    start_time_iso: str | None = None,
    end_time_iso: str | None = None,
    event_count: int | None = None,
    source_file: str | None = None,
    status: str | None = None,
    display_name: str | None = None,
) -> None:
    """Create or replace Session metadata; reset in-memory DF chain state for live/import writes.

    Session nodes store only ``id``, ``start_time``, and ``end_time``. Other parameters are
    ignored (kept for call-site compatibility).
    """
    global _last_event_per_part, _last_process_event_per_part, _last_global_event_id, _current_session_id
    _ = (description, event_count, source_file, status, display_name)
    _current_session_id = session_id
    _last_event_per_part.clear()
    _last_process_event_per_part.clear()
    _last_global_event_id = None
    if start_time_iso is not None and str(start_time_iso).strip():
        st = str(start_time_iso).strip()
    else:
        st = datetime.datetime.now().isoformat()
    with driver.session() as session:
        session.execute_write(_create_session_tx, session_id, st, end_time_iso)


def _create_session_tx(
    tx,
    session_id: str,
    start_time_iso: str,
    end_time_iso: str | None,
):
    """Session node: only id, start_time, end_time (legacy props removed on write)."""
    tx.run(
        """
        MERGE (s:Session {id: $session_id})
        SET s.start_time = $start_time,
            s.end_time = $end_time
        REMOVE s.description, s.event_count, s.source_file, s.status, s.display_name
        """,
        session_id=session_id,
        start_time=start_time_iso,
        end_time=end_time_iso,
    )


def finalize_session(
    session_id: str,
    end_time_iso: str,
    *,
    status: str = "completed",
    event_count: int | None = None,
) -> None:
    """Normal Stop / import complete: persist end_time on Session.

    ``status`` and ``event_count`` are ignored (kept for call-site compatibility).
    """
    _ = (status, event_count)
    with driver.session() as session:
        session.execute_write(_finalize_session_tx, session_id, end_time_iso)


def _finalize_session_tx(tx, session_id: str, end_time_iso: str):
    tx.run(
        """
        MATCH (s:Session {id: $session_id})
        SET s.end_time = $end_time
        REMOVE s.description, s.event_count, s.source_file, s.status, s.display_name
        """,
        session_id=session_id,
        end_time=end_time_iso,
    )


def _to_neo4j_format(event: dict) -> dict | None:
    """Convert physical event (time/component_id) to neo4j format (timestamp/station_id)."""
    time_str = event.get("time")
    if not time_str:
        return None
    ts = datetime.datetime.fromisoformat(str(time_str).strip()).timestamp()
    return {
        "timestamp": ts,
        "station_id": str(event.get("component_id", "") or "").strip(),
        "time_str": str(time_str).strip(),
        "part_id": str(event.get("part_id", "") or "").strip(),
        "part_type": event.get("part_type", "part"),
        "activity": str(event.get("activity", "") or "").strip(),
    }


def write_event_to_graph(event: dict, session_id: str | None = None):
    """Write one event to Neo4j. Prefer write_events_batch for multiple events."""
    write_events_batch([event], session_id)


def write_events_batch(events: list, session_id: str | None = None):
    """Write multiple events in one transaction. More efficient than one-by-one."""
    global _last_global_event_id, _last_process_event_per_part
    sid = session_id or _current_session_id
    if not sid or not events:
        return
    prepared = []
    for ev in events:
        if "time" in ev and "timestamp" not in ev:
            e = _to_neo4j_format(ev)
        else:
            e = dict(ev)
            if not e.get("time_str"):
                if e.get("time"):
                    e["time_str"] = str(e["time"]).strip()
                elif e.get("timestamp") is not None:
                    e["time_str"] = datetime.datetime.fromtimestamp(
                        float(e["timestamp"])
                    ).isoformat()
            if "station_id" not in e and e.get("component_id") is not None:
                e["station_id"] = str(e["component_id"]).strip()
        if e is None:
            continue
        prepared.append((str(uuid.uuid4()), e))
    if not prepared:
        return
    last_part = dict(_last_event_per_part)
    last_proc = dict(_last_process_event_per_part)
    last_global = _last_global_event_id
    df_acts = _df_process_activities_list() if _df_process_on_write_enabled() else None
    st_df, ent_df = _station_entity_df_flags()
    with driver.session() as session:
        session.execute_write(
            _write_batch_tx,
            sid,
            prepared,
            last_part,
            last_global,
            df_acts,
            last_proc,
            st_df,
            ent_df,
        )
    for eid, e in prepared:
        _last_event_per_part[e["part_id"]] = eid
        _last_global_event_id = eid
    _last_process_event_per_part = last_proc


def _write_batch_tx(
    tx,
    session_id: str,
    prepared: list,
    last_part: dict,
    last_global: str | None,
    df_process_acts: list | None,
    last_proc: dict,
    station_df_on_write: bool,
    entity_df_on_write: bool,
):
    """Event DF / DF_PROCESS; optional Station–Station DF / DF_PROCESS; Entity–Entity DF on global chain.

    NEXT links consecutive events by ingestion/write order (system timeline), not causal dependency between parts.
    """
    act_set = frozenset(df_process_acts) if df_process_acts else None
    for event_id, event in prepared:
        tx.run(
            """
            MERGE (sess:Session {id: $session_id})
            CREATE (e:Event {id: $event_id})
            SET e.timestamp = $timestamp,
                e.label = $activity + "@" + toString($timestamp),
                e.time = $time_str,
                e.component_id = $station_id,
                e.part_id = $part_id,
                e.activity = $activity
            MERGE (s:Station {sysId: $station_id})
            MERGE (en:Entity {sysId: $part_id})
            MERGE (et:EntityType {name: $part_type})
            MERGE (a:Activity {name: $activity})
            MERGE (e)-[:OCCURRED_AT]->(s)
            MERGE (e)-[:ACTS_ON]->(en)
            MERGE (en)-[:OF_TYPE]->(et)
            MERGE (e)-[:OF_ACTIVITY]->(a)
            MERGE (e)-[:IN_SESSION]->(sess)
            """,
            event_id=event_id,
            session_id=session_id,
            timestamp=event["timestamp"],
            time_str=event.get("time_str") or "",
            station_id=event["station_id"],
            part_id=event["part_id"],
            part_type=event["part_type"],
            activity=event["activity"],
        )
        part_id = event["part_id"]
        prev_part = last_part.get(part_id)
        if prev_part:
            tx.run(
                "MATCH (e1:Event {id: $p}) MATCH (e2:Event {id: $c}) MERGE (e1)-[:DF]->(e2)",
                p=prev_part, c=event_id,
            )
            if station_df_on_write:
                tx.run(
                    """
                    MATCH (e1:Event {id: $p})-[:OCCURRED_AT]->(s1:Station)
                    MATCH (e2:Event {id: $c})-[:OCCURRED_AT]->(s2:Station)
                    MERGE (s1)-[:DF]->(s2)
                    """,
                    p=prev_part,
                    c=event_id,
                )
        act_u = str(event.get("activity") or "").strip().upper()
        if act_set and act_u in act_set:
            prev_pe = last_proc.get(part_id)
            if prev_pe:
                tx.run(
                    "MATCH (e1:Event {id: $p}) MATCH (e2:Event {id: $c}) MERGE (e1)-[:DF_PROCESS]->(e2)",
                    p=prev_pe,
                    c=event_id,
                )
                if station_df_on_write:
                    tx.run(
                        """
                        MATCH (e1:Event {id: $p})-[:OCCURRED_AT]->(s1:Station)
                        MATCH (e2:Event {id: $c})-[:OCCURRED_AT]->(s2:Station)
                        MERGE (s1)-[:DF_PROCESS]->(s2)
                        """,
                        p=prev_pe,
                        c=event_id,
                    )
            last_proc[part_id] = event_id

        last_part[part_id] = event_id
        if last_global:
            tx.run(
                "MATCH (e1:Event {id: $p}) MATCH (e2:Event {id: $c}) MERGE (e1)-[:NEXT]->(e2)",
                p=last_global, c=event_id,
            )
            if entity_df_on_write:
                tx.run(
                    """
                    MATCH (e1:Event {id: $p})-[:ACTS_ON]->(en1:Entity)
                    MATCH (e2:Event {id: $c})-[:ACTS_ON]->(en2:Entity)
                    WHERE en1.sysId <> en2.sysId
                    MERGE (en1)-[:DF]->(en2)
                    """,
                    p=last_global,
                    c=event_id,
                )
        last_global = event_id


def clear_all_events() -> None:
    """Delete all Event and Session nodes and reset internal state."""
    global _last_event_per_part, _last_process_event_per_part, _last_global_event_id, _current_session_id
    with driver.session() as session:
        session.execute_write(_delete_all_events_tx)
    _last_event_per_part.clear()
    _last_process_event_per_part.clear()
    _last_global_event_id = None
    _current_session_id = None


def _delete_all_events_tx(tx):
    tx.run("MATCH (:Station)-[r:DF]->(:Station) DELETE r")
    tx.run("MATCH (:Station)-[r:DF_PROCESS]->(:Station) DELETE r")
    tx.run("MATCH (:Entity)-[r:DF]->(:Entity) DELETE r")
    tx.run("MATCH (e:Event) DETACH DELETE e")
    tx.run("MATCH (s:Session) DETACH DELETE s")


def rebuild_df_process_graph(session_id: str | None = None) -> int:
    """
    Remove all DF_PROCESS edges and rebuild using the same skip-noise rule as live writes:
    per part, chronological order, connect consecutive events whose activities are in
    df_process_activities (TRANSFER/BLOCK/… are skipped).

    If session_id is None (full rebuild): also drops (:Station)-[:DF|DF_PROCESS]-(:Station)
    and (:Entity)-[:DF]-(:Entity), then re-derives them from Event-DF / Event-DF_PROCESS / NEXT.
    """
    from collections import defaultdict

    acts = _df_process_activities_list()
    if not acts:
        return 0
    act_set = frozenset(acts)

    q_fetch = """
    MATCH (e:Event)-[:IN_SESSION]->(sess:Session)
    WHERE $sid IS NULL OR sess.id = $sid
    MATCH (e)-[:ACTS_ON]->(en:Entity)
    MATCH (e)-[:OF_ACTIVITY]->(a:Activity)
    RETURN en.sysId AS part_id, e.id AS eid, e.timestamp AS ts, a.name AS act
    ORDER BY part_id, ts
    """
    with driver.session() as s:
        rows = list(s.run(q_fetch, sid=session_id))
    by_part = defaultdict(list)
    for rec in rows:
        pid = rec.get("part_id")
        if pid is None:
            continue
        by_part[pid].append((float(rec["ts"]), rec["eid"], rec["act"]))

    def work(tx):
        if session_id:
            tx.run(
                """
                MATCH (e1:Event)-[r:DF_PROCESS]->(e2:Event)
                MATCH (e1)-[:IN_SESSION]->(sess:Session {id: $sid})
                MATCH (e2)-[:IN_SESSION]->(sess)
                DELETE r
                """,
                sid=session_id,
            )
        else:
            tx.run("MATCH ()-[r:DF_PROCESS]->() DELETE r")
        n = 0
        for _pid, lst in by_part.items():
            last_pe = None
            for _ts, eid, act in lst:
                au = str(act).strip().upper()
                if au in act_set:
                    if last_pe:
                        tx.run(
                            "MATCH (e1:Event {id: $p}) MATCH (e2:Event {id: $c}) MERGE (e1)-[:DF_PROCESS]->(e2)",
                            p=last_pe,
                            c=eid,
                        )
                        n += 1
                    last_pe = eid
        if session_id is None:
            tx.run("MATCH (:Station)-[r:DF]->(:Station) DELETE r")
            tx.run("MATCH (:Station)-[r:DF_PROCESS]->(:Station) DELETE r")
            tx.run("MATCH (:Entity)-[r:DF]->(:Entity) DELETE r")
            tx.run(
                """
                MATCH (e1:Event)-[:DF]->(e2:Event)
                MATCH (e1)-[:OCCURRED_AT]->(s1:Station)
                MATCH (e2)-[:OCCURRED_AT]->(s2:Station)
                MERGE (s1)-[:DF]->(s2)
                """
            )
            tx.run(
                """
                MATCH (e1:Event)-[:DF_PROCESS]->(e2:Event)
                MATCH (e1)-[:OCCURRED_AT]->(s1:Station)
                MATCH (e2)-[:OCCURRED_AT]->(s2:Station)
                MERGE (s1)-[:DF_PROCESS]->(s2)
                """
            )
            tx.run(
                """
                MATCH (e1:Event)-[:NEXT]->(e2:Event)
                MATCH (e1)-[:ACTS_ON]->(en1:Entity)
                MATCH (e2)-[:ACTS_ON]->(en2:Entity)
                WHERE en1.sysId <> en2.sysId
                MERGE (en1)-[:DF]->(en2)
                """
            )
        return n

    with driver.session() as session:
        return session.execute_write(work)


def get_latest_session_id() -> str | None:
    """Return the most recent session id (by latest event timestamp)."""
    with driver.session() as session:
        result = session.run("""
            MATCH (e:Event)-[:IN_SESSION]->(s:Session)
            RETURN s.id AS session_id
            ORDER BY e.timestamp DESC
            LIMIT 1
        """)
        record = result.single()
        return record["session_id"] if record else None


def close():
    """Close Neo4j connection."""
    driver.close()
# test_floor_events_neo4j.py — Neo4j integration tests for the Digital Twin floor event reads
# (neo4j_backend.fetch_session_events_for_floor / floor_cursor_status) and
# twin.factory_floor_sim.sync_factory_floor_sim on top of them.
#
# Opt-in: runs only when NEO4J_TEST_URI is set (optional NEO4J_TEST_USER / NEO4J_TEST_PASSWORD,
# default neo4j / neo4j). Each test creates its own Session nodes named "itest_floor_<random>"
# and deletes only those sessions and their events afterwards; other graph data is checked to
# be unchanged. Needs the project's requirements (neo4j driver, streamlit).

import copy
import json
import os
import random
import sys
import types
import uuid

import pytest

TEST_URI = os.environ.get("NEO4J_TEST_URI", "").strip()
pytestmark = pytest.mark.skipif(not TEST_URI, reason="NEO4J_TEST_URI not set (opt-in Neo4j integration tests)")

pytest.importorskip("neo4j")
pytest.importorskip("streamlit")

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services.neo4j_backend as neo4j_backend  # noqa: E402
import twin.factory_floor_sim as ffs  # noqa: E402

BASE = 1_900_000_000.0


def _outside_counts(driver, prefix):
    with driver.session() as s:
        return s.run(
            """
            MATCH (n)
            WHERE NOT (n:Session AND n.id STARTS WITH $p)
              AND NOT (n:Event AND EXISTS { (n)-[:IN_SESSION]->(:Session) }
                       AND ALL(x IN [(n)-[:IN_SESSION]->(t:Session) | t.id] WHERE x STARTS WITH $p))
            RETURN count(n) AS nodes
            """,
            p=prefix,
        ).single()["nodes"]


@pytest.fixture
def db(monkeypatch, tmp_path):
    user = os.environ.get("NEO4J_TEST_USER", "neo4j")
    password = os.environ.get("NEO4J_TEST_PASSWORD", "neo4j")
    cfg = tmp_path / "config_itest.json"
    cfg.write_text(json.dumps({"neo4j": {"uri": TEST_URI, "username": user, "password": password}}))
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    monkeypatch.setenv("NEO4J_URI", TEST_URI)
    monkeypatch.setenv("NEO4J_USER", user)
    monkeypatch.setenv("NEO4J_PASSWORD", password)
    monkeypatch.setattr(ffs, "st", types.SimpleNamespace(session_state={}))
    neo4j_backend.close_driver()
    driver = neo4j_backend.get_driver()
    prefix = "itest_floor_{}_".format(uuid.uuid4().hex[:12])
    outside_before = _outside_counts(driver, prefix)

    def write(sid, rows):
        """rows: (offset or None, event_id, part, component, activity)."""
        with driver.session() as s:
            s.run("MERGE (:Session {id: $sid})", sid=sid).consume()
            for off, eid, part, comp, act in rows:
                s.run(
                    """
                    MATCH (sess:Session {id: $sid})
                    CREATE (e:Event {id: $id, timestamp: $ts, time: $t,
                                     component_id: $c, part_id: $p, activity: $a})
                    CREATE (e)-[:IN_SESSION]->(sess)
                    """,
                    sid=sid, id=eid, ts=None if off is None else BASE + off,
                    t="itest+{}".format(off), c=comp, p=part, a=act,
                ).consume()

    def delete_event(sid, eid):
        with driver.session() as s:
            s.run(
                "MATCH (e:Event {id: $id})-[:IN_SESSION]->(:Session {id: $sid}) DETACH DELETE e",
                id=eid, sid=sid,
            ).consume()

    env = types.SimpleNamespace(sid=lambda name: prefix + name, write=write, delete_event=delete_event)
    yield env

    with driver.session() as s:
        s.run(
            "MATCH (e:Event)-[:IN_SESSION]->(sess:Session) WHERE sess.id STARTS WITH $p DETACH DELETE e",
            p=prefix,
        ).consume()
        s.run("MATCH (sess:Session) WHERE sess.id STARTS WITH $p DETACH DELETE sess", p=prefix).consume()
    assert _outside_counts(driver, prefix) == outside_before, "test touched data outside its sessions"
    neo4j_backend.close_driver()


LINE = [
    (0, "m", "p1", "corner2", "START"), (0, "c", "p2", "corner2", "START"),
    (2, "z", "p1", "station11", "LOAD"), (2, "a", "p2", "station11", "LOAD"),
    (4, "k", "p1", "station11", "PROCESS"),
    (6, "q", "p1", "station11", "TRANSFER"), (6, "b", "p2", "station11", "PROCESS"),
    (8, "r", "p1", "station21", "LOAD"),
    (10, "d", "p2", "station11", "TRANSFER"), (10, "y", "p1", "station21", "PROCESS"),
    (12, "e", "p1", "splitter5", "FINISH"),
]


def _ids(events):
    return [e["event_id"] for e in events]


def _truth(sid, cap=None):
    evs = neo4j_backend.fetch_session_events_for_floor(sid)
    sim = ffs.empty_sim_state()
    ffs.replay_events(sim, [e for e in evs if cap is None or e["timestamp"] <= cap])
    return sim


def _canon(sim):
    s = copy.deepcopy(sim)
    s["queues"] = {str(k): v for k, v in s["queues"].items()}
    return json.dumps(s, sort_keys=True, default=str)


def _live(sid):
    return ffs.sync_factory_floor_sim(sid, is_replay=False, kpi={}, neo_connected=True)[0]


def _replay(sid, cap):
    kpi = {"run_mode": "replay", "chart_time_unix": cap}
    return ffs.sync_factory_floor_sim(sid, is_replay=True, kpi=kpi, neo_connected=True)[0]


def test_fetch_orders_by_timestamp_then_id_and_applies_filters(db):
    sid = db.sid("filters")
    db.write(sid, LINE)
    fetch = neo4j_backend.fetch_session_events_for_floor
    full = fetch(sid)
    assert _ids(full) == ["c", "m", "a", "z", "k", "b", "q", "r", "d", "y", "e"]
    for cursor in [(BASE + 2, "a"), (BASE + 2, "z"), (BASE + 6, "b"), (BASE + 12, "e"), (BASE + 1, "")]:
        want = ffs.events_after_cursor(full, cursor[0], cursor[1])
        assert _ids(fetch(sid, since_ts=cursor[0], since_event_id=cursor[1])) == _ids(want), cursor
    assert _ids(fetch(sid, until_ts=BASE + 6)) == ["c", "m", "a", "z", "k", "b", "q"]
    assert _ids(fetch(sid, since_ts=BASE + 2, since_event_id="a", until_ts=BASE + 6)) == ["z", "k", "b", "q"]
    assert fetch(sid, since_ts=BASE + 12, since_event_id="e") == []


def test_cursor_status_matches_fetch_order(db):
    sid = db.sid("count")
    db.write(sid, LINE + [(None, "nots", "p1", "station11", "PASS")])
    full = neo4j_backend.fetch_session_events_for_floor(sid)
    assert "nots" not in _ids(full)  # events without a timestamp are skipped by both reads
    status = neo4j_backend.floor_cursor_status
    for i, ev in enumerate(full):
        assert status(sid, ev["timestamp"], ev["event_id"]) == (i + 1, True)
    assert status(sid, BASE + 2, "") == (2, True)  # before 'a' and 'z'
    assert status(sid, BASE + 2, "b") == (3, False)  # 'b' exists, but not at this cursor
    assert status(sid, BASE + 12, "gone") == (11, False)
    assert status(db.sid("missing"), BASE, "x") == (0, False)


def test_large_same_timestamp_group_is_read_completely_and_resumable(db):
    sid = db.sid("burst")
    rng = random.Random(7)
    ids = ["{:08x}".format(rng.getrandbits(32)) for _ in range(1500)]
    db.write(sid, [(5, eid, "p{}".format(i % 9), "station11", "PROCESS") for i, eid in enumerate(ids)])
    fetch = neo4j_backend.fetch_session_events_for_floor
    full = fetch(sid)
    assert _ids(full) == sorted(ids)
    for pos in (0, 1, 749, 1498, 1499):
        cur = full[pos]
        rest = fetch(sid, since_ts=cur["timestamp"], since_event_id=cur["event_id"])
        assert _ids(rest) == sorted(ids)[pos + 1:]
        assert neo4j_backend.floor_cursor_status(sid, cur["timestamp"], cur["event_id"]) == (pos + 1, True)


def test_floor_sync_live_reruns_late_and_same_timestamp_arrivals(db, monkeypatch):
    sid = db.sid("live")
    db.write(sid, LINE)
    want = _canon(_truth(sid))
    applied = []
    real_apply = ffs.apply_event
    monkeypatch.setattr(ffs, "apply_event", lambda sim, ev: (applied.append(ev["event_id"]), real_apply(sim, ev)))
    for _ in range(3):
        assert _canon(_live(sid)) == want
    assert sorted(applied) == sorted(e[1] for e in LINE)  # nothing applied twice
    monkeypatch.setattr(ffs, "apply_event", real_apply)

    db.write(sid, [(14, "f", "p2", "station21", "LOAD")])
    assert _canon(_live(sid)) == _canon(_truth(sid))
    db.write(sid, [(14, "g", "p2", "station21", "PROCESS")])  # same ts as cursor, larger id
    assert _canon(_live(sid)) == _canon(_truth(sid))
    db.write(sid, [(14, "a0", "p3", "corner2", "START")])  # same ts as cursor, smaller id
    assert _canon(_live(sid)) == _canon(_truth(sid))
    db.write(sid, [(3, "late", "p9", "corner2", "START")])  # earlier timestamp, written late
    assert _canon(_live(sid)) == _canon(_truth(sid))
    db.delete_event(sid, "d")  # removed
    assert _canon(_live(sid)) == _canon(_truth(sid))


def test_floor_sync_replay_forward_backward_and_switches(db):
    s1, s2 = db.sid("r1"), db.sid("r2")
    db.write(s1, LINE)
    db.write(s2, LINE + [(14, "x", "p3", "corner2", "START")])
    for cap in [1, 2, 6, 6, 10, 12, 20, 4, 12]:  # forward, same-ts caps, backward jump, forward again
        assert _canon(_replay(s1, BASE + cap)) == _canon(_truth(s1, BASE + cap)), cap
    db.write(s1, [(11, "late", "p9", "corner2", "START")])  # late event below the current cap
    assert _canon(_replay(s1, BASE + 13)) == _canon(_truth(s1, BASE + 13))
    assert _canon(_live(s2)) == _canon(_truth(s2))  # session + mode switch
    assert _canon(_replay(s1, BASE + 6)) == _canon(_truth(s1, BASE + 6))
    assert _canon(_live(s1)) == _canon(_truth(s1))


def test_floor_sync_write_between_status_check_and_read(db, monkeypatch):
    sid = db.sid("race")
    db.write(sid, LINE)
    _live(sid)
    real_status = neo4j_backend.floor_cursor_status
    pending = []

    def status_then_write(*a):
        out = real_status(*a)
        while pending:
            db.write(sid, [pending.pop(0)])
        return out

    monkeypatch.setattr(neo4j_backend, "floor_cursor_status", status_then_write)
    pending.append((14, "f", "p2", "station21", "LOAD"))  # after the cursor
    assert _canon(_live(sid)) == _canon(_truth(sid))
    before = _canon(_truth(sid))
    pending.append((3, "late", "p9", "corner2", "START"))  # before the cursor
    assert _canon(_live(sid)) == before  # not visible to this refresh
    assert _canon(_truth(sid)) != before
    assert _canon(_live(sid)) == _canon(_truth(sid))  # repaired on the next one


def test_floor_sync_session_cleared_and_rewritten_under_same_id(db):
    sid = db.sid("rewrite")
    db.write(sid, LINE)
    _live(sid)
    before = _canon(_truth(sid))
    for _, eid, *_ in LINE:
        db.delete_event(sid, eid)
    rows = [(off, "a%02d" % (len(LINE) - i), p, c, "PROCESS" if eid == "d" else a)
            for i, (off, eid, p, c, a) in enumerate(LINE)]
    db.write(sid, rows)  # same count at or before the old cursor; the cursor event is gone
    assert neo4j_backend.floor_cursor_status(sid, BASE + 12, "e") == (len(LINE), False)
    assert _canon(_truth(sid)) != before
    assert _canon(_live(sid)) == _canon(_truth(sid))

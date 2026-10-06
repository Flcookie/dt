# test_factory_floor_sim.py — Digital Twin floor state (twin.factory_floor_sim.sync_factory_floor_sim).
#
# The Neo4j reads are replaced by an in-memory store with the documented cursor contract:
# events ordered by (timestamp, event_id); "after cursor" means timestamp > ts, or the same
# timestamp and event_id > id; until_ts is inclusive; the cursor status is (events at or before
# the cursor, cursor event still present). The expected floor state for a session
# is always "every event with timestamp <= cap applied exactly once, in (timestamp, id) order".
# Event ids are random in production, so tests use ids that do NOT follow arrival order.
#
# Skipped when streamlit is not installed (factory_floor_sim imports it).

import copy
import json
import os
import sys
import types

import pytest

pytest.importorskip("streamlit")

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services.neo4j_backend as neo4j_backend  # noqa: E402
import twin.factory_floor_sim as ffs  # noqa: E402

BASE = 1_800_000_000.0


def _key(ev):
    return (ev["timestamp"], ev.get("event_id") or "")


class FakeStore:
    """Events per session; mimics fetch_session_events_for_floor / floor_cursor_status."""

    def __init__(self):
        self.sessions: dict[str, list[dict]] = {}
        self.fetch_calls: list[tuple] = []
        self.count_calls: list[tuple] = []
        self.count_fails = False
        self.after_count = None  # callable run right after a status check (concurrent writer)

    def add(self, sid, offset, eid, part, comp, act):
        self.sessions.setdefault(sid, []).append(
            {
                "timestamp": BASE + offset,
                "event_id": eid,
                "part_id": part,
                "component_id": comp,
                "activity": act,
                "time": "t+%s" % offset,
            }
        )

    def ordered(self, sid):
        return sorted(self.sessions.get(sid, []), key=_key)

    def fetch(self, session_id, *, since_ts=None, since_event_id=None, until_ts=None):
        self.fetch_calls.append((session_id, since_ts, since_event_id, until_ts))
        out = []
        for ev in self.ordered(session_id):
            if until_ts is not None and ev["timestamp"] > until_ts:
                continue
            if since_ts is not None and _key(ev) <= (since_ts, since_event_id or ""):
                continue
            out.append(dict(ev))
        return out

    def cursor_status(self, session_id, cursor_ts, cursor_event_id):
        self.count_calls.append((session_id, cursor_ts, cursor_event_id))
        if self.count_fails:
            return None
        cursor = (cursor_ts, cursor_event_id or "")
        evs = self.sessions.get(session_id, [])
        n = sum(1 for ev in evs if _key(ev) <= cursor)
        present = not cursor_event_id or any(_key(ev) == cursor for ev in evs)
        hook, self.after_count = self.after_count, None
        if hook:
            hook()
        return n, present


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(neo4j_backend, "fetch_session_events_for_floor", s.fetch)
    monkeypatch.setattr(neo4j_backend, "floor_cursor_status", s.cursor_status)
    monkeypatch.setattr(ffs, "st", types.SimpleNamespace(session_state={}))
    return s


def truth(store, sid, cap=None):
    sim = ffs.empty_sim_state()
    ffs.replay_events(sim, [e for e in store.ordered(sid) if cap is None or e["timestamp"] <= cap])
    return sim


def canon(sim):
    s = copy.deepcopy(sim)
    s["queues"] = {str(k): v for k, v in s["queues"].items()}
    return json.dumps(s, sort_keys=True, default=str)


def live(sid):
    sim, _ = ffs.sync_factory_floor_sim(sid, is_replay=False, kpi={}, neo_connected=True)
    return sim


def replay(sid, cap):
    kpi = {"run_mode": "replay", "chart_time_unix": cap}
    sim, _ = ffs.sync_factory_floor_sim(sid, is_replay=True, kpi=kpi, neo_connected=True)
    return sim


def seed_line(store, sid="S1"):
    """Two parts through station11 -> station21 -> splitter5, with shared timestamps."""
    plan = [
        (0, "m", "p1", "corner2", "START"), (0, "c", "p2", "corner2", "START"),
        (2, "z", "p1", "station11", "LOAD"), (2, "a", "p2", "station11", "LOAD"),
        (4, "k", "p1", "station11", "PROCESS"),
        (6, "q", "p1", "station11", "TRANSFER"), (6, "b", "p2", "station11", "PROCESS"),
        (8, "r", "p1", "station21", "LOAD"),
        (10, "d", "p2", "station11", "TRANSFER"), (10, "y", "p1", "station21", "PROCESS"),
        (12, "e", "p1", "splitter5", "FINISH"),
    ]
    for off, eid, p, c, a in plan:
        store.add(sid, off, eid, p, c, a)


def test_live_reruns_apply_each_event_once(store, monkeypatch):
    seed_line(store)
    want = canon(truth(store, "S1"))  # computed before apply_event is wrapped
    applied = []
    real_apply = ffs.apply_event
    monkeypatch.setattr(ffs, "apply_event", lambda sim, ev: (applied.append(ev["event_id"]), real_apply(sim, ev)))
    for _ in range(4):
        assert canon(live("S1")) == want
    assert sorted(applied) == sorted(e["event_id"] for e in store.sessions["S1"])
    assert store.fetch_calls[-1][1:3] == (BASE + 12, "e")  # incremental read after the cursor


def test_live_new_events_after_cursor_including_same_timestamp_larger_id(store):
    seed_line(store)
    live("S1")
    store.add("S1", 14, "f", "p2", "station21", "LOAD")
    assert canon(live("S1")) == canon(truth(store, "S1"))
    store.add("S1", 14, "g", "p2", "station21", "PROCESS")  # same ts as cursor, larger id
    assert canon(live("S1")) == canon(truth(store, "S1"))


def test_live_same_timestamp_smaller_id_is_not_lost(store):
    seed_line(store)
    live("S1")
    before = canon(truth(store, "S1"))
    store.add("S1", 12, "a0", "p2", "station21", "LOAD")  # same ts as cursor, smaller id
    assert canon(truth(store, "S1")) != before
    assert canon(live("S1")) == canon(truth(store, "S1"))


def test_live_late_event_with_earlier_timestamp_rebuilds(store):
    seed_line(store)
    live("S1")
    before = canon(truth(store, "S1"))
    store.add("S1", 3, "zz", "p9", "corner2", "START")  # written late, older timestamp
    assert canon(truth(store, "S1")) != before  # the late event matters for the floor
    assert canon(live("S1")) == canon(truth(store, "S1"))
    assert canon(live("S1")) == canon(truth(store, "S1"))  # and stays correct


def test_live_events_removed_from_session_rebuilds(store):
    seed_line(store)
    live("S1")
    before = canon(truth(store, "S1"))
    store.sessions["S1"] = [e for e in store.sessions["S1"] if e["event_id"] != "d"]
    assert canon(truth(store, "S1")) != before
    assert canon(live("S1")) == canon(truth(store, "S1"))


def test_count_failure_keeps_current_state(store):
    seed_line(store)
    before = canon(live("S1"))
    store.count_fails = True
    store.add("S1", 14, "f", "p2", "station21", "LOAD")
    assert canon(live("S1")) == canon(truth(store, "S1"))  # incremental read still works
    assert before != canon(truth(store, "S1"))


def test_write_between_status_check_and_read(store):
    """A writer commits between the status check and the incremental read of one refresh."""
    seed_line(store)
    live("S1")
    # after the cursor: picked up by the same refresh's read, applied once
    store.after_count = lambda: store.add("S1", 14, "f", "p2", "station21", "LOAD")
    assert canon(live("S1")) == canon(truth(store, "S1"))
    assert canon(live("S1")) == canon(truth(store, "S1"))
    # before the cursor: invisible to this refresh, repaired by the next one
    before = canon(truth(store, "S1"))
    store.after_count = lambda: store.add("S1", 3, "late", "p9", "corner2", "START")
    assert canon(live("S1")) == before
    assert canon(truth(store, "S1")) != before
    assert canon(live("S1")) == canon(truth(store, "S1"))
    # removal racing the read: same
    store.after_count = lambda: store.sessions.__setitem__(
        "S1", [e for e in store.sessions["S1"] if e["event_id"] != "d"]
    )
    live("S1")
    assert canon(live("S1")) == canon(truth(store, "S1"))


def test_session_rewritten_under_same_id_rebuilds(store):
    """Graph cleared and the session written again (new random ids, one event changed)."""
    seed_line(store)
    live("S1")
    before = canon(truth(store, "S1"))
    old = store.sessions["S1"]
    store.sessions["S1"] = []
    for i, ev in enumerate(old):
        act = "PROCESS" if ev["event_id"] == "d" else ev["activity"]
        store.add("S1", ev["timestamp"] - BASE, "a%02d" % (10 - i), ev["part_id"], ev["component_id"], act)
    assert len(store.sessions["S1"]) == len(old)
    assert canon(truth(store, "S1")) != before
    assert canon(live("S1")) == canon(truth(store, "S1"))


@pytest.mark.parametrize("caps", [[1, 2, 6, 6, 10, 12, 20], [0, 12], [6, 2], [12, 4, 10]])
def test_replay_forward_backward_and_same_timestamp_caps(store, caps):
    seed_line(store)
    for c in caps:
        assert canon(replay("S1", BASE + c)) == canon(truth(store, "S1", BASE + c)), c


def test_replay_late_event_within_cap_is_picked_up_when_cap_advances(store):
    seed_line(store)
    replay("S1", BASE + 6)
    before = canon(truth(store, "S1", BASE + 8))
    store.add("S1", 5, "late", "p9", "corner2", "START")
    assert canon(truth(store, "S1", BASE + 8)) != before
    assert canon(replay("S1", BASE + 8)) == canon(truth(store, "S1", BASE + 8))


def test_switch_session_and_mode(store):
    seed_line(store, "S1")
    seed_line(store, "S2")
    store.add("S2", 14, "x", "p3", "corner2", "START")
    assert canon(replay("S1", BASE + 6)) == canon(truth(store, "S1", BASE + 6))
    assert canon(live("S2")) == canon(truth(store, "S2"))
    assert canon(replay("S1", BASE + 10)) == canon(truth(store, "S1", BASE + 10))
    assert canon(live("S1")) == canon(truth(store, "S1"))


def test_not_connected_or_no_session_clears_state(store):
    seed_line(store)
    live("S1")
    sim, msg = ffs.sync_factory_floor_sim("S1", is_replay=False, kpi={}, neo_connected=False)
    assert sim is None and msg == "Neo4j not connected — cannot load part positions."
    assert ffs._SESSION_KEY not in ffs.st.session_state
    live("S1")
    assert ffs.sync_factory_floor_sim("  ", is_replay=False, kpi={}, neo_connected=True) == (None, None)
    assert ffs._SESSION_KEY not in ffs.st.session_state

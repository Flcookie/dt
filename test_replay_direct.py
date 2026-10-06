# test_replay_direct.py — records current pacing / sidecar / Neo4j behavior of
# replay_csv_direct.run_replay and replay_session_direct.run_replay_from_neo4j_session.
#
# No real sleeping, Neo4j, MQTT or config.json: time is virtual, Neo4j modules are fakes,
# and the .replay_kpi.json sidecar is redirected to a temp dir.

import csv
import json
import logging
import sys
import time
import types

import pytest

import common

_CFG = {
    "event_buffer": {
        "window_ms": 1000,
        "replay_window_ms": 1000,
        "max_size": None,
        "kpi_print_interval_sec": 2.0,
    },
    "kpi_config": {},
}

_CLOCK_START = 1000.0


class _Clock:
    def __init__(self, trace: list):
        self.now = _CLOCK_START
        self.trace = trace

    def time(self):
        return self.now

    def sleep(self, sec):
        self.trace.append(("sleep", round(sec, 6)))
        self.now += sec


def _make_fake_writer(trace):
    m = types.ModuleType("neo4j_writer")

    def start_session(session_id, description="", **kwargs):
        trace.append(("start_session", session_id, description, kwargs))

    def finalize_session(session_id, end_time_iso, **kwargs):
        trace.append(("finalize_session", session_id, end_time_iso, kwargs))

    def write_events_batch(events, session_id=None):
        trace.append(("write", [e.get("part_id") for e in events], session_id))

    m.start_session = start_session
    m.finalize_session = finalize_session
    m.write_events_batch = write_events_batch
    m.clear_all_events = lambda: trace.append(("clear_all_events",))
    m.close = lambda: trace.append(("close",))
    return m


@pytest.fixture
def env(monkeypatch, tmp_path):
    trace: list = []
    writer = _make_fake_writer(trace)
    backend = types.ModuleType("services.neo4j_backend")
    backend.session_events = []

    def fetch(sid):
        trace.append(("fetch", sid))
        return list(backend.session_events)

    backend.fetch_session_events_log_format = fetch
    backend.close_driver = lambda: trace.append(("close_driver",))
    services_pkg = types.ModuleType("services")
    services_pkg.__path__ = []
    services_pkg.neo4j_backend = backend

    monkeypatch.setitem(sys.modules, "neo4j_writer", writer)
    monkeypatch.setitem(sys.modules, "services", services_pkg)
    monkeypatch.setitem(sys.modules, "services.neo4j_backend", backend)

    import event_pipeline
    import replay_csv_direct
    import replay_session_direct

    for mod in (event_pipeline, replay_csv_direct, replay_session_direct):
        monkeypatch.setattr(mod, "neo4j_writer", writer)
    monkeypatch.setattr(replay_session_direct, "neo4j_backend", backend)

    def load_config(path="config.json"):
        trace.append(("load_config", path))
        return json.loads(json.dumps(_CFG))

    monkeypatch.setattr(common, "load_config", load_config)

    clock = _Clock(trace)
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)

    sidecar = tmp_path / ".replay_kpi.json"
    monkeypatch.setattr(replay_csv_direct, "replay_kpi_state_path", lambda: str(sidecar))
    real_dump = json.dump

    def dump(obj, fp, *a, **kw):
        if isinstance(obj, dict) and "completed" in obj and "data" in obj:
            data = obj["data"]
            trace.append(
                (
                    "kpi_state",
                    round(obj["t"], 6),
                    obj["completed"],
                    data.get("session_id"),
                    data.get("run_mode"),
                    data.get("finished_count"),
                )
            )
        return real_dump(obj, fp, *a, **kw)

    monkeypatch.setattr(json, "dump", dump)

    orig_ingest = event_pipeline.EventPipeline.ingest_event
    orig_drain = event_pipeline.EventPipeline.drain_buffer_tail

    def ingest(self, event):
        trace.append(("ingest", event.get("part_id"), event.get("time")))
        return orig_ingest(self, event)

    def drain(self):
        trace.append(("drain",))
        return orig_drain(self)

    monkeypatch.setattr(event_pipeline.EventPipeline, "ingest_event", ingest)
    monkeypatch.setattr(event_pipeline.EventPipeline, "drain_buffer_tail", drain)

    return types.SimpleNamespace(
        trace=trace,
        clock=clock,
        sidecar=sidecar,
        backend=backend,
        csv_mod=replay_csv_direct,
        session_mod=replay_session_direct,
        tmp=tmp_path,
    )


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "component_id", "part_id", "activity"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _row(t, part, comp, act):
    return {"time": t, "component_id": comp, "part_id": part, "activity": act}


def _sidecar(env):
    with open(env.sidecar, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- CSV replay


def test_csv_replay_sorted_paced_sidecar_and_neo4j(env, capsys):
    path = env.tmp / "log.csv"
    _write_csv(
        path,
        [
            _row("2026-03-12T18:00:05", "p2", "corner2", "START"),
            _row("2026-03-12T18:00:00", "p1", "corner2", "START"),
            _row("2026-03-12T18:00:10", "p1", "splitter5", "FINISH"),
            _row("2026-03-12T18:00:01", "p1", "station11", "LOAD"),
        ],
    )
    env.csv_mod.run_replay(str(path), 2.0)

    sid = "event_log_20260312_180000"
    assert env.trace == [
        ("load_config", "config.json"),
        (
            "start_session",
            sid,
            "csv_import",
            {
                "start_time_iso": "2026-03-12T18:00:00",
                "end_time_iso": None,
                "event_count": None,
                "source_file": "log.csv",
                "status": "running",
            },
        ),
        ("sleep", 0.05),
        ("ingest", "p1", "2026-03-12T18:00:00"),
        ("write", [], sid),
        ("kpi_state", 1000.05, False, sid, "replay", 0),
        ("sleep", 0.5),
        ("ingest", "p1", "2026-03-12T18:00:01"),
        ("write", [], sid),
        ("sleep", 2.0),
        ("ingest", "p2", "2026-03-12T18:00:05"),
        ("write", ["p1", "p1"], sid),
        ("kpi_state", 1002.55, False, sid, "replay", 0),
        ("sleep", 2.5),
        ("ingest", "p1", "2026-03-12T18:00:10"),
        ("write", ["p2"], sid),
        ("kpi_state", 1005.05, False, sid, "replay", 0),
        ("drain",),
        ("write", ["p1"], sid),
        ("finalize_session", sid, "2026-03-12T18:00:10", {"status": "completed"}),
        ("kpi_state", 1005.05, True, sid, "replay", 1),
    ]
    blob = _sidecar(env)
    assert blob["completed"] is True
    assert blob["data"]["session_id"] == sid
    assert blob["data"]["finished_count"] == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "Direct replay: 4 events from {} (speed=2.0x, no MQTT)".format(path),
        "Done. 4 events in {:.1f}s (direct pipeline)".format(env.clock.now - _CLOCK_START),
    ]


def test_csv_replay_speed_zero_division_falls_back_to_fixed_pauses(env):
    path = env.tmp / "log.csv"
    _write_csv(
        path,
        [
            _row("2026-03-12T18:00:00", "p1", "corner2", "START"),
            _row("2026-03-12T18:00:02", "p2", "corner2", "START"),
            _row("2026-03-12T18:00:04", "p3", "corner2", "START"),
        ],
    )
    env.csv_mod.run_replay(str(path), 0.0)
    sleeps = [t for t in env.trace if t[0] == "sleep"]
    # 1st: no previous ts -> 0.05; 2nd: ZeroDivisionError -> 0.1 and pacing reset;
    # 3rd: previous ts was reset -> 0.05 again.
    assert sleeps == [("sleep", 0.05), ("sleep", 0.1), ("sleep", 0.05)]
    assert [t[1] for t in env.trace if t[0] == "ingest"] == ["p1", "p2", "p3"]


def test_csv_replay_kpi_interval_controls_sidecar_frequency(env):
    path = env.tmp / "log.csv"
    _write_csv(
        path,
        [_row("2026-03-12T18:00:{:02d}".format(i), "p{}".format(i), "corner2", "START") for i in range(6)],
    )
    env.csv_mod.run_replay(str(path), 1.0)
    # clock after each event: 1000.05, 1001.05, ... ; interval 2.0 -> write at 1st, 3rd, 5th + final
    writes = [(t[1], t[2]) for t in env.trace if t[0] == "kpi_state"]
    assert writes == [
        (1000.05, False),
        (1002.05, False),
        (1004.05, False),
        (1005.05, True),
    ]


def test_csv_replay_empty_file_only_warns(env, caplog):
    caplog.set_level(logging.INFO)
    path = env.tmp / "empty.csv"
    _write_csv(path, [])
    assert env.csv_mod.run_replay(str(path), 10.0) is None
    assert env.trace == [("load_config", "config.json")]
    assert not env.sidecar.exists()
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("replay_csv_direct", "No rows in {}".format(path))
    ]


def test_csv_replay_unparseable_time_row_currently_aborts_before_session(env):
    """Records current behavior (suspected bug, not fixed here): rows with bad/empty time sort
    first, and the first row's time is parsed for the session id without a guard."""
    path = env.tmp / "bad.csv"
    _write_csv(
        path,
        [
            _row("2026-03-12T18:00:00", "p1", "corner2", "START"),
            _row("garbage", "p2", "corner2", "START"),
        ],
    )
    with pytest.raises(ValueError):
        env.csv_mod.run_replay(str(path), 1.0)
    assert env.trace == [("load_config", "config.json")]


def test_load_sorted_events_stable_and_bad_times_first(env):
    path = env.tmp / "log.csv"
    _write_csv(
        path,
        [
            _row("2026-03-12T18:00:02", "a", "c", "X"),
            _row("", "b", "c", "X"),
            _row("2026-03-12T18:00:01", "c", "c", "X"),
            _row("bad", "d", "c", "X"),
        ],
    )
    rows = env.csv_mod.load_sorted_events(str(path))
    assert [r["part_id"] for r in rows] == ["b", "d", "c", "a"]


def test_write_kpi_state_payload_format(env):
    env.csv_mod._write_kpi_state({"session_id": "s"})
    assert _sidecar(env) == {"t": _CLOCK_START, "data": {"session_id": "s"}, "completed": False}
    env.csv_mod._write_kpi_state({"session_id": "s"}, completed=True)
    assert _sidecar(env)["completed"] is True
    assert sorted(p.name for p in env.tmp.iterdir()) == [".replay_kpi.json"]


# ---------------------------------------------------------------- Neo4j session replay


def test_session_replay_pacing_branches_and_no_neo4j_writes(env, capsys):
    env.backend.session_events = [
        _row("2026-03-12T18:00:00", "a", "corner2", "START"),
        _row("2026-03-12T18:00:03", "a", "station11", "LOAD"),
        _row("2026-03-12T18:00:02", "b", "corner2", "START"),  # negative gap: no sleep
        _row("garbage", "c", "station31", "PASS"),  # parse error with previous ts: 0.1
        {"time": None, "component_id": "corner2", "part_id": "d", "activity": "X"},
        _row("2026-03-12T18:00:05", "a", "splitter5", "FINISH"),  # previous reset: 0.05
        _row("2026-03-12T18:00:07", "e", "corner2", "START"),
    ]
    env.session_mod.run_replay_from_neo4j_session("  src_sid ", 1.0)

    seq = [t for t in env.trace if t[0] != "kpi_state"]
    assert seq == [
        ("fetch", "src_sid"),
        ("load_config", "config.json"),
        ("sleep", 0.05),
        ("ingest", "a", "2026-03-12T18:00:00"),
        ("sleep", 3.0),
        ("ingest", "a", "2026-03-12T18:00:03"),
        ("ingest", "b", "2026-03-12T18:00:02"),
        ("sleep", 0.1),
        ("ingest", "c", "garbage"),
        ("sleep", 0.05),
        ("ingest", "d", None),
        ("sleep", 0.05),
        ("ingest", "a", "2026-03-12T18:00:05"),
        ("sleep", 2.0),
        ("ingest", "e", "2026-03-12T18:00:07"),
        ("drain",),
    ]
    writes = [t for t in env.trace if t[0] == "kpi_state"]
    assert [(w[1], w[2], w[3], w[4]) for w in writes] == [
        (1000.05, False, "src_sid", "replay"),
        (1003.05, False, "src_sid", "replay"),
        (1005.25, False, "src_sid", "replay"),
        (1005.25, True, "src_sid", "replay"),
    ]
    assert writes[-1][5] == 1
    # the KPI write follows the ingest it reflects
    idx = env.trace.index(("ingest", "a", "2026-03-12T18:00:00"))
    assert env.trace[idx + 1][0] == "kpi_state"
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "Session replay: 7 events, session=src_sid (KPI only, no Neo4j write; speed=1.0x)"
    )
    assert out[-1] == "Done. 7 events in {:.1f}s (session src_sid)".format(
        env.clock.now - _CLOCK_START
    )


def test_session_replay_empty_id_only_warns(env, caplog):
    caplog.set_level(logging.INFO)
    env.session_mod.run_replay_from_neo4j_session("   ", 1.0)
    assert env.trace == []
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("replay_session_direct", "empty session id")
    ]


def test_session_replay_no_events_only_warns(env, caplog):
    caplog.set_level(logging.INFO)
    env.session_mod.run_replay_from_neo4j_session("sid", 1.0)
    assert env.trace == [("fetch", "sid")]
    assert not env.sidecar.exists()
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("replay_session_direct", "No events for session sid")
    ]

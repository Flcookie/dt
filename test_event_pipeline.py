# test_event_pipeline.py — records current EventPipeline behavior (commands, ingest, tail drain).
#
# neo4j_writer is replaced by an in-memory fake, so no Neo4j server or config.json is needed.

import datetime
import logging
import sys
import types

import pytest

import common


class _FakeNeo4jWriter(types.ModuleType):
    """Stands in for neo4j_writer; every call is appended to ``calls``."""

    def __init__(self, calls: list):
        super().__init__("neo4j_writer")
        self.calls = calls
        self.fail_write = False
        self.fail_start = False

    def start_session(self, session_id, description="", **kwargs):
        self.calls.append(("start_session", session_id, description, kwargs))
        if self.fail_start:
            raise RuntimeError("start boom")

    def finalize_session(self, session_id, end_time_iso, **kwargs):
        self.calls.append(("finalize_session", session_id, end_time_iso, kwargs))

    def write_events_batch(self, events, session_id=None):
        self.calls.append(
            ("write_events_batch", [e["part_id"] for e in events], session_id)
        )
        if self.fail_write:
            raise RuntimeError("write boom")

    def clear_all_events(self):
        self.calls.append(("clear_all_events",))

    def close(self):
        self.calls.append(("close",))


_FIXED_NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)


class _FixedDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW


@pytest.fixture
def env(monkeypatch):
    calls: list = []
    fake = _FakeNeo4jWriter(calls)
    monkeypatch.setitem(sys.modules, "neo4j_writer", fake)
    import event_pipeline

    monkeypatch.setattr(event_pipeline, "neo4j_writer", fake)
    monkeypatch.setattr(event_pipeline, "datetime", _FixedDatetime)
    counter = iter(range(1, 100))
    monkeypatch.setattr(
        common, "new_event_log_session_id", lambda when=None: "sid_{}".format(next(counter))
    )
    return types.SimpleNamespace(calls=calls, fake=fake, ep=event_pipeline)


def _cfg(**buffer_overrides):
    buf = {"window_ms": 1000, "max_size": None, "replay_window_ms": 1000}
    buf.update(buffer_overrides)
    return {"event_buffer": buf, "kpi_config": {}}


def _ev(sec: int, part: str, comp: str = "corner2", act: str = "START") -> dict:
    return {
        "time": "2026-03-12T18:00:{:02d}.000".format(sec),
        "component_id": comp,
        "part_id": part,
        "activity": act,
    }


def _spy_buffer_and_kpi(pipeline, calls: list) -> None:
    """Record buffer.clear / kpi.reset into the same call log as the Neo4j fake."""
    orig_clear = pipeline.buffer.clear
    orig_reset = pipeline.kpi.reset

    def clear():
        calls.append(("buffer.clear",))
        orig_clear()

    def reset():
        calls.append(("kpi.reset",))
        orig_reset()

    pipeline.buffer.clear = clear
    pipeline.kpi.reset = reset


def _pipeline_with_state(env, **kw):
    """Pipeline with one open session, one KPI-counted part and one event still buffered."""
    p = env.ep.EventPipeline(_cfg(), replay_mode=False, **kw)
    p.init_session("old_sid")
    p.ingest_event(_ev(0, "p1"))
    p.ingest_event(_ev(5, "p2"))  # flushes p1 into KPI, p2 stays in buffer
    assert p.buffer.size == 1
    assert p.kpi.sys_wip == 1
    env.calls.clear()
    _spy_buffer_and_kpi(p, env.calls)
    return p


def _messages(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "event_pipeline"]


# ---------------------------------------------------------------- process_command


def test_reset_kpi_clears_buffer_then_kpi_without_new_session(env, caplog):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)
    p.process_command({"action": "reset_kpi"})
    assert env.calls == [("buffer.clear",), ("kpi.reset",)]
    assert p.session_id == "old_sid"
    assert p.buffer.size == 0
    assert p.kpi.sys_wip == 0
    assert _messages(caplog) == [(logging.INFO, "KPI reset")]


@pytest.mark.parametrize(
    "cmd, expected_desc",
    [
        ({"action": "start_new_session", "description": "shift A"}, "shift A"),
        ({"action": "start_new_session", "description": ""}, "live"),
        ({"action": "start_new_session", "description": None}, "live"),
        ({"action": "start_new_session"}, "live"),
        ({"action": "start_new_session", "description": 42}, "42"),
    ],
)
def test_start_new_session(env, caplog, cmd, expected_desc):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)
    p.process_command(cmd)
    assert env.calls == [
        (
            "start_session",
            "sid_1",
            expected_desc,
            {"start_time_iso": _FIXED_NOW.isoformat(), "status": "running"},
        ),
        ("buffer.clear",),
        ("kpi.reset",),
    ]
    assert p.session_id == "sid_1"
    assert p.buffer.size == 0
    assert p.kpi.sys_wip == 0
    assert _messages(caplog) == [(logging.INFO, "New session: sid_1")]


@pytest.mark.parametrize(
    "cmd, expected_desc",
    [
        ({"action": "reset_all", "description": "after fix"}, "after fix"),
        ({"action": "reset_all", "description": ""}, "live"),
        ({"action": "reset_all"}, "live"),
    ],
)
def test_reset_all(env, caplog, cmd, expected_desc):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)
    p.process_command(cmd)
    assert env.calls == [
        (
            "start_session",
            "sid_1",
            expected_desc,
            {"start_time_iso": _FIXED_NOW.isoformat(), "status": "running"},
        ),
        ("buffer.clear",),
        ("kpi.reset",),
    ]
    assert p.session_id == "sid_1"
    assert _messages(caplog) == [
        (logging.INFO, "Reset All: new session sid_1 {}".format(expected_desc))
    ]


def test_clear_neo4j_clears_graph_before_new_live_session(env, caplog):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)
    p.process_command({"action": "clear_neo4j", "description": "ignored"})
    assert env.calls == [
        ("clear_all_events",),
        (
            "start_session",
            "sid_1",
            "live",
            {"start_time_iso": _FIXED_NOW.isoformat(), "status": "running"},
        ),
        ("buffer.clear",),
        ("kpi.reset",),
    ]
    assert p.session_id == "sid_1"
    assert _messages(caplog) == [(logging.INFO, "Neo4j cleared, new session: sid_1")]


@pytest.mark.parametrize("action", ["start_new_session", "reset_all", "clear_neo4j"])
def test_new_session_failure_keeps_old_session_and_state(env, action):
    p = _pipeline_with_state(env)
    env.fake.fail_start = True
    with pytest.raises(RuntimeError, match="start boom"):
        p.process_command({"action": action})
    assert p.session_id == "old_sid"
    assert p.buffer.size == 1
    assert p.kpi.sys_wip == 1
    assert ("buffer.clear",) not in env.calls
    assert ("kpi.reset",) not in env.calls


def test_new_session_works_when_persist_neo4j_is_false(env):
    """Commands always talk to Neo4j, independent of persist_neo4j."""
    p = _pipeline_with_state(env, persist_neo4j=False)
    p.process_command({"action": "start_new_session"})
    assert env.calls[0][0] == "start_session"
    assert p.session_id == "sid_1"


def test_finalize_session_variants(env, caplog):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)

    p.process_command(
        {
            "action": "finalize_session",
            "session_id": "x",
            "end_time_iso": "2026-01-01T00:00:00",
            "status": "aborted",
        }
    )
    p.process_command({"action": "finalize_session"})
    assert env.calls == [
        ("finalize_session", "x", "2026-01-01T00:00:00", {"status": "aborted"}),
        ("finalize_session", "old_sid", _FIXED_NOW.isoformat(), {"status": "completed"}),
    ]
    assert p.session_id == "old_sid"
    assert _messages(caplog) == [
        (logging.INFO, "Session finalized: x"),
        (logging.INFO, "Session finalized: old_sid"),
    ]


def test_finalize_without_any_session_is_noop(env, caplog):
    caplog.set_level(logging.INFO)
    p = env.ep.EventPipeline(_cfg(), replay_mode=False)
    p.process_command({"action": "finalize_session"})
    assert env.calls == []
    assert _messages(caplog) == []


@pytest.mark.parametrize("cmd", [{}, {"action": "nope"}, {"action": None}])
def test_unknown_command_is_ignored(env, caplog, cmd):
    caplog.set_level(logging.INFO)
    p = _pipeline_with_state(env)
    p.process_command(cmd)
    assert env.calls == []
    assert p.session_id == "old_sid"
    assert _messages(caplog) == []


# ---------------------------------------------------------------- ingest / drain


def test_ingest_counts_feeds_kpi_and_writes_every_call_even_when_empty(env):
    p = env.ep.EventPipeline(_cfg(), replay_mode=False)
    p.init_session("s")
    env.calls.clear()
    seen = []
    orig_on_event = p.kpi.on_event
    p.kpi.on_event = lambda ev: (seen.append(ev["part_id"]), orig_on_event(ev))

    assert p.ingest_event(_ev(0, "a")) == (0, 0)
    assert p.ingest_event(_ev(1, "b")) == (0, 0)
    assert p.ingest_event(_ev(3, "c")) == (2, 0)
    assert p.ingest_event({"component_id": "x"}) == (0, 0)  # no time -> ignored by buffer

    assert seen == ["a", "b"]
    assert env.calls == [
        ("write_events_batch", [], "s"),
        ("write_events_batch", [], "s"),
        ("write_events_batch", ["a", "b"], "s"),
        ("write_events_batch", [], "s"),
    ]
    assert p._last_flush_count == 0
    assert p.flush_since_last_print == 2
    assert p.total_flush_count == 2
    assert p.kpi.sys_wip == 2


def test_ingest_same_timestamp_events_are_each_processed_once_in_arrival_order(env):
    """Two events with an identical timestamp: both reach KPI and Neo4j once, in arrival order
    (this used to raise TypeError and drop the second event)."""
    p = env.ep.EventPipeline(_cfg(), replay_mode=False)
    p.init_session("s")
    env.calls.clear()
    seen = []
    orig_on_event = p.kpi.on_event
    p.kpi.on_event = lambda ev: (seen.append(ev["part_id"]), orig_on_event(ev))

    assert p.ingest_event(_ev(0, "a")) == (0, 0)
    assert p.ingest_event(_ev(0, "b")) == (0, 0)
    assert p.ingest_event(_ev(2, "c")) == (2, 0)
    assert p.ingest_event(_ev(2, "d")) == (0, 0)
    p.drain_buffer_tail()

    assert seen == ["a", "b", "c", "d"]
    assert env.calls == [
        ("write_events_batch", [], "s"),
        ("write_events_batch", [], "s"),
        ("write_events_batch", ["a", "b"], "s"),
        ("write_events_batch", [], "s"),
        ("write_events_batch", ["c", "d"], "s"),
    ]
    assert p.total_flush_count == 4
    assert p.kpi.sys_wip == 4


def test_ingest_forced_flush_logs_warning(env, caplog):
    caplog.set_level(logging.INFO)
    p = env.ep.EventPipeline(_cfg(window_ms=10000, max_size=1), replay_mode=False)
    p.init_session("s")
    env.calls.clear()
    p.ingest_event(_ev(0, "a"))
    assert p.ingest_event(_ev(1, "b")) == (1, 1)
    assert env.calls[-1] == ("write_events_batch", ["a"], "s")
    assert _messages(caplog) == [
        (
            logging.WARNING,
            "max_size forced flush of 1 events (window_ms=10000, max_size=1)",
        )
    ]


def test_ingest_neo4j_error_is_logged_and_kpi_still_updated(env, caplog):
    caplog.set_level(logging.INFO)
    p = env.ep.EventPipeline(_cfg(), replay_mode=False)
    p.init_session("s")
    env.fake.fail_write = True
    p.ingest_event(_ev(0, "a"))
    assert p.ingest_event(_ev(3, "b")) == (1, 0)
    assert p.kpi.sys_wip == 1
    assert p.total_flush_count == 1
    assert _messages(caplog) == [
        (logging.ERROR, "Neo4j write error（KPI 已更新，图库未写入）: write boom"),
        (logging.ERROR, "Neo4j write error（KPI 已更新，图库未写入）: write boom"),
    ]


def test_ingest_without_persist_never_writes(env):
    p = env.ep.EventPipeline(_cfg(), replay_mode=True, persist_neo4j=False)
    p.attach_existing_session_kpi_only("  src  ")
    p.ingest_event(_ev(0, "a"))
    p.ingest_event(_ev(3, "b"))
    p.drain_buffer_tail()
    assert env.calls == []
    assert p.session_id == "src"
    assert p.total_flush_count == 2


def test_drain_empty_buffer_does_nothing(env):
    p = env.ep.EventPipeline(_cfg(), replay_mode=False)
    p.init_session("s")
    p.ingest_event(_ev(0, "a"))
    p.ingest_event(_ev(3, "b"))  # flushes a -> _last_flush_count 1
    p.drain_buffer_tail()  # drains b
    env.calls.clear()
    p.drain_buffer_tail()
    assert env.calls == []
    assert p._last_flush_count == 1
    assert p.flush_since_last_print == 2
    assert p.total_flush_count == 2


def test_drain_flushes_tail_in_time_order(env):
    p = env.ep.EventPipeline(_cfg(window_ms=60000), replay_mode=False)
    p.init_session("s")
    p.ingest_event(_ev(5, "late"))
    p.ingest_event(_ev(1, "early"))
    env.calls.clear()
    p.drain_buffer_tail()
    assert env.calls == [("write_events_batch", ["early", "late"], "s")]
    assert p._last_flush_count == 2
    assert p.flush_since_last_print == 2
    assert p.total_flush_count == 2
    assert p.kpi.sys_wip == 2


def test_drain_neo4j_error_message(env, caplog):
    caplog.set_level(logging.INFO)
    p = env.ep.EventPipeline(_cfg(window_ms=60000), replay_mode=False)
    p.init_session("s")
    p.ingest_event(_ev(1, "a"))
    env.fake.fail_write = True
    p.drain_buffer_tail()
    assert p.kpi.sys_wip == 1
    assert _messages(caplog) == [(logging.ERROR, "Neo4j tail flush error: write boom")]


def test_publish_payload_fields(env):
    p = env.ep.EventPipeline(_cfg(), replay_mode=True)
    p.init_session("s", "csv_import")
    pub = p.kpi_publish_payload()
    assert pub["session_id"] == "s"
    assert pub["run_mode"] == "replay"
    assert env.calls[0] == (
        "start_session",
        "s",
        "csv_import",
        {
            "start_time_iso": _FIXED_NOW.isoformat(),
            "end_time_iso": None,
            "event_count": None,
            "source_file": None,
            "status": "completed",
        },
    )

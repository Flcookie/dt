# test_history_panel_ui.py — records current behavior of ui.history_panel.render_history_panel
# with Streamlit's AppTest (headless). Neo4j, process control, recording, replay worker and the
# file uploader are replaced by recorders, so nothing is started and no database is needed.
#
# Skipped when streamlit is not installed.

import io
import os
import sys
import types

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st  # noqa: E402

import services.mqtt_backend as mqtt_backend  # noqa: E402
import services.neo4j_backend as neo4j_backend  # noqa: E402
import services.process_control as process_control  # noqa: E402
import services.recording as recording  # noqa: E402
import ui.history_panel as hp  # noqa: E402
import ui.replay_panel as replay_panel  # noqa: E402

KP = "home_hist"
SEL = KP + "_history_session_id"
TOGGLE = KP + "_replay_toggle"
IMPORT = KP + "_import_btn"
CSV_OK = (
    "time,component_id,part_id,activity\n"
    "2026-05-09T16:00:02,station11,p1,LOAD\n"
    "2026-05-09T16:00:01,corner2,p1,START\n"
)
SESSIONS = [
    {"id": "s1", "label": "Session one", "event_count": 10, "status_badge": "closed"},
    {"id": "s_empty_open", "label": "E", "event_count": 0, "status_badge": " Open "},
    {"id": "s2", "label": "Session two", "event_count": 0, "status_badge": "closed"},
]


class _Proc:
    def __init__(self, pid, alive=True):
        self.pid = pid
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


class _Upload(io.BytesIO):
    name = "log.csv"


def _app():
    import streamlit as st  # noqa: F811

    import ui.history_panel as hp  # noqa: F811

    hp.render_history_panel(
        key_prefix="home_hist", disabled=st.session_state.get("_test_disabled", False)
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls = []
    cfg = {
        "ping": {"connected": True},
        "sessions": SESSIONS,
        "count": 10,
        "ensure": (True, "ok"),
        "dup_info": None,
        "import_exc": None,
        "upload": None,
        "recording": False,
    }

    def rec(name, fn=lambda *a, **k: None):
        def wrapper(*a, **k):
            calls.append((name,) + a + tuple(sorted(k.items())))
            return fn(*a, **k)

        return wrapper

    def _import(rows, filename, force_new_id, display_name):
        if cfg["import_exc"]:
            raise RuntimeError(cfg["import_exc"])
        return ("new_sid", len(rows), None)

    def _clear_child():
        calls.append(("clear_replay_child",))
        st.session_state.replay_proc = None

    m = monkeypatch.setattr
    m(neo4j_backend, "neo4j_ping", rec("ping", lambda: cfg["ping"]))
    m(neo4j_backend, "list_sessions_enriched", rec("list", lambda n: [dict(s) for s in cfg["sessions"]]))
    m(neo4j_backend, "export_session_events_csv", lambda sid: "ev," + sid)
    m(neo4j_backend, "export_session_kpi_log_csv", lambda sid: "kpi," + sid)
    m(neo4j_backend, "count_session_events", rec("count", lambda sid: cfg["count"]))
    m(neo4j_backend, "find_csv_import_duplicate_info", rec("find_dup", lambda f, n: cfg["dup_info"]))
    m(neo4j_backend, "import_csv_session", rec("import", _import))
    m(process_control, "ensure_main_service_replay", rec("ensure_main_service", lambda: cfg["ensure"]))
    m(mqtt_backend, "switch_config_file", rec("switch_config"))
    m(mqtt_backend, "run_replay_session_subprocess", rec("spawn", lambda sid, speed: _Proc(4242)))
    m(recording, "is_recording", lambda: cfg["recording"])
    m(replay_panel, "stop_replay_and_clear_state", _clear_child)
    m(replay_panel, "clear_kpi_before_replay", rec("reset_downstream"))
    # Replace only history_panel's view of `time` (patching time.sleep globally breaks AppTest).
    m(hp, "time", types.SimpleNamespace(sleep=rec("sleep")))
    m(hp, "PROJECT_ROOT", str(tmp_path))
    m(
        st,
        "file_uploader",
        lambda *a, **k: None if cfg["upload"] is None else _Upload(cfg["upload"].encode()),
    )
    st.cache_data.clear()

    def run(**state):
        at = AppTest.from_function(_app, default_timeout=120)  # first run is a cold import
        for k, v in state.items():
            at.session_state[k] = v
        at.run()
        return at

    return type("Env", (), {"calls": calls, "cfg": cfg, "run": staticmethod(run), "tmp": tmp_path})


def _texts(at, kind):
    return [e.value for e in getattr(at, kind)]


def test_disabled_skips_neo4j_and_drops_pending_import_state(env):
    at = env.run(
        _test_disabled=True,
        home_hist_dup_import={"rows": [1]},
        home_hist_import_feedback={"level": "error", "text": "x"},
    )
    assert not at.exception
    assert [c[0] for c in env.calls] == []
    assert "home_hist_dup_import" not in at.session_state
    assert "home_hist_import_feedback" not in at.session_state
    assert at.selectbox(key=SEL).disabled
    assert at.button(key=TOGGLE).disabled


def test_database_down_shows_error(env):
    env.cfg["ping"] = {"connected": False, "error": "refused"}
    at = env.run()
    assert _texts(at, "error") == ["Database not connected: **refused**"]
    assert at.selectbox(key=SEL).disabled


def test_session_list_hides_empty_open_sessions(env):
    at = env.run()
    assert at.selectbox(key=SEL).options == ["--Select Session--", "Session one", "Session two"]
    assert at.button(key=TOGGLE).disabled  # nothing selected yet


def test_selecting_session_enables_replay_and_exports(env):
    at = env.run()
    at.selectbox(key=SEL).set_value("s1").run()
    assert not at.button(key=TOGGLE).disabled
    assert at.session_state["home_hist_export_session_id"] == "s1"
    assert at.session_state["dt_resolved_session"] == "s1"


def test_start_replay_full_sequence_with_config_local(env):
    (env.tmp / "config_local.json").write_text("{}")
    at = env.run()
    at.selectbox(key=SEL).set_value("s1").run()
    at.selectbox(key=KP + "_replay_spd").set_value("50×").run()
    env.calls.clear()
    at.button(key=TOGGLE).click().run()
    names = [c[0] for c in env.calls]
    start = names.index("count")
    assert names[start:start + 7] == [
        "count",
        "clear_replay_child",
        "ensure_main_service",
        "switch_config",
        "sleep",
        "reset_downstream",
        "spawn",
    ]
    assert ("spawn", "s1", 50.0) in env.calls
    assert ("sleep", 2) in env.calls
    assert any("Replay started · PID <strong>4242</strong>" in m.value for m in at.markdown)


@pytest.mark.parametrize(
    "count, ensure, expected_error, last_call",
    [
        (-1, (True, ""), "Could not read events from the database. Check Neo4j connection and try again.", "count"),
        (0, (True, ""), "No events in this session.", "count"),
        (5, (False, "cannot stop main_service"), "cannot stop main_service", "ensure_main_service"),
    ],
)
def test_start_replay_stops_at_first_failure(env, count, ensure, expected_error, last_call):
    env.cfg["count"] = count
    env.cfg["ensure"] = ensure
    at = env.run()
    at.selectbox(key=SEL).set_value("s1").run()
    env.calls.clear()
    at.button(key=TOGGLE).click().run()
    names = [c[0] for c in env.calls]
    assert last_call in names and "spawn" not in names
    assert expected_error in _texts(at, "error")


def test_live_replay_shows_stop_button_and_stop_clears_child(env):
    at = env.run(replay_proc=_Proc(2222, alive=True))
    assert at.button(key=TOGGLE).label == "Stop Replay"
    assert at.selectbox(key=SEL).disabled
    at.button(key=TOGGLE).click().run()
    assert ("clear_replay_child",) in env.calls
    assert at.button(key=TOGGLE).label == "Start Replay"


def test_finished_replay_is_cleaned_up_on_render(env):
    at = env.run(replay_proc=_Proc(3333, alive=False))
    assert ("clear_replay_child",) in env.calls  # the finished worker's state is cleared first
    assert at.button(key=TOGGLE).label == "Start Replay"
    assert not any("Replay started" in m.value for m in at.markdown)


def test_import_ok_selects_new_session_after_rerun(env):
    env.cfg["upload"] = CSV_OK
    env.cfg["sessions"] = SESSIONS + [{"id": "new_sid", "label": "New", "event_count": 2, "status_badge": "closed"}]
    at = env.run()
    at.button(key=IMPORT).click().run()
    imp = [c for c in env.calls if c[0] == "import"][0]
    assert [r["time"] for r in imp[1]] == ["2026-05-09T16:00:01", "2026-05-09T16:00:02"]  # sorted
    assert ("force_new_id", False) in imp
    assert _texts(at, "success") == ["Import successful. 2 event(s) imported."]
    assert at.selectbox(key=SEL).value == "new_sid"


@pytest.mark.parametrize(
    "upload, import_exc, message",
    [
        (CSV_OK, "db down", "Import failed. db down"),
        ("time,component_id,part_id,activity\n", None, "Import failed. The file has no data rows."),
        (
            "time,component_id,part_id\nx,y,z\n",
            None,
            "Import failed. Each row must include time, component_id, part_id, and activity.",
        ),
        (
            "time,component_id,part_id,activity\n ,c,p,A\n",
            None,
            "Import failed. Each row must include time, component_id, part_id, and activity.",
        ),
    ],
)
def test_import_failures_show_error_feedback(env, upload, import_exc, message):
    env.cfg["upload"] = upload
    env.cfg["import_exc"] = import_exc
    at = env.run()
    at.button(key=IMPORT).click().run()
    assert _texts(at, "error") == [message]


def test_duplicate_import_prompt_skip_and_force(env):
    env.cfg["upload"] = CSV_OK
    env.cfg["dup_info"] = {"id": "s1", "start_time": "T0"}
    at = env.run()
    at.button(key=IMPORT).click().run()
    assert _texts(at, "warning") == [
        "This dataset already exists as session **`s1`** (same first event time and **2** rows). "
        "Session start_time: `T0`."
    ]
    assert at.button(key=IMPORT).disabled
    at.button(key=KP + "_dup_skip").click().run()
    assert not _texts(at, "warning")
    assert not [c for c in env.calls if c[0] == "import"]

    at.button(key=IMPORT).click().run()
    env.cfg["import_exc"] = "db down"
    at.button(key=KP + "_dup_force").click().run()
    imp = [c for c in env.calls if c[0] == "import"][-1]
    assert ("force_new_id", True) in imp
    assert _texts(at, "error") == ["Import failed. db down"]
    assert not _texts(at, "warning")


def test_feedback_levels(env):
    at = env.run(home_hist_import_feedback={"level": "warn", "text": "hello"})
    assert _texts(at, "info") == ["hello"]
    at = env.run(home_hist_import_feedback={"level": "error", "text": "   "})
    assert not _texts(at, "error")

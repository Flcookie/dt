# test_home_ui.py — records current behavior of ui.home.render (control panel home page) with
# Streamlit's AppTest.
#
# SAFETY: every device / SSH / upload / subprocess entry point is replaced by a recorder:
# process_control.run_script_background (the only route to start_programs / stop_programs /
# shutdown / upload_code / upload_config), physical_workflow.start/stop_physical_line_integrated,
# mqtt_backend.switch_config_file, recording, replay child cleanup, time.sleep and st.switch_page.
# Nothing is executed. The History panel is replaced too (it has its own tests).
#
# Skipped when streamlit is not installed.

import json
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
import services.physical_workflow as physical_workflow  # noqa: E402
import services.process_control as process_control  # noqa: E402
import services.recording as recording  # noqa: E402
import ui.history_panel as history_panel  # noqa: E402
import ui.home as home  # noqa: E402
import ui.replay_panel as replay_panel  # noqa: E402

FULL_CFG = {"local_code_paths": {"a": ["x.py"]}, "local_config_paths": {"a": ["c.json"]}}
CONTROL_KEYS = ["home_start_progs", "home_btn_start", "home_btn_stop", "home_stop_progs", "home_shutdown"]
UPLOAD_KEYS = ["home_ul_code", "home_ul_cfg"]
WAIT = "Wait for the background operation to finish."


def _app():
    import ui.home as home

    home.render()


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls = []
    cfg = {
        "cfg_name": "config.json", "kpi": {}, "bg_busy": False, "programs": "deactivated",
        "system": "stop", "recording": False, "rec_path": None, "history": [],
        "start_line": (True, "started"), "stop_line": (True, "stopped"), "raise": {},
    }

    def rec(name, fn=None):
        def wrapper(*a, **k):
            calls.append((name,) + a + tuple(sorted(k.items())))
            if name in cfg["raise"]:
                raise RuntimeError(cfg["raise"][name])
            return fn(*a, **k) if fn else None

        return wrapper

    m = monkeypatch.setattr
    m(mqtt_backend, "active_config_name", lambda: cfg["cfg_name"])
    m(mqtt_backend, "switch_config_file", rec("switch_config_file"))
    m(mqtt_backend, "get_kpi_snapshot", lambda: (dict(cfg["kpi"]), 0.0))
    m(mqtt_backend, "clear_kpi_snapshot", rec("clear_kpi_snapshot"))
    m(process_control, "is_control_operation_running", lambda: cfg["bg_busy"])
    m(process_control, "get_programs_status", lambda: cfg["programs"])
    m(process_control, "get_system_status", lambda: cfg["system"])
    m(process_control, "reset_control_log_session", rec("reset_control_log_session"))
    m(process_control, "run_script_background", rec("run_script_background"))
    m(process_control, "record_control_action", rec("record_control_action"))
    m(process_control, "read_control_action_history", lambda n: list(cfg["history"]))
    m(physical_workflow, "start_physical_line_integrated", rec("start_line", lambda: cfg["start_line"]))
    m(physical_workflow, "stop_physical_line_integrated", rec("stop_line", lambda: cfg["stop_line"]))
    m(recording, "is_recording", lambda: cfg["recording"])
    m(recording, "current_path", lambda: cfg["rec_path"])
    m(replay_panel, "_clear_replay_child_and_temp_file", rec("clear_replay_child"))
    m(history_panel, "render_history_panel", rec("render_history_panel"))
    # Replace only home's view of `time` (patching time.sleep globally breaks AppTest).
    m(home, "time", types.SimpleNamespace(sleep=rec("sleep")))
    m(home, "PROJECT_ROOT", str(tmp_path))
    m(st, "switch_page", rec("switch_page"))
    for name in ("config.json", "config_local.json"):
        (tmp_path / name).write_text(json.dumps(FULL_CFG))

    def run(**state):
        at = AppTest.from_function(_app, default_timeout=120)  # first run is a cold import
        for k, v in state.items():
            at.session_state[k] = v
        at.run()
        return at

    def click(at, key):
        calls.clear()
        at.button(key=key).click().run()
        return at

    return types.SimpleNamespace(calls=calls, cfg=cfg, run=run, click=click, root=tmp_path)


def _names(calls, *only):
    return [c for c in calls if c[0] in only] if only else list(calls)


def _disabled(at, keys):
    return {k: at.button(key=k).disabled for k in keys}


def test_no_mode_locks_controls_history_and_navigation(env):
    at = env.run()
    assert not at.exception
    assert all(_disabled(at, CONTROL_KEYS + UPLOAD_KEYS + ["home_view_logs", "home_dash_kpi", "home_dash_twin"]).values())
    assert not at.button(key="home_what_if").disabled
    assert ("render_history_panel", ("disabled", True), ("key_prefix", "home_hist")) in env.calls
    assert "Programs: <b style='color:#212529'>—</b>" in "".join(m.value for m in at.markdown)


def test_live_mode_enables_controls_and_shows_status(env):
    env.cfg.update(programs="activated", system="start")
    at = env.run(cp_data_source="live")
    assert not any(_disabled(at, CONTROL_KEYS + UPLOAD_KEYS).values())
    html = "".join(m.value for m in at.markdown)
    assert "Programs: <b style='color:#212529'>Activated</b>" in html
    assert "System: <b style='color:#212529'>Running</b>" in html
    assert ("render_history_panel", ("disabled", True), ("key_prefix", "home_hist")) in env.calls


@pytest.mark.parametrize("programs, label", [("partial", "Partial"), ("deactivated", "Deactivated"), ("x", "Unknown")])
def test_program_status_labels(env, programs, label):
    env.cfg["programs"] = programs
    at = env.run(cp_data_source="live")
    assert "Programs: <b style='color:#212529'>{}</b>".format(label) in "".join(m.value for m in at.markdown)


def test_local_mode_disables_controls_and_enables_history(env):
    at = env.run(cp_data_source="local")
    assert all(_disabled(at, CONTROL_KEYS + UPLOAD_KEYS).values())
    assert ("render_history_panel", ("disabled", False), ("key_prefix", "home_hist")) in env.calls


def test_live_replay_disables_controls_with_info(env):
    env.cfg["kpi"] = {"run_mode": "replay"}
    at = env.run(cp_data_source="live")
    assert all(_disabled(at, CONTROL_KEYS + UPLOAD_KEYS).values())
    assert [i.value for i in at.info] == [
        "**Replay mode** — stop replay or use **Stop System** before starting the line."
    ]


def test_background_busy_blocks_every_action_with_toast(env):
    env.cfg["bg_busy"] = True
    at = env.run(cp_data_source="live")
    assert "Background operation in progress — controls paused until it finishes." in [c.value for c in at.caption]
    for key in CONTROL_KEYS + UPLOAD_KEYS:
        env.click(at, key)
        assert [t.value for t in at.toast] == [WAIT]
        assert not _names(env.calls, "run_script_background", "start_line", "stop_line",
                          "switch_config_file", "record_control_action")


@pytest.mark.parametrize(
    "key, label, script, resets_log",
    [
        ("home_start_progs", "Start Programs", "start_programs", True),
        ("home_stop_progs", "Stop Programs", "stop_programs", False),
        ("home_shutdown", "Shutdown", "shutdown", False),
    ],
)
def test_program_scripts_switch_config_then_run_in_background(env, key, label, script, resets_log):
    env.cfg["cfg_name"] = "config_local.json"
    at = env.click(env.run(cp_data_source="live"), key)
    expected = [("switch_config_file", "config_local.json"), ("sleep", 1.0)]
    if resets_log:
        expected.append(("reset_control_log_session",))
    expected.append(("run_script_background", script, ("enforce_config", "config_local.json")))
    assert _names(env.calls, "switch_config_file", "sleep", "reset_control_log_session",
                  "run_script_background", "record_control_action") == expected
    assert [t.value for t in at.toast] == ["{} submitted. Check View Logs.".format(label)]


def test_program_script_while_recording_skips_config_switch(env):
    env.cfg.update(recording=True, rec_path="/x/rec.csv")
    at = env.click(env.run(cp_data_source="live"), "home_stop_progs")
    assert _names(env.calls, "switch_config_file", "sleep", "run_script_background") == [
        ("run_script_background", "stop_programs", ("enforce_config", "config.json"))
    ]
    assert [w.value for w in at.warning] == ["Recording · **/x/rec.csv**"]


@pytest.mark.parametrize("failing", ["switch_config_file", "run_script_background"])
@pytest.mark.parametrize("key, label", [("home_start_progs", "Start Programs"), ("home_shutdown", "Shutdown")])
def test_program_script_failure_is_recorded_and_toasted(env, failing, key, label):
    env.cfg["raise"] = {failing: "boom"}
    at = env.click(env.run(cp_data_source="live"), key)
    assert _names(env.calls, "record_control_action")[-1] == ("record_control_action", label, False, "boom")
    assert [t.value for t in at.toast] == ["{} failed. Check View Logs.".format(label)]
    assert not at.exception


@pytest.mark.parametrize(
    "key, label, fn, result, record_msg, toast",
    [
        ("home_btn_start", "Start System", "start_line", (True, "ok"), "ok", "Start System done. See View Logs for details."),
        ("home_btn_start", "Start System", "start_line", (False, None), "", "Start System failed. See View Logs for details."),
        ("home_btn_stop", "Stop System", "stop_line", (True, "x" * 900), "x" * 800, "Stop System done. See View Logs for details."),
        ("home_btn_stop", "Stop System", "stop_line", (False, "plc"), "plc", "Stop System failed. See View Logs for details."),
    ],
)
def test_line_start_stop_records_result(env, key, label, fn, result, record_msg, toast):
    env.cfg["start_line" if fn == "start_line" else "stop_line"] = result
    at = env.click(env.run(cp_data_source="live"), key)
    assert _names(env.calls, "start_line", "stop_line", "record_control_action") == [
        (fn,), ("record_control_action", label, result[0], record_msg)
    ]
    assert [t.value for t in at.toast] == [toast]


def test_line_start_exception_propagates(env):
    env.cfg["raise"] = {"start_line": "kaboom"}
    at = env.click(env.run(cp_data_source="live"), "home_btn_start")
    assert [e.value for e in at.exception] == ["kaboom"]
    assert not _names(env.calls, "record_control_action")


@pytest.mark.parametrize("key, script, label", [("home_ul_code", "upload_code", "Upload Code"),
                                                ("home_ul_cfg", "upload_config", "Upload Config")])
def test_upload_runs_script_without_config_switch(env, key, script, label):
    at = env.click(env.run(cp_data_source="live"), key)
    assert _names(env.calls, "switch_config_file", "sleep", "run_script_background") == [
        ("run_script_background", script, ("enforce_config", "config.json"))
    ]
    assert [t.value for t in at.toast] == ["{} submitted. Check View Logs.".format(label)]


def test_upload_buttons_disabled_without_paths_or_config(env):
    (env.root / "config.json").write_text(json.dumps({"local_code_paths": {}, "local_config_paths": None}))
    at = env.run(cp_data_source="live")
    assert _disabled(at, UPLOAD_KEYS) == {"home_ul_code": True, "home_ul_cfg": True}
    (env.root / "config.json").unlink()
    at = env.run(cp_data_source="live")
    assert _disabled(at, UPLOAD_KEYS) == {"home_ul_code": True, "home_ul_cfg": True}
    assert not at.exception


def test_invalid_config_json_raises(env):
    (env.root / "config.json").write_text("{oops")
    at = env.run(cp_data_source="live")
    assert at.exception


def test_view_logs_dialog_lists_history(env):
    env.cfg["history"] = [{"cmd": "Shutdown", "time": "10:05:00", "success": False, "message": "boom"}]
    at = env.click(env.run(cp_data_source="live"), "home_view_logs")
    assert any(e.label.startswith("✗ Shutdown · 10:05:00 · failed") for e in at.expander)


@pytest.mark.parametrize("key, target", [("home_dash_kpi", "pages/01_Realtime.py"),
                                         ("home_dash_twin", "pages/05_Digital_Twin.py"),
                                         ("home_what_if", "pages/what-if-analysis.py")])
def test_navigation_buttons(env, key, target):
    env.click(env.run(cp_data_source="live"), key)
    assert ("switch_page", target) in env.calls

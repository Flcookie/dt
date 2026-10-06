# test_mqtt_backend_replay.py — records current mqtt_backend behavior around the replay KPI
# sidecar (.replay_kpi.json) and the replay worker subprocess launchers.
#
# No broker or Streamlit runtime needed: streamlit is a fake module with a session_state dict,
# and paho is stubbed only when it is not installed (the code under test does not use it).

import json
import os
import subprocess
import sys
import time
import types

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

try:
    import paho.mqtt.client  # noqa: F401
except ImportError:
    _paho = types.ModuleType("paho")
    _paho_mqtt = types.ModuleType("paho.mqtt")
    _paho_client = types.ModuleType("paho.mqtt.client")
    _paho_client.Client = object
    _paho_client.MQTT_ERR_SUCCESS = 0
    _paho_client.error_string = lambda rc: "rc={}".format(rc)
    _paho.mqtt = _paho_mqtt
    _paho_mqtt.client = _paho_client
    sys.modules.update(
        {"paho": _paho, "paho.mqtt": _paho_mqtt, "paho.mqtt.client": _paho_client}
    )

import services.mqtt_backend as mb  # noqa: E402

NOW = 10000.0
MQTT_KPI = {"from": "mqtt"}
MQTT_T = 5.0


@pytest.fixture
def env(monkeypatch, tmp_path):
    st = types.ModuleType("streamlit")
    st.session_state = {}
    monkeypatch.setitem(sys.modules, "streamlit", st)
    sidecar = tmp_path / ".replay_kpi.json"
    monkeypatch.setattr(mb, "_replay_kpi_file", lambda: str(sidecar))
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(mb, "_latest_kpi", dict(MQTT_KPI))
    monkeypatch.setattr(mb, "_last_kpi_time", MQTT_T)
    monkeypatch.setattr(mb, "_mqtt_kpi_connected", False)
    monkeypatch.setattr(mb, "_kpi_connect_ts", 0.0)
    return types.SimpleNamespace(st=st, sidecar=sidecar)


def _write(env, content):
    if content is None:
        return
    text = content if isinstance(content, str) else json.dumps(content)
    env.sidecar.write_text(text, encoding="utf-8")


DATA = {"session_id": "s1", "finished_count": 3}

# name, sidecar content (None = no file), expected session id, sidecar used (t or None)
SIDECAR_CASES = [
    ("missing", None, None, None),
    ("invalid_json", "{", None, None),
    ("json_list", "[1, 2]", None, None),
    ("fresh", {"t": NOW - 5, "data": DATA, "completed": False}, "s1", NOW - 5),
    ("boundary_30s", {"t": NOW - 30, "data": DATA}, "s1", NOW - 30),
    ("stale", {"t": NOW - 30.5, "data": DATA, "completed": False}, None, None),
    ("stale_completed", {"t": 1.0, "data": DATA, "completed": True}, "s1", 1.0),
    ("completed_truthy_str", {"t": 1.0, "data": DATA, "completed": "yes"}, "s1", 1.0),
    ("t_zero_completed", {"t": 0, "data": DATA, "completed": True}, None, None),
    ("t_missing_completed", {"data": DATA, "completed": True}, None, None),
    ("t_negative_completed", {"t": -5, "data": DATA, "completed": True}, None, None),
    ("t_not_number", {"t": "abc", "data": DATA, "completed": True}, None, None),
    ("t_numeric_string", {"t": "9999", "data": DATA}, "s1", 9999.0),
    ("data_empty", {"t": NOW, "data": {}, "completed": True}, None, None),
    ("data_list", {"t": NOW, "data": [1], "completed": True}, None, None),
    ("data_missing", {"t": NOW, "completed": True}, None, None),
    ("no_session_id", {"t": NOW, "data": {"x": 1}}, None, NOW),
    ("empty_session_id", {"t": NOW, "data": {"session_id": ""}}, None, NOW),
    ("zero_session_id", {"t": NOW, "data": {"session_id": 0}}, None, NOW),
    ("int_session_id", {"t": NOW, "data": {"session_id": 42}}, "42", NOW),
]


@pytest.mark.parametrize(
    "name, content, exp_sid, exp_t", SIDECAR_CASES, ids=[c[0] for c in SIDECAR_CASES]
)
def test_sidecar_outside_live_mode(env, name, content, exp_sid, exp_t):
    _write(env, content)
    sid = mb.get_replay_pipeline_session_id()
    snap, t = mb.get_kpi_snapshot()
    connected = mb.kpi_connected()

    assert sid == exp_sid
    if exp_t is None:
        assert (snap, t) == (MQTT_KPI, MQTT_T)
        assert connected is False
    else:
        assert snap == content["data"]
        assert t == exp_t and isinstance(t, float)
        assert connected is True
    assert isinstance(sid, (str, type(None)))


@pytest.mark.parametrize(
    "name, content, exp_sid, exp_t", SIDECAR_CASES, ids=[c[0] for c in SIDECAR_CASES]
)
def test_live_mode_never_reads_sidecar(env, name, content, exp_sid, exp_t):
    _write(env, content)
    env.st.session_state["cp_data_source"] = "live"
    assert mb.get_replay_pipeline_session_id() is None
    assert mb.get_kpi_snapshot() == (MQTT_KPI, MQTT_T)
    assert mb.kpi_connected() is False


def test_streamlit_import_failure_counts_as_not_live(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "streamlit", None)  # import raises ImportError
    _write(env, {"t": NOW, "data": DATA})
    assert mb.get_replay_pipeline_session_id() == "s1"
    assert mb.get_kpi_snapshot() == (DATA, NOW)
    assert mb.kpi_connected() is True


def test_snapshot_from_sidecar_is_a_copy(env):
    _write(env, {"t": NOW, "data": DATA})
    snap, _ = mb.get_kpi_snapshot()
    snap["finished_count"] = 99
    assert mb.get_kpi_snapshot()[0]["finished_count"] == 3


def test_snapshot_from_mqtt_is_a_copy(env):
    snap, _ = mb.get_kpi_snapshot()
    snap["from"] = "changed"
    assert mb._latest_kpi == MQTT_KPI


@pytest.mark.parametrize(
    "mqtt_connected, last_kpi, connect_ts, expected",
    [
        (False, NOW, NOW, False),
        (True, NOW - 12.0, 0.0, True),  # fresh KPI (<= stale limit)
        (True, NOW - 12.5, 0.0, False),  # stale KPI
        (True, 0.0, NOW - 28.0, True),  # no KPI yet, still in grace period
        (True, 0.0, NOW - 28.5, False),  # no KPI after grace period
        (True, 0.0, 0.0, True),  # no KPI and no connect time recorded
    ],
)
def test_kpi_connected_mqtt_rules(env, monkeypatch, mqtt_connected, last_kpi, connect_ts, expected):
    env.st.session_state["cp_data_source"] = "live"
    monkeypatch.setattr(mb, "_mqtt_kpi_connected", mqtt_connected)
    monkeypatch.setattr(mb, "_last_kpi_time", last_kpi)
    monkeypatch.setattr(mb, "_kpi_connect_ts", connect_ts)
    assert mb.kpi_connected() is expected


def test_usable_sidecar_wins_over_mqtt_outside_live(env, monkeypatch):
    monkeypatch.setattr(mb, "_mqtt_kpi_connected", True)
    monkeypatch.setattr(mb, "_last_kpi_time", NOW)
    _write(env, {"t": NOW - 1, "data": DATA})
    assert mb.get_kpi_snapshot() == (DATA, NOW - 1)


def test_physical_kpi_session_id_uses_snapshot(env):
    _write(env, {"t": NOW, "data": {"session_id": " p1 ", "run_mode": "physical"}})
    assert mb.physical_kpi_session_id() == "p1"
    _write(env, {"t": NOW, "data": {"session_id": "r1", "run_mode": "replay"}})
    assert mb.physical_kpi_session_id() is None


def test_clear_kpi_snapshot_removes_sidecar_and_mqtt_state(env, monkeypatch):
    monkeypatch.setattr(mb, "_kpi_history", __import__("collections").deque([1, 2]))
    _write(env, {"t": NOW, "data": DATA})
    mb.clear_kpi_snapshot()
    assert not env.sidecar.exists()
    assert mb._latest_kpi == {}
    assert mb._last_kpi_time == 0.0
    assert list(mb._kpi_history) == []
    mb.clear_kpi_snapshot()  # no file: no error


# ---------------------------------------------------------------- replay workers


class _PopenRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        return ("popen", len(self.calls))


@pytest.fixture
def popen(monkeypatch):
    rec = _PopenRecorder()
    monkeypatch.setattr(subprocess, "Popen", rec)
    monkeypatch.setenv("CONFIG_FILE", "config_lab.json")
    monkeypatch.setenv("SOME_OTHER_VAR", "kept")
    return rec


def _check_common(kw):
    from paths import PROJECT_ROOT

    assert kw["cwd"] == PROJECT_ROOT
    assert kw["env"]["CONFIG_FILE"] == "config_lab.json"
    assert kw["env"]["SOME_OTHER_VAR"] == "kept"
    assert kw["env"] is not os.environ
    return PROJECT_ROOT


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_run_replay_subprocess_args(popen, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    ret = mb.run_replay_subprocess("event-logs/a b.csv", 2.5)
    assert ret == ("popen", 1)
    (args, kw), = popen.calls
    root = _check_common(kw)
    assert args == [
        sys.executable,
        os.path.join(root, "replay_csv_direct.py"),
        "event-logs/a b.csv",
        "2.5",
    ]
    assert set(kw) == {"cwd", "env"}


def test_run_replay_session_subprocess_args(popen):
    ret = mb.run_replay_session_subprocess(12345, 10)
    assert ret == ("popen", 1)
    (args, kw), = popen.calls
    root = _check_common(kw)
    assert args == [
        sys.executable,
        os.path.join(root, "replay_session_direct.py"),
        "12345",
        "10",
    ]
    assert set(kw) == {"cwd", "env"}


def test_default_config_name_when_env_unset(popen, monkeypatch):
    monkeypatch.delenv("CONFIG_FILE")
    mb.run_replay_subprocess("x.csv", 1.0)
    assert popen.calls[0][1]["env"]["CONFIG_FILE"] == "config.json"


@pytest.mark.parametrize(
    "fn, first_arg",
    [(mb.run_replay_subprocess, "x.csv"), (mb.run_replay_session_subprocess, "sid")],
)
def test_windows_hides_console_when_flag_available(popen, monkeypatch, fn, first_arg):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    fn(first_arg, 1.0)
    assert popen.calls[0][1]["creationflags"] == 0x08000000


@pytest.mark.parametrize(
    "fn, first_arg",
    [(mb.run_replay_subprocess, "x.csv"), (mb.run_replay_session_subprocess, "sid")],
)
def test_windows_without_flag_attribute_passes_no_flags(popen, monkeypatch, fn, first_arg):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    fn(first_arg, 1.0)
    assert "creationflags" not in popen.calls[0][1]

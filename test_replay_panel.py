# test_replay_panel.py — the replay clean-up helpers that home and history_panel call
# (ui.replay_panel.stop_replay_and_clear_state / clear_kpi_before_replay). Session state, the
# replay process, the KPI snapshot and the sleep are fakes; nothing is started or killed.
#
# Skipped when streamlit is not installed.

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

import ui.replay_panel as replay_panel  # noqa: E402


class _SessionState(dict):
    """dict with attribute access, like st.session_state."""

    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class _Proc:
    def __init__(self, running, calls):
        self.running = running
        self.calls = calls

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.calls.append("terminate")

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))


@pytest.fixture
def state(monkeypatch):
    s = _SessionState()
    monkeypatch.setattr(replay_panel, "st", types.SimpleNamespace(session_state=s))
    return s


@pytest.mark.parametrize("running, expected_calls", [(True, ["terminate", ("wait", 3)]), (False, [])])
def test_stop_replay_and_clear_state(state, tmp_path, running, expected_calls):
    calls = []
    csv = tmp_path / "upload.csv"
    csv.write_text("time,component_id\n")
    state.update(replay_proc=_Proc(running, calls), replay_csv_path=str(csv), replay_event_baseline=12,
                 other="kept")
    replay_panel.stop_replay_and_clear_state()
    assert calls == expected_calls  # only a still-running worker is terminated
    assert state == {"replay_proc": None, "replay_csv_path": None, "other": "kept"}
    assert not csv.exists()


def test_stop_replay_and_clear_state_without_process_or_file(state, tmp_path):
    state.update(replay_csv_path=str(tmp_path / "already_gone.csv"))
    replay_panel.stop_replay_and_clear_state()  # no process, missing file: nothing raises
    assert state == {"replay_csv_path": None}


def test_clear_kpi_before_replay(state, monkeypatch):
    calls = []
    monkeypatch.setattr(replay_panel.mqtt_backend, "clear_kpi_snapshot", lambda: calls.append("clear_kpi_snapshot"))
    monkeypatch.setattr(replay_panel, "time", types.SimpleNamespace(sleep=lambda s: calls.append(("sleep", s))))
    state.update(_kpi_cache={"x": 1}, _kpi_tupd=5.0, replay_proc="kept")
    replay_panel.clear_kpi_before_replay()
    assert calls == ["clear_kpi_snapshot", ("sleep", 0.15)]
    assert state == {"replay_proc": "kept"}


class _CountingProc:
    def __init__(self, running):
        self.running = running
        self.polls = 0

    def poll(self):
        self.polls += 1
        return None if self.running else 0


@pytest.mark.parametrize("running", [True, False])
def test_running_replay_process_polls_once(state, running):
    proc = _CountingProc(running)
    state.update(replay_proc=proc)
    assert replay_panel.running_replay_process() is (proc if running else None)
    assert proc.polls == 1
    assert state["replay_proc"] is proc  # only reads the state


def test_running_replay_process_without_worker(state):
    assert replay_panel.running_replay_process() is None
    state.update(replay_proc=None)
    assert replay_panel.running_replay_process() is None


@pytest.mark.parametrize("running, cleaned", [(True, False), (False, True)])
def test_clean_up_finished_replay_only_clears_an_exited_worker(state, monkeypatch, running, cleaned):
    calls = []
    monkeypatch.setattr(replay_panel, "stop_replay_and_clear_state", lambda: calls.append("stop"))
    proc = _CountingProc(running)
    state.update(replay_proc=proc)
    replay_panel.clean_up_finished_replay()
    assert calls == (["stop"] if cleaned else []) and proc.polls == 1
    state.update(replay_proc=None)
    replay_panel.clean_up_finished_replay()  # no worker: nothing to do
    assert calls == (["stop"] if cleaned else [])

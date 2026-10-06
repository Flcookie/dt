# test_main_service.py — main_service.py run as a script (runpy, as __main__), the way
# process_control / run_main.bat start it, with fake external dependencies only: the paho MQTT
# client (records calls, delivers scripted messages), neo4j_writer (records calls) and
# time.sleep (drives the KPI loop and stops it with KeyboardInterrupt). event_pipeline,
# event_buffer, kpi_calculator, common and main_service itself run for real. The script is
# copied into tmp_path so its PID file and logs stay there.

import json
import logging
import os
import runpy
import shutil
import subprocess
import sys
import time
import types

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SCRIPT = os.path.join(_ROOT, "main_service.py")
EVENT_TOPIC = "component_event/station11/all"
KPI_TOPIC = "kpi/main_service/all"


def _event(seconds, part, activity, component="station11"):
    payload = {"time": "2026-05-09T10:00:{:06.3f}".format(seconds), "component_id": component,
               "part_id": part, "activity": activity}
    return EVENT_TOPIC, json.dumps(payload)


class _Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode("utf-8")


class Service:
    """One run of main_service.py; collects MQTT / Neo4j calls, errors, logs and the PID file."""

    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path
        self.monkeypatch = monkeypatch
        self.calls = []
        self.client = None
        self.globals = None
        self.connect_error = None
        self.on_tick = None
        self.snapshot_hook = None
        self.pid_seen = []

    def deliver(self, topic, payload):
        self.client.on_message(self.client, None, _Msg(topic, payload))

    def _fakes(self):
        svc = self

        class Client:
            def __init__(self, *a, **k):
                svc.client = self
                self.on_connect = self.on_disconnect = self.on_message = None

            def reconnect_delay_set(self, **k):
                svc.calls.append(("reconnect_delay_set", k))

            def connect(self, host, port=1883, *a, **k):
                svc.calls.append(("connect", host, port))
                if svc.connect_error:
                    raise svc.connect_error

            def loop_start(self):
                svc.calls.append(("loop_start",))
                self.on_connect(self, None, {}, 0)

            def subscribe(self, topic, qos=0):
                svc.calls.append(("subscribe", topic, qos))

            def publish(self, topic, payload=None, qos=0, retain=False):
                svc.calls.append(("publish", topic, json.loads(payload), qos))

            def loop_stop(self):
                svc.calls.append(("loop_stop",))

            def disconnect(self):
                svc.calls.append(("disconnect",))

        client_mod = types.ModuleType("paho.mqtt.client")
        client_mod.Client = Client
        mqtt_pkg = types.ModuleType("paho.mqtt")
        mqtt_pkg.client = client_mod
        paho = types.ModuleType("paho")
        paho.mqtt = mqtt_pkg
        writer = types.ModuleType("neo4j_writer")
        for name in ("start_session", "write_events_batch", "finalize_session", "clear_all_events", "close"):
            setattr(writer, name, lambda *a, _n=name, **k: svc.calls.append(("neo4j." + _n,)))
        m = self.monkeypatch
        m.setitem(sys.modules, "paho", paho)
        m.setitem(sys.modules, "paho.mqtt", mqtt_pkg)
        m.setitem(sys.modules, "paho.mqtt.client", client_mod)
        m.setitem(sys.modules, "neo4j_writer", writer)
        m.delitem(sys.modules, "event_pipeline", raising=False)  # re-import against the fake writer

    def run(self, ticks, *, stop_after, argv=()):
        script = os.path.join(self.dir, "main_service.py")
        shutil.copyfile(SCRIPT, script)
        self.pid_path = os.path.join(self.dir, "main_service.pid")
        cfg = {"mqtt_broker_host": "127.0.0.1", "mqtt_broker_port": 1883,
               "log_folder": os.path.join(self.dir, "logs"),
               "event_buffer": {"window_ms": 500, "max_size": 50, "kpi_print_interval_sec": 2.0}}
        cfg_path = os.path.join(self.dir, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        self.monkeypatch.setenv("CONFIG_FILE", cfg_path)
        self.monkeypatch.setattr(sys, "argv", [script, *argv])
        self._fakes()
        if self.snapshot_hook:
            import kpi_calculator

            real = kpi_calculator.KpiCalculator.get_snapshot
            hook = self.snapshot_hook
            self.monkeypatch.setattr(kpi_calculator.KpiCalculator, "get_snapshot",
                                     lambda calc: hook(real(calc)))
        state = {"tick": 0}

        def fake_sleep(seconds):
            state["tick"] += 1
            self.pid_seen.append(os.path.exists(self.pid_path))
            self.globals = sys.modules["__main__"].__dict__  # the running script's module
            for topic, payload in ticks.get(state["tick"], []):
                self.deliver(topic, payload)
            if self.on_tick:
                self.on_tick(state["tick"])
            if state["tick"] >= stop_after:
                raise KeyboardInterrupt

        self.monkeypatch.setattr(time, "sleep", fake_sleep)
        logger = logging.getLogger("main_service")  # setup_logger keeps handlers across runs
        saved = logger.handlers[:]
        logger.handlers = []
        self.error = None
        try:
            runpy.run_path(script, run_name="__main__")
        except BaseException as e:  # noqa: BLE001 — what escapes the script, as an uncaught crash would
            self.error = e
        finally:
            for h in logger.handlers:
                h.close()
            logger.handlers = saved
        return self

    def log_text(self, prefix):
        folder = os.path.join(self.dir, "logs")
        (name,) = [n for n in os.listdir(folder) if n.startswith(prefix)]
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return f.read()

    def published(self):
        return [c[2] for c in self.calls if c[0] == "publish"]


@pytest.fixture
def service(tmp_path, monkeypatch):
    return Service(tmp_path, monkeypatch)


def test_normal_run_logs_publishes_and_cleans_up(service):
    ticks = {1: [_event(0.1, "p1", "START", "corner2"), _event(1.0, "p1", "LOAD")],
             2: [("command/dashboard/main_service", json.dumps({"action": "reset_kpi"}))]}
    svc = service.run(ticks, stop_after=3)
    assert svc.error is None
    assert [c[:2] for c in svc.calls[:6]] == [
        ("neo4j.start_session",),  # the live session is created before MQTT is connected
        ("reconnect_delay_set", {"min_delay": 1, "max_delay": 120}), ("connect", "127.0.0.1"),
        ("loop_start",), ("subscribe", "component_event/+/all"), ("subscribe", "command/+/main_service")]
    pubs = svc.published()
    assert len(pubs) == 2  # tick 3 raises KeyboardInterrupt before its snapshot
    assert all(p["run_mode"] == "physical" and p["session_id"].startswith("event_log_") for p in pubs)
    assert [c[0] for c in svc.calls[-3:]] == ["loop_stop", "disconnect", "neo4j.close"]
    assert svc.pid_seen == [True, True, True] and not os.path.exists(svc.pid_path)
    kpi_log = svc.log_text("kpi_log_")
    assert kpi_log.startswith("[main_service] Started.") and kpi_log.endswith("[main_service] Stopped.\n")
    report = kpi_log.split("\n")
    assert report.count(next(line for line in report if line.startswith("[Session] "))) == 2
    assert sum(line.startswith("[KPI stage ") for line in report) == 12  # 6 stages x 2 reports
    assert "[Buffer] size=1, flush_last=1, flush_2s=1, total=1" in report
    assert "[Buffer] size=0, flush_last=1, flush_2s=0, total=1" in report  # reset_kpi cleared the buffer


def test_replay_flag_sets_run_mode(service):
    svc = service.run({}, stop_after=2, argv=["--replay"])
    assert [p["run_mode"] for p in svc.published()] == ["replay"]


def _observe_report(service, inject=None):
    """Record, in order, the shared values the KPI report reads (session id, buffer counters)
    and the keys it reads from the report's snapshot. ``inject`` runs while buffer.size is read."""
    reads = []
    snapshots = {"n": 0}

    class RecordingSnapshot(dict):
        def __getitem__(self, key):
            reads.append("snap[%s]" % key)
            return dict.__getitem__(self, key)

        def get(self, key, default=None):
            reads.append("snap[%s]" % key)
            return dict.get(self, key, default)

    def hook(snap):
        snapshots["n"] += 1
        return RecordingSnapshot(snap) if snapshots["n"] == 1 else snap  # the first one is the report's

    def caller_is_report():
        return sys._getframe(2).f_code.co_name in ("_log_kpi_report", "_print_kpi_snapshot")

    def watch(tick):
        if tick != 1:
            return
        pipeline = service.globals["pipeline"]

        def recorded(name):
            def get(obj):
                if caller_is_report():
                    reads.append(name)
                return obj.__dict__[name]

            return property(get, lambda obj, v: obj.__dict__.__setitem__(name, v))

        base = type(pipeline)

        class ObservedPipeline(base):
            session_id = property(lambda obj: (reads.append("session_id") if caller_is_report() else None,
                                               base.session_id.fget(obj))[1])
            flush_since_last_print = recorded("flush_since_last_print")
            total_flush_count = recorded("total_flush_count")

        buffer_base = type(pipeline.buffer)

        class ObservedBuffer(buffer_base):
            @property
            def size(obj):
                value = buffer_base.size.fget(obj)
                if caller_is_report():
                    reads.append("buffer.size")
                    if inject:
                        inject()
                return value

        pipeline.__class__ = ObservedPipeline
        pipeline.buffer.__class__ = ObservedBuffer

    service.snapshot_hook = hook
    service.on_tick = watch
    return reads


def test_report_reads_counters_in_order_after_the_summary(service):
    # e1 is flushed by e2 (flush_last=1); e3 stays buffered with e2 (flush_last=0, size=2).
    ticks = {1: [_event(0.0, "p1", "START", "corner2"), _event(1.0, "p1", "LOAD"), _event(1.1, "p1", "PROCESS")]}
    late = _event(5.0, "p2", "START", "corner2")  # flushes e2 and e3 when delivered
    reads = _observe_report(service, inject=lambda: service.deliver(*late))
    svc = service.run(ticks, stop_after=2)
    assert svc.error is None
    counters = ["buffer.size", "flush_since_last_print", "total_flush_count"]
    assert reads[0] == "session_id"
    assert [r for r in reads if not r.startswith("snap[")] == ["session_id"] + counters
    first_counter = reads.index("buffer.size")
    assert reads.index("snap[observation_time_sec]") < reads.index("snap[stages]") < first_counter
    assert reads.index("total_flush_count") < reads.index("snap[state_probability]")
    # A message handled by the callback while buffer.size is being read: size is the value
    # before it; flush_last, flush_2s and total are read afterwards and include it.
    assert "[Buffer] size=2, flush_last=2, flush_2s=3, total=3" in svc.log_text("kpi_log_").split("\n")


def test_bad_snapshot_fails_before_any_counter_is_read(service):
    reads = _observe_report(service)
    real_hook = service.snapshot_hook

    def drop_obs_time(snap):
        snap = real_hook(snap)
        dict.pop(snap, "observation_time_sec", None)
        return snap

    service.snapshot_hook = drop_obs_time
    svc = service.run({1: [_event(0.0, "p1", "START", "corner2")]}, stop_after=3)
    assert isinstance(svc.error, KeyError) and svc.error.args == ("observation_time_sec",)
    assert reads[0] == "session_id"
    assert not {"buffer.size", "flush_since_last_print", "total_flush_count"} & set(reads)
    kpi_log = svc.log_text("kpi_log_")
    assert "[Session]" not in kpi_log and "[Buffer]" not in kpi_log  # nothing of the report is written
    assert svc.published() == []
    assert [c[0] for c in svc.calls[-3:]] == ["loop_stop", "disconnect", "neo4j.close"]  # finally ran
    assert not os.path.exists(svc.pid_path)


def test_missing_utilization_fails_after_the_counters_are_read(service):
    reads = _observe_report(service)
    real_hook = service.snapshot_hook

    def drop_utilization(snap):
        snap = real_hook(snap)
        dict.pop(snap, "utilization", None)
        return snap

    service.snapshot_hook = drop_utilization
    svc = service.run({}, stop_after=3)
    assert isinstance(svc.error, KeyError) and svc.error.args == ("utilization",)
    assert [r for r in reads if not r.startswith("snap[")] == [
        "session_id", "buffer.size", "flush_since_last_print", "total_flush_count"]
    assert "[Buffer]" not in svc.log_text("kpi_log_")


def test_another_running_instance_exits_before_connecting(service, tmp_path):
    dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "main_service.py"])
    try:
        (tmp_path / "main_service.pid").write_text(str(dummy.pid))
        svc = service.run({}, stop_after=1)
    finally:
        dummy.kill()
        dummy.wait()
    assert isinstance(svc.error, SystemExit) and svc.error.code == 1
    assert svc.calls == [] and "Already running (PID {})".format(dummy.pid) in svc.log_text("main_service_")


def test_mqtt_connect_failure_escapes_and_leaves_the_pid_file(service):
    service.connect_error = ConnectionRefusedError(111, "Connection refused")
    svc = service.run({}, stop_after=1)
    assert isinstance(svc.error, ConnectionRefusedError)
    assert os.path.exists(svc.pid_path)  # current behavior: no cleanup before the KPI loop starts
    assert [c[0] for c in svc.calls] == ["neo4j.start_session", "reconnect_delay_set", "connect"]


# ---- importing the module (main() is only called when run as a script) ----------------------


@pytest.fixture
def ms_module(tmp_path, monkeypatch):
    """main_service imported fresh with the fake MQTT client and neo4j_writer installed."""
    svc = Service(tmp_path, monkeypatch)
    svc._fakes()
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"mqtt_broker_host": "127.0.0.1", "mqtt_broker_port": 1883,
                                    "log_folder": str(tmp_path / "logs")}))
    monkeypatch.setenv("CONFIG_FILE", str(cfg_path))
    monkeypatch.delitem(sys.modules, "main_service", raising=False)
    import importlib

    svc.handlers_before_import = list(logging.getLogger("main_service").handlers)
    module = importlib.import_module("main_service")
    yield module, svc
    sys.modules.pop("main_service", None)


def test_import_does_not_start_the_service(ms_module, tmp_path):
    module, svc = ms_module
    assert svc.client is None and svc.calls == []  # no MQTT client, no Neo4j session
    assert not (tmp_path / "logs").exists()  # no log folder / kpi_log / app log
    assert module.pipeline is None
    assert logging.getLogger("main_service").handlers == svc.handlers_before_import  # no logger setup
    assert callable(module.main)


SNAPSHOT = {
    "observation_time_sec": 120.0,
    "system": {"num_completions": 4, "num_scraps": 1, "wip_instantaneous": 3, "wip_average": 2.5,
               "complete_rate": 0.0333, "scrap_rate": 0.2, "avg_cycle_time_fin": 41.25,
               "avg_cycle_time_all": 40.0},
    "stages": {
        "stage10": {"wip_instantaneous": 0, "wip_average": 0.0, "num_departures": 0, "throughput": 0.0,
                    "avg_flow_time": 0.0},
        "stage2": {"instantaneous_wip": 2, "avg_wip": 1.25, "departures": 7, "throughput_per_sec": 0.05,
                   "avg_flow_time_sec": 9.5},
        "stage1": {"wip_instantaneous": 1, "wip_average": 0.5, "num_departures": 9, "throughput": 0.075,
                   "avg_flow_time": 6.0},
    },
    "state_probability": {"station11": {"busy": 0.5, "fail": 0.1, "blocked": 0.15, "idle": 0.25},
                          "station21": {"loading": 0.25, "idle": 0.75}},
    "utilization": {"station11": 0.5},
}


def test_summary_lines_read_system_block_and_order_stages_numerically(ms_module):
    module, _ = ms_module
    assert module.format_kpi_summary_lines(SNAPSHOT) == [
        "[KPI system] NumCompletions=4 NumScraps=1 WIP=3 AvgWIP=2.500 CompleteRate=0.0333/s "
        "ScrapRate=0.200 AvgCycleFin=41.2s AvgCycleAll=40.0s obs_time=120.0s",
        "[KPI stage 1] WIP=1 AvgWIP=0.500 NumDepartures=9 Throughput=0.0750/s AvgFlow=6.0s",
        "[KPI stage 2] WIP=2 AvgWIP=1.250 NumDepartures=7 Throughput=0.0500/s AvgFlow=9.5s",  # older stage keys
        "[KPI stage 10] WIP=0 AvgWIP=0.000 NumDepartures=0 Throughput=0.0000/s AvgFlow=0.0s",
    ]


def test_summary_lines_fall_back_to_top_level_keys(ms_module):
    module, _ = ms_module
    legacy = {"observation_time_sec": 60, "throughput": 0.05, "finished_count": 3, "scrap_count": 1,
              "scrap_rate": 0.25, "avg_flow_time_sec": 30, "avg_cycle_time_all_sec": 28.5, "current_wip": 2,
              "avg_wip": 1.5}
    assert module.format_kpi_summary_lines(legacy) == [
        "[KPI system] NumCompletions=3 NumScraps=1 WIP=2 AvgWIP=1.500 CompleteRate=0.0500/s "
        "ScrapRate=0.250 AvgCycleFin=30.0s AvgCycleAll=28.5s obs_time=60.0s",
    ]
    with pytest.raises(KeyError, match="observation_time_sec"):
        module.format_kpi_summary_lines({"system": {}})


def test_station_lines(ms_module):
    module, _ = ms_module
    assert module.format_station_state_lines(SNAPSHOT) == [
        "  station11",
        "    Utilization (P_busy): 0.50",
        "    P_busy: 0.50  P_fail: 0.10  P_blocked: 0.15  P_idle: 0.25",
        "  station21",
        "    Utilization (P_busy): 0.00",  # not in utilization -> 0
        "    P_busy: 0.25  P_fail: 0.00  P_blocked: 0.00  P_idle: 0.75",  # 'loading' counts as busy
    ]
    assert module.format_station_state_lines({}) == []
    with pytest.raises(KeyError, match="utilization"):
        module.format_station_state_lines({"state_probability": {"station11": {}}})

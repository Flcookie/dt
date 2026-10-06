"""
MQTT: KPI subscribe + control publish. Same topics as web_api / start_system.
No FastAPI dependency.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

import paho.mqtt.client as mqtt

from paths import ensure_paths

ensure_paths()
import common  # noqa: E402


def _logger_backend() -> logging.Logger:
    fn = getattr(common, "setup_logger", None)
    if callable(fn):
        return fn("mqtt_backend", logging.INFO)
    lg = logging.getLogger("mqtt_backend")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        lg.addHandler(h)
    return lg


_log = _logger_backend()

_cfg: dict | None = None
# 单一连接：订阅 KPI + 发布控制命令（双连接在公共 broker 上易被踢 rc=7 CONN_LOST）
_dash_client: mqtt.Client | None = None
_started = False
_last_disconnect_log_ts: float = 0.0
# 与 broker 约定 keepalive（秒）；过短易误判断线，过长恢复慢
MQTT_DASH_KEEPALIVE_SEC = 90
_kpi_lock = threading.Lock()
_latest_kpi: dict = {}
_last_kpi_time: float = 0.0
_mqtt_kpi_connected = False
_kpi_connect_ts: float = 0.0
# 改进4：KPI 新鲜度 / 首包等待
MQTT_KPI_STALE_SEC = 12.0
MQTT_KPI_GRACE_SEC = 28.0
# Direct CSV replay writes KPI snapshots here (no MQTT from replay worker).
REPLAY_KPI_STALE_SEC = 30.0
_KPI_HISTORY_MAX = 100
_kpi_history: deque = deque(maxlen=_KPI_HISTORY_MAX)


def load_cfg() -> dict:
    """Same config file as subsystems (respects CONFIG_FILE env)."""
    global _cfg
    if _cfg is None:
        _cfg = common.load_config(active_config_name())
    return _cfg


def active_config_name() -> str:
    return os.environ.get("CONFIG_FILE", "config.json")


def _replay_kpi_file() -> str:
    from paths import PROJECT_ROOT

    return os.path.normpath(os.path.join(PROJECT_ROOT, ".replay_kpi.json"))


def _replay_blob_usable(blob: dict) -> bool:
    """Replay sidecar is readable: in-progress (fresh) or finished (``completed``)."""
    if not isinstance(blob, dict):
        return False
    t = float(blob.get("t") or 0)
    data = blob.get("data")
    if not isinstance(data, dict) or not data:
        return False
    if t <= 0:
        return False
    if blob.get("completed"):
        return True
    return (time.time() - t) <= REPLAY_KPI_STALE_SEC


def _live_prefers_mqtt_over_replay() -> bool:
    """Control Panel **Live Monitoring**: KPI must not be satisfied by ``.replay_kpi.json``."""
    try:
        import streamlit as st  # noqa: PLC0415

        return st.session_state.get("cp_data_source") == "live"
    except Exception:
        return False


def physical_kpi_session_id() -> str | None:
    """Live lab: session_id from latest KPI payload when ``run_mode`` is physical (main_service).

    Aligns Digital Twin / Part Track with the same Neo4j session as the MQTT pipeline,
    instead of ``get_latest_session_info()`` (which can pick another session if replay/import
    has newer events).
    """
    kpi, _ = get_kpi_snapshot()
    if not isinstance(kpi, dict):
        return None
    if (kpi.get("run_mode") or "") != "physical":
        return None
    sid = kpi.get("session_id")
    if sid is None:
        return None
    s = str(sid).strip()
    return s or None


def get_replay_pipeline_session_id() -> str | None:
    """Neo4j session id from replay worker KPI sidecar (ignored in Live Monitoring mode)."""
    if _live_prefers_mqtt_over_replay():
        return None
    try:
        rp = _replay_kpi_file()
        if not os.path.isfile(rp):
            return None
        with open(rp, encoding="utf-8") as f:
            blob = json.load(f)
        if not _replay_blob_usable(blob):
            return None
        data = blob.get("data")
        if isinstance(data, dict):
            sid = data.get("session_id")
            if sid:
                return str(sid)
    except Exception:
        pass
    return None


def resolve_digital_twin_neo4j_session_id() -> str | None:
    """Same Neo4j session as the active KPI: MQTT / ``.replay_kpi`` / history pick / offline snapshot (as 01_Realtime)."""
    import services.neo4j_backend as neo4j_backend
    import streamlit as st  # noqa: PLC0415

    kpi, _ = get_kpi_snapshot()
    ds = st.session_state.get("cp_data_source")
    dts = st.session_state.get("dt_resolved_session")

    def _from_kpi() -> str | None:
        s = (kpi or {}).get("session_id")
        if s is None:
            return None
        t = str(s).strip()
        return t or None

    if ds == "live":
        sid = physical_kpi_session_id() or _from_kpi()
        if sid:
            return sid
    else:
        rps = get_replay_pipeline_session_id()
        sid = (rps or dts or _from_kpi())
        if sid:
            return sid

    if not kpi_connected():
        q = None if ds == "live" else dts
        alt = neo4j_backend.get_session_kpi_snapshot(q)
        if alt and (str(alt.get("session_id") or "").strip()):
            return str(alt.get("session_id")).strip()
    return None


def clear_kpi_snapshot():
    global _latest_kpi, _last_kpi_time
    with _kpi_lock:
        _latest_kpi = {}
        _last_kpi_time = 0.0
        _kpi_history.clear()
    try:
        rp = _replay_kpi_file()
        if os.path.isfile(rp):
            os.unlink(rp)
    except OSError:
        pass


def switch_config_file(filename: str):
    """切换 CONFIG_FILE、重连 MQTT、关闭 Neo4j driver 缓存（下次查询用新库配置）。"""
    global _cfg, _started, _dash_client, _mqtt_kpi_connected, _kpi_connect_ts
    import services.neo4j_backend as neo4j_backend

    os.environ["CONFIG_FILE"] = filename
    _cfg = None
    if _dash_client is not None:
        try:
            _dash_client.loop_stop()
            _dash_client.disconnect()
        except Exception:
            pass
        _dash_client = None
    _started = False
    _mqtt_kpi_connected = False
    _kpi_connect_ts = 0.0
    neo4j_backend.close_driver()
    ensure_started()


def _on_dash_connect(client, userdata, flags, rc):
    global _mqtt_kpi_connected, _kpi_connect_ts
    host, port = userdata if userdata else ("", "")
    if rc == 0:
        _mqtt_kpi_connected = True
        _kpi_connect_ts = time.time()
        topic = common.render_topic("kpi", "main_service", "all")
        client.subscribe(topic, qos=0)
        _log.info(
            "MQTT dashboard connected to %s:%s (single client: KPI + control)",
            host,
            port,
        )
    else:
        _mqtt_kpi_connected = False
        _kpi_connect_ts = 0.0
        _log.error("MQTT dashboard on_connect failed rc=%s", rc)


def _on_dash_disconnect(client, userdata, rc):
    global _mqtt_kpi_connected, _kpi_connect_ts, _last_disconnect_log_ts
    if rc != 0:
        _mqtt_kpi_connected = False
        _kpi_connect_ts = 0.0
    now = time.time()
    if rc == 0:
        return
    if now - _last_disconnect_log_ts < 25.0:
        return
    _last_disconnect_log_ts = now
    try:
        reason = mqtt.error_string(rc)
    except Exception:
        reason = "rc={}".format(rc)
    # ASCII only: Windows consoles often mangle Unicode dashes in logs.
    _log.warning(
        "MQTT dashboard disconnect: %s - paho will retry. "
        "Physical line: set mqtt_broker_host to the SAME reachable IP/hostname as "
        "main_service and PLCs (not broker.hivemq.com unless that is your broker). "
        "Check WiFi/VPN, firewall on 1883, and Mosquitto max_keepalive / idle limits.",
        reason,
    )


def _on_kpi_message(client, userdata, msg):
    global _latest_kpi, _last_kpi_time
    try:
        data = common.deserialize_object(msg.payload.decode("utf-8"))
        with _kpi_lock:
            _latest_kpi = data
            _last_kpi_time = time.time()
            d = dict(data)
            chart_ts = d.get("chart_time_unix")
            try:
                chart_ts = float(chart_ts) if chart_ts is not None else None
            except (TypeError, ValueError):
                chart_ts = None
            _kpi_history.append(
                {
                    "t": _last_kpi_time,
                    "chart_time_unix": chart_ts if chart_ts is not None else _last_kpi_time,
                    "throughput": d.get("throughput", 0.0),
                    "finished_count": d.get("finished_count", 0),
                    "scrap_count": d.get("scrap_count", 0),
                    "yield_rate": d.get("yield_rate", 0.0),
                    "scrap_rate": d.get("scrap_rate", 0.0),
                    "observation_time_sec": d.get("observation_time_sec", 0.0),
                    "avg_flow_time_sec": d.get("avg_flow_time_sec", 0.0),
                }
            )
    except Exception as e:
        _log.warning("KPI parse error: %s", e)


def _dash_tcp_connect(host: str, port: int, keepalive: int) -> None:
    """Blocking TCP+MQTT handshake; run in a thread so Streamlit does not freeze."""
    global _dash_client
    if _dash_client is None:
        return
    try:
        _dash_client.connect(host, port=port, keepalive=keepalive)
        _dash_client.loop_start()
    except Exception as e:
        _log.error("MQTT dashboard connect failed: %s", e)


def ensure_started():
    """Idempotent: one MQTT client subscribes KPI and publishes control commands."""
    global _started, _dash_client
    if _started:
        return
    cfg = load_cfg()
    host = cfg["mqtt_broker_host"]
    port = int(cfg["mqtt_broker_port"])
    cfg_name = active_config_name()
    _log.info(
        "MQTT dashboard will connect to %s:%s (CONFIG_FILE=%s)",
        host,
        port,
        cfg_name,
    )
    # 显式 client_id，避免多会话互挤
    cid = "ldsh_{}_{}".format(os.getpid(), uuid.uuid4().hex[:8])
    _dash_client = mqtt.Client(client_id=cid, clean_session=True)
    _dash_client.user_data_set((host, port))
    _dash_client.on_connect = _on_dash_connect
    _dash_client.on_disconnect = _on_dash_disconnect
    _dash_client.on_message = _on_kpi_message
    _dash_client.reconnect_delay_set(min_delay=1, max_delay=30)
    ka = MQTT_DASH_KEEPALIVE_SEC
    threading.Thread(
        target=_dash_tcp_connect,
        args=(host, port, ka),
        daemon=True,
        name="mqtt_dash_connect",
    ).start()
    _started = True


def wait_for_control_mqtt(timeout_sec: float = 25.0) -> None:
    """
    connect_async is non-blocking; publish must wait until the session is up.
    Raises RuntimeError if broker stays unreachable (check host/port and firewall).
    """
    ensure_started()
    c = _dash_client
    if c is None:
        raise RuntimeError("MQTT dashboard client not started")
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        if c.is_connected():
            return
        time.sleep(0.05)
    cfg = load_cfg()
    host = cfg.get("mqtt_broker_host", "")
    port = cfg.get("mqtt_broker_port", "")
    raise RuntimeError(
        "MQTT dashboard did not connect within {:.0f}s (broker {!r}, port {}). "
        "Ensure the broker is running and this PC can reach it. "
        "If Streamlit runs on Windows while the broker hostname is only valid inside Docker/LAN, "
        "set mqtt_broker_host to localhost, 127.0.0.1, or the broker machine IP in config.json / CONFIG_FILE.".format(
            float(timeout_sec),
            host,
            port,
        )
    )


def kpi_connected() -> bool:
    """KPI source: MQTT from main_service, or (non-Live mode only) direct replay sidecar."""
    if not _live_prefers_mqtt_over_replay():
        try:
            rp = _replay_kpi_file()
            if os.path.isfile(rp):
                with open(rp, encoding="utf-8") as f:
                    blob = json.load(f)
                if _replay_blob_usable(blob):
                    return True
        except Exception:
            pass
    if not _mqtt_kpi_connected:
        return False
    now = time.time()
    with _kpi_lock:
        last = _last_kpi_time
    if last > 0:
        if now - last > MQTT_KPI_STALE_SEC:
            return False
    else:
        if _kpi_connect_ts > 0 and now - _kpi_connect_ts > MQTT_KPI_GRACE_SEC:
            return False
    return True


def wait_for_kpi_ready(timeout_sec: float = 45.0) -> None:
    """
    After main_service starts, block until we see KPI on MQTT (same broker as dashboard).
    Avoids sending START before the pipeline is up; also catches broker misconfiguration early.
    """
    ensure_started()
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        if kpi_connected():
            return
        time.sleep(0.2)
    cfg = load_cfg()
    host = cfg.get("mqtt_broker_host", "")
    port = cfg.get("mqtt_broker_port", "")
    raise RuntimeError(
        "No KPI from main_service within {:.0f}s (broker {!r}, port {}). "
        "Check: main_service running, same CONFIG_FILE / mqtt_broker_host as dashboard, "
        "firewall allows MQTT, and public brokers (e.g. HiveMQ) allow your network.".format(
            float(timeout_sec),
            host,
            port,
        )
    )


def get_kpi_snapshot() -> tuple[dict, float]:
    """Return (kpi_dict, last_update_unix).

    **Live Monitoring** (``cp_data_source == "live"``): MQTT only — never ``.replay_kpi.json``.
    Otherwise: prefer usable replay sidecar, then MQTT buffer.
    """
    if not _live_prefers_mqtt_over_replay():
        try:
            rp = _replay_kpi_file()
            if os.path.isfile(rp):
                with open(rp, encoding="utf-8") as f:
                    blob = json.load(f)
                t = float(blob.get("t", 0))
                data = blob.get("data")
                if isinstance(data, dict) and data and _replay_blob_usable(blob):
                    return dict(data), t
        except Exception:
            pass
    with _kpi_lock:
        return dict(_latest_kpi), _last_kpi_time


def get_kpi_history(max_points: int | None = None) -> list[dict]:
    """最近 KPI 快照（用于趋势图），线程安全。"""
    n = max_points if max_points is not None else _KPI_HISTORY_MAX
    with _kpi_lock:
        rows = list(_kpi_history)
    return rows[-n:] if n < len(rows) else rows


def start_physical_system():
    wait_for_control_mqtt()
    cfg = load_cfg()
    c = _dash_client
    assert c is not None
    delay = float(cfg.get("system_start_delay", 5.0))
    for component_id, wip in cfg.get("component_wips", {}).items():
        topic = common.render_topic("component_wip", "master", component_id)
        c.publish(topic, common.serialize_object(wip), qos=2)
    time.sleep(delay)
    topic = common.render_topic("system_status", "master", "all")
    c.publish(topic, "START", qos=2)


def stop_physical_system():
    wait_for_control_mqtt()
    c = _dash_client
    assert c is not None
    topic = common.render_topic("system_status", "master", "all")
    c.publish(topic, "STOP", qos=2)


def publish_main_service_command(action: str, **kwargs):
    wait_for_control_mqtt()
    c = _dash_client
    assert c is not None
    topic = common.render_topic("command", "dashboard", "main_service")
    payload = common.serialize_object({"action": action, **kwargs})
    info = c.publish(topic, payload=payload, qos=2)
    info.wait_for_publish(timeout=2.0)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError("MQTT publish failed (rc={})".format(info.rc))


def run_replay_subprocess(csv_path: str, speed: float) -> subprocess.Popen:
    """Run replay_csv_direct.py: CSV -> buffer/KPI/Neo4j (no MQTT event publish)."""
    from paths import PROJECT_ROOT

    script = os.path.join(PROJECT_ROOT, "replay_csv_direct.py")
    env = os.environ.copy()
    env["CONFIG_FILE"] = active_config_name()
    kw: dict = {}
    if sys.platform == "win32":
        try:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        except AttributeError:
            pass
    return subprocess.Popen(
        [sys.executable, script, csv_path, str(speed)],
        cwd=PROJECT_ROOT,
        env=env,
        **kw,
    )


def run_replay_session_subprocess(source_session_id: str, speed: float) -> subprocess.Popen:
    """Run replay_session_direct.py: read events from existing Session, KPI replay only (no new Neo4j session)."""
    from paths import PROJECT_ROOT

    script = os.path.join(PROJECT_ROOT, "replay_session_direct.py")
    env = os.environ.copy()
    env["CONFIG_FILE"] = active_config_name()
    kw: dict = {}
    if sys.platform == "win32":
        try:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        except AttributeError:
            pass
    return subprocess.Popen(
        [sys.executable, script, str(source_session_id), str(speed)],
        cwd=PROJECT_ROOT,
        env=env,
        **kw,
    )

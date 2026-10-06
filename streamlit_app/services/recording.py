"""
订阅 component_event 并写入 CSV — 与 web_api 录制逻辑一致。
"""
from __future__ import annotations

import csv
import datetime
import logging
import os
import sys
import threading

import paho.mqtt.client as mqtt

from paths import PROJECT_ROOT, ensure_paths

ensure_paths()
import common  # noqa: E402
import services.mqtt_backend as mqtt_backend  # noqa: E402


def _logger_rec() -> logging.Logger:
    fn = getattr(common, "setup_logger", None)
    if callable(fn):
        return fn("recording", logging.INFO)
    lg = logging.getLogger("recording")
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


_log = _logger_rec()

_record_active = False
_record_file = None
_record_writer = None
_record_mqtt_client: mqtt.Client | None = None
_record_lock = threading.Lock()
_record_path: str | None = None
_record_columns: list[str] = [
    "time",
    "component_id",
    "part_id",
    "activity",
]


def _record_on_connect(client, userdata, flags, rc):
    if rc == 0:
        topic = common.render_topic("component_event", "+", "all")
        client.subscribe(topic, qos=2)


def _record_on_message(client, userdata, msg):
    global _record_writer, _record_file
    with _record_lock:
        if not _record_active or _record_writer is None:
            return
        try:
            context, _, _ = common.parse_topic(msg.topic)
            if context == "component_event":
                event = common.deserialize_object(msg.payload.decode("utf-8"))
                row = [event.get(c, "") for c in _record_columns]
                _record_writer.writerow(row)
                if _record_file:
                    _record_file.flush()
        except Exception as e:
            _log.warning("write error: %s", e)


def is_recording() -> bool:
    return _record_active


def current_path() -> str | None:
    return _record_path if _record_active else None


def start_recording() -> tuple[bool, str]:
    global _record_active, _record_file, _record_writer, _record_mqtt_client, _record_path
    global _record_columns
    mqtt_backend.ensure_started()
    cfg = mqtt_backend.load_cfg()
    host = cfg["mqtt_broker_host"]
    port = cfg["mqtt_broker_port"]
    log_folder = cfg.get("log_folder", "event-logs")
    _record_columns = cfg.get(
        "log_columns", ["time", "component_id", "part_id", "activity"]
    )
    path = None
    with _record_lock:
        if _record_active:
            return False, "Already recording"
        try:
            log_dir = os.path.normpath(os.path.join(PROJECT_ROOT, log_folder))
            os.makedirs(log_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
            _record_path = os.path.normpath(
                os.path.join(log_dir, "event_log_{}.csv".format(ts))
            )
            _record_file = open(_record_path, "w", newline="", encoding="utf-8")
            _record_writer = csv.writer(_record_file)
            _record_writer.writerow(_record_columns)
            _record_file.flush()
            _record_mqtt_client = mqtt.Client()
            _record_mqtt_client.on_connect = _record_on_connect
            _record_mqtt_client.on_message = _record_on_message
            _record_mqtt_client.connect(host, port=port)
            _record_mqtt_client.loop_start()
            _record_active = True
            path = _record_path
        except Exception as e:
            if _record_file:
                try:
                    _record_file.close()
                except Exception:
                    pass
                _record_file = None
            _record_writer = None
            _record_path = None
            _record_mqtt_client = None
            return False, "Failed to start recording: " + str(e)
    return True, path or ""


def stop_recording() -> tuple[bool, str]:
    global _record_active, _record_file, _record_writer, _record_mqtt_client, _record_path
    path = None
    with _record_lock:
        if not _record_active:
            return False, "Not recording"
        _record_active = False
        path = _record_path
        _record_path = None
        if _record_mqtt_client:
            try:
                _record_mqtt_client.loop_stop()
                _record_mqtt_client.disconnect()
            except Exception:
                pass
            _record_mqtt_client = None
        if _record_file:
            try:
                _record_file.close()
            except Exception:
                pass
            _record_file = None
        _record_writer = None
    return True, path or ""

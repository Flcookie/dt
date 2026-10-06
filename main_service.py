# main_service.py - MQTT -> buffer -> Neo4j + KPI, print snapshot every 2s

import logging
import os
import sys
import time
from datetime import datetime

import common
import paho.mqtt.client as mqtt

try:
    import psutil
except ImportError:
    psutil = None

import event_pipeline
import neo4j_writer

# 与 Streamlit / replay 脚本一致：可用 CONFIG_FILE=config_lab.json 指向实验室配置
CONFIG = common.load_config(os.environ.get("CONFIG_FILE", "config.json"))
MQTT_BROKER_HOST = CONFIG["mqtt_broker_host"]
MQTT_BROKER_PORT = CONFIG["mqtt_broker_port"]
BUFFER_CFG = CONFIG.get("event_buffer", {})
_replay_mode = "--replay" in sys.argv
pipeline = event_pipeline.EventPipeline(CONFIG, replay_mode=_replay_mode)
_last_flush_count = 0

PID_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "main_service.pid"))
LOG_FOLDER = CONFIG.get("log_folder", "event-logs")
os.makedirs(LOG_FOLDER, exist_ok=True)
LOG_TIME = datetime.now().strftime("%y%m%d_%H%M%S")
LOG_FILE = "kpi_log_{}.txt".format(LOG_TIME)
LOG_PATH = os.path.normpath(os.path.join(LOG_FOLDER, LOG_FILE))
APP_LOG_PATH = os.path.normpath(os.path.join(LOG_FOLDER, "main_service_{}.log".format(LOG_TIME)))

logger = common.setup_logger("main_service", logging.INFO, log_file=APP_LOG_PATH)


def _check_single_instance():
    """Enforce single instance via PID file. Exit if another main_service is running."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            if psutil and psutil.pid_exists(old_pid):
                try:
                    proc = psutil.Process(old_pid)
                    cmd = " ".join(proc.cmdline() or [])
                    if "main_service.py" in cmd and "web_api" not in cmd:
                        logger.error(
                            "Already running (PID %s). Stop it first.", old_pid
                        )
                        sys.exit(1)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # Stale PID file, remove it
        except (ValueError, OSError):
            pass
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.warning("Cannot write PID file: %s", e)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT")
        client.subscribe(common.render_topic("component_event", "+", "all"), qos=2)
        client.subscribe(common.render_topic("command", "+", "main_service"), qos=2)
    else:
        logger.error("MQTT connection failed, code %s", rc)


def on_disconnect(client, userdata, rc):
    if rc != 0:
        logger.warning("Unexpected disconnect (rc=%s), will auto-reconnect", rc)


def on_message(client, userdata, msg):
    global _last_flush_count
    try:
        context, source_id, target_id = common.parse_topic(msg.topic)
        payload = msg.payload.decode("utf-8")
        if context == "command":
            try:
                cmd = common.deserialize_object(payload)
                pipeline.process_command(cmd)
            except Exception as e:
                logger.exception("Command error: %s", e)
            return
        if context != "component_event":
            return
        event = common.deserialize_object(payload)
        n, n_forced = pipeline.ingest_event(event)
        _last_flush_count = n
    except Exception as e:
        logger.exception("on_message error: %s", e)


def _print_kpi_snapshot(snap: dict, log_file=None):
    """Print KPI snapshot, optionally write to log_file. 所有数值统一为浮点格式。"""
    lines = []
    lines.append("[Session] {}".format(pipeline.session_id or "N/A"))
    sysb = snap.get("system") or {}
    cr = float(sysb.get("complete_rate", snap.get("throughput", 0)) or 0)
    f = int(sysb.get("num_completions", snap.get("finished_count", 0)) or 0)
    sct = int(sysb.get("num_scraps", snap.get("scrap_count", 0)) or 0)
    sr = float(sysb.get("scrap_rate", snap.get("scrap_rate", 0)) or 0)
    o = float(snap["observation_time_sec"])
    a_fin = float(sysb.get("avg_cycle_time_fin", snap.get("avg_flow_time_sec", 0)) or 0)
    a_all = float(sysb.get("avg_cycle_time_all", snap.get("avg_cycle_time_all_sec", 0)) or 0)
    wip = int(sysb.get("wip_instantaneous", snap.get("current_wip", 0)) or 0)
    awip = float(sysb.get("wip_average", snap.get("avg_wip", 0)) or 0)
    lines.append(
        "[KPI system] NumCompletions={} NumScraps={} WIP={} AvgWIP={:.3f} CompleteRate={:.4f}/s "
        "ScrapRate={:.3f} AvgCycleFin={:.1f}s AvgCycleAll={:.1f}s obs_time={:.1f}s".format(
            f, sct, wip, awip, cr, sr, a_fin, a_all, o
        )
    )
    stg = snap.get("stages") or {}
    for sk in sorted(stg.keys(), key=lambda x: int(str(x).replace("stage", "") or 0)):
        row = stg[sk]
        sn = str(sk).replace("stage", "") if str(sk).startswith("stage") else sk
        lines.append(
            "[KPI stage {}] WIP={} AvgWIP={:.3f} NumDepartures={} Throughput={:.4f}/s AvgFlow={:.1f}s".format(
                sn,
                row.get("wip_instantaneous", row.get("instantaneous_wip", 0)),
                float(row.get("wip_average", row.get("avg_wip", 0)) or 0),
                row.get("num_departures", row.get("departures", 0)),
                float(row.get("throughput", row.get("throughput_per_sec", 0)) or 0),
                float(row.get("avg_flow_time", row.get("avg_flow_time_sec", 0)) or 0),
            )
        )
    lines.append(
        "[Buffer] size={}, flush_last={}, flush_2s={}, total={}".format(
            pipeline.buffer.size,
            _last_flush_count,
            pipeline.flush_since_last_print,
            pipeline.total_flush_count,
        )
    )
    for sid, probs in snap.get("state_probability", {}).items():
        util = snap["utilization"].get(sid, 0)
        lines.append("  {}".format(sid))
        lines.append("    Utilization (P_busy): {:.2f}".format(util))
        lines.append(
            "    P_busy: {:.2f}  P_fail: {:.2f}  P_blocked: {:.2f}  P_idle: {:.2f}".format(
                probs.get("busy", probs.get("loading", 0)),
                probs.get("fail", 0),
                probs.get("blocked", 0),
                probs.get("idle", 0),
            )
        )
    text = "\n".join(lines)
    for line in lines:
        logger.info("%s", line)
    if log_file:
        log_file.write(text + "\n")
        log_file.flush()


_check_single_instance()

_sid0 = common.new_event_log_session_id()
try:
    pipeline.init_session(_sid0, "live", start_time_iso=datetime.now().isoformat())
    logger.info("Neo4j init OK, session: %s", _sid0)
except Exception as e:
    logger.error("Neo4j init error: %s", e)

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
mqtt_client.connect(MQTT_BROKER_HOST, port=MQTT_BROKER_PORT)
mqtt_client.loop_start()

# KPI 打印/发布间隔固定 2 秒（replay 时 buffer 用 replay_window_ms 平滑 WIP，但打印仍 2 秒一次）
KPI_PRINT_INTERVAL = BUFFER_CFG.get("kpi_print_interval_sec", 2.0)

kpi_log_file = open(LOG_PATH, "w", encoding="utf-8")


def _log(msg: str):
    logger.info(msg)
    kpi_log_file.write(msg + "\n")
    kpi_log_file.flush()


_log("[main_service] Started. Buffer + Neo4j + KPI. Ctrl+C to stop.")
_log("[main_service] KPI log: {}".format(LOG_PATH))
_log("[main_service] App log: {}".format(APP_LOG_PATH))
try:
    while True:
        time.sleep(KPI_PRINT_INTERVAL)
        snap = pipeline.get_snapshot()
        _print_kpi_snapshot(snap, kpi_log_file)
        pipeline.flush_since_last_print = 0  # reset for next interval

        # Publish KPI to MQTT for web dashboard（含 session_id 供 UI 展示）
        try:
            topic = common.render_topic("kpi", "main_service", "all")
            pub = pipeline.kpi_publish_payload()
            mqtt_client.publish(topic, common.serialize_object(pub), qos=0)
        except Exception as e:
            logger.error("KPI publish error: %s", e)
except KeyboardInterrupt:
    pass
finally:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    neo4j_writer.close()
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass
    _log("[main_service] Stopped.")
    kpi_log_file.close()

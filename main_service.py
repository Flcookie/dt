# main_service.py - MQTT -> buffer -> Neo4j + KPI; logs and publishes a KPI snapshot every
# event_buffer.kpi_print_interval_sec seconds (default 2).

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

PID_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "main_service.pid"))

# Set by main(). Shared with the MQTT callbacks, which run on paho's network thread, and with
# the KPI report on the main thread.
pipeline: event_pipeline.EventPipeline | None = None
_last_flush_count = 0
# Console + app-log handlers are attached in main() (common.setup_logger on this logger).
logger = logging.getLogger("main_service")


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


def format_kpi_summary_lines(snap: dict) -> list[str]:
    """System KPI line and one line per stage of the periodic KPI report.

    Reads the snapshot's ``system`` block first and falls back to the older top-level keys.
    """
    lines = []
    sysb = snap.get("system") or {}
    complete_rate = float(sysb.get("complete_rate", snap.get("throughput", 0)) or 0)
    n_completions = int(sysb.get("num_completions", snap.get("finished_count", 0)) or 0)
    n_scraps = int(sysb.get("num_scraps", snap.get("scrap_count", 0)) or 0)
    scrap_rate = float(sysb.get("scrap_rate", snap.get("scrap_rate", 0)) or 0)
    obs_time = float(snap["observation_time_sec"])
    avg_cycle_fin = float(sysb.get("avg_cycle_time_fin", snap.get("avg_flow_time_sec", 0)) or 0)
    avg_cycle_all = float(sysb.get("avg_cycle_time_all", snap.get("avg_cycle_time_all_sec", 0)) or 0)
    wip = int(sysb.get("wip_instantaneous", snap.get("current_wip", 0)) or 0)
    avg_wip = float(sysb.get("wip_average", snap.get("avg_wip", 0)) or 0)
    lines.append(
        "[KPI system] NumCompletions={} NumScraps={} WIP={} AvgWIP={:.3f} CompleteRate={:.4f}/s "
        "ScrapRate={:.3f} AvgCycleFin={:.1f}s AvgCycleAll={:.1f}s obs_time={:.1f}s".format(
            n_completions, n_scraps, wip, avg_wip, complete_rate, scrap_rate, avg_cycle_fin,
            avg_cycle_all, obs_time
        )
    )
    stages = snap.get("stages") or {}
    for stage_key in sorted(stages.keys(), key=lambda x: int(str(x).replace("stage", "") or 0)):
        row = stages[stage_key]
        stage_no = str(stage_key).replace("stage", "") if str(stage_key).startswith("stage") else stage_key
        lines.append(
            "[KPI stage {}] WIP={} AvgWIP={:.3f} NumDepartures={} Throughput={:.4f}/s AvgFlow={:.1f}s".format(
                stage_no,
                row.get("wip_instantaneous", row.get("instantaneous_wip", 0)),
                float(row.get("wip_average", row.get("avg_wip", 0)) or 0),
                row.get("num_departures", row.get("departures", 0)),
                float(row.get("throughput", row.get("throughput_per_sec", 0)) or 0),
                float(row.get("avg_flow_time", row.get("avg_flow_time_sec", 0)) or 0),
            )
        )
    return lines


def format_station_state_lines(snap: dict) -> list[str]:
    """Utilization and state probabilities per station, the last part of the KPI report."""
    lines = []
    for station_id, probs in snap.get("state_probability", {}).items():
        util = snap["utilization"].get(station_id, 0)
        lines.append("  {}".format(station_id))
        lines.append("    Utilization (P_busy): {:.2f}".format(util))
        lines.append(
            "    P_busy: {:.2f}  P_fail: {:.2f}  P_blocked: {:.2f}  P_idle: {:.2f}".format(
                probs.get("busy", probs.get("loading", 0)),
                probs.get("fail", 0),
                probs.get("blocked", 0),
                probs.get("idle", 0),
            )
        )
    return lines


def _log_kpi_report(snap: dict, log_file=None):
    """Write the KPI report for ``snap`` to the app log, and to ``log_file`` (the kpi_log) if given.

    The session id and the buffer counters are shared with the MQTT callback thread, so they
    are read here, at their place in the report: after the summary lines are built (a bad
    snapshot fails before any counter is read) and before the station lines.
    """
    lines = ["[Session] {}".format(pipeline.session_id or "N/A")]
    lines += format_kpi_summary_lines(snap)
    lines.append(
        "[Buffer] size={}, flush_last={}, flush_2s={}, total={}".format(
            pipeline.buffer.size,
            _last_flush_count,
            pipeline.flush_since_last_print,
            pipeline.total_flush_count,
        )
    )
    lines += format_station_state_lines(snap)
    for line in lines:
        logger.info("%s", line)
    if log_file:
        log_file.write("\n".join(lines) + "\n")
        log_file.flush()


def _start_live_session():
    """Create this run's live Session in Neo4j; on failure only log (events are still processed)."""
    session_id = common.new_event_log_session_id()
    try:
        pipeline.init_session(session_id, "live", start_time_iso=datetime.now().isoformat())
        logger.info("Neo4j init OK, session: %s", session_id)
    except Exception as e:
        logger.error("Neo4j init error: %s", e)


def _connect_mqtt(host, port) -> mqtt.Client:
    """Connect to the broker and start paho's network thread (callbacks run there)."""
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=120)
    client.connect(host, port=port)
    client.loop_start()
    return client


def _log(kpi_log_file, msg: str):
    logger.info(msg)
    kpi_log_file.write(msg + "\n")
    kpi_log_file.flush()


def _run_kpi_loop(mqtt_client, interval, kpi_log_file):
    """Every ``interval`` seconds: log the KPI report, reset the per-interval flush count and
    publish the KPI payload for the dashboard. Runs until interrupted."""
    while True:
        time.sleep(interval)
        snap = pipeline.get_snapshot()
        _log_kpi_report(snap, kpi_log_file)
        pipeline.flush_since_last_print = 0  # reset for next interval

        # Publish KPI to MQTT for web dashboard（含 session_id 供 UI 展示）
        try:
            topic = common.render_topic("kpi", "main_service", "all")
            pub = pipeline.kpi_publish_payload()
            mqtt_client.publish(topic, common.serialize_object(pub), qos=0)
        except Exception as e:
            logger.error("KPI publish error: %s", e)


def _shutdown(mqtt_client, kpi_log_file):
    """Stop MQTT, close Neo4j, remove the PID file and close the KPI log."""
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    neo4j_writer.close()
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass
    _log(kpi_log_file, "[main_service] Stopped.")
    kpi_log_file.close()


def main():
    """Start the service, run the KPI loop until Ctrl+C, then clean up.

    Only the KPI loop is covered by the cleanup (as before): a failure while starting up
    (e.g. MQTT connect) leaves the PID file in place.
    """
    global pipeline
    # 与 Streamlit / replay 脚本一致：可用 CONFIG_FILE=config_lab.json 指向实验室配置
    config = common.load_config(os.environ.get("CONFIG_FILE", "config.json"))
    mqtt_broker_host = config["mqtt_broker_host"]
    mqtt_broker_port = config["mqtt_broker_port"]
    buffer_cfg = config.get("event_buffer", {})
    replay_mode = "--replay" in sys.argv
    pipeline = event_pipeline.EventPipeline(config, replay_mode=replay_mode)

    log_folder = config.get("log_folder", "event-logs")
    os.makedirs(log_folder, exist_ok=True)
    log_time = datetime.now().strftime("%y%m%d_%H%M%S")
    kpi_log_path = os.path.normpath(os.path.join(log_folder, "kpi_log_{}.txt".format(log_time)))
    app_log_path = os.path.normpath(os.path.join(log_folder, "main_service_{}.log".format(log_time)))
    common.setup_logger("main_service", logging.INFO, log_file=app_log_path)

    _check_single_instance()
    _start_live_session()
    mqtt_client = _connect_mqtt(mqtt_broker_host, mqtt_broker_port)

    # KPI 记录/发布间隔：event_buffer.kpi_print_interval_sec，默认 2 秒；replay 模式也用同一间隔
    # （replay 只把 buffer 窗口换成 replay_window_ms）。
    kpi_interval = buffer_cfg.get("kpi_print_interval_sec", 2.0)
    kpi_log_file = open(kpi_log_path, "w", encoding="utf-8")
    _log(kpi_log_file, "[main_service] Started. Buffer + Neo4j + KPI. Ctrl+C to stop.")
    _log(kpi_log_file, "[main_service] KPI log: {}".format(kpi_log_path))
    _log(kpi_log_file, "[main_service] App log: {}".format(app_log_path))
    try:
        _run_kpi_loop(mqtt_client, kpi_interval, kpi_log_file)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown(mqtt_client, kpi_log_file)


if __name__ == "__main__":
    main()

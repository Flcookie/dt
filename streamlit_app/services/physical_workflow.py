"""
Physical line workflow: start ensures main_service (realtime), MQTT START, optional recording;
stop sends MQTT STOP, stops recording, stops main_service, clears local twin state.
"""
from __future__ import annotations

import datetime
import time


def reset_realtime_dashboard_data(
    *, notify_main_service: bool = True, clear_graph: bool = True
) -> tuple[str, str]:
    """Clear KPI snapshot; optionally clear Neo4j graph and notify main_service."""
    import services.mqtt_backend as mqtt_backend
    import services.neo4j_backend as neo4j_backend

    try:
        mqtt_backend.clear_kpi_snapshot()
        if clear_graph:
            neo4j_backend.clear_neo4j_graph()
    except Exception as ex:
        return "error", str(ex)
    if not clear_graph:
        return "success", "Cleared KPI snapshot."
    if not notify_main_service:
        return "success", "Cleared KPI snapshot and Neo4j graph."
    try:
        mqtt_backend.publish_main_service_command("clear_neo4j")
    except Exception:
        return (
            "warning",
            "Cleared locally; could not notify main_service (retry or restart main_service).",
        )
    return (
        "success",
        "Cleared KPI + graph and notified main_service (new session).",
    )


def start_physical_line_integrated() -> tuple[bool, str]:
    import services.mqtt_backend as mqtt_backend
    import services.process_control as process_control
    import services.recording as recording

    mqtt_backend.switch_config_file("config.json")

    if process_control.main_service_status().get("running"):
        kpi, _ = mqtt_backend.get_kpi_snapshot()
        if kpi.get("run_mode") == "replay":
            return (
                False,
                "main_service is in **replay** mode — cannot start the physical line. "
                "Stop main_service on **Realtime**, then start in **realtime**.",
            )
    else:
        ok, msg = process_control.start_main_service("realtime")
        if not ok:
            return False, msg

    # 1) main_service → 2) 等 KPI（MQTT 通）→ 3) 录制（等同 record_events.py 订阅写 CSV）→ 4) START
    try:
        mqtt_backend.wait_for_kpi_ready()
    except RuntimeError as ex:
        return False, str(ex)

    chunks: list[str] = []
    if recording.is_recording():
        chunks.append("Recording already running.")
    else:
        ok_rec, res = recording.start_recording()
        if ok_rec:
            chunks.append("**Recording** (event CSV) started: `{}`.".format(res))
        else:
            return (
                False,
                "Recording failed (fix MQTT or permissions) — not sending START. {}".format(res),
            )

    try:
        mqtt_backend.start_physical_system()
    except RuntimeError as ex:
        return False, str(ex)

    parts = [
        "**main_service (realtime)** up; **physical line START** sent (WIP + system_status).",
    ]
    parts.extend(chunks)
    return True, " ".join(parts)


def stop_physical_line_integrated() -> tuple[bool, str]:
    import services.mqtt_backend as mqtt_backend
    import services.neo4j_backend as neo4j_backend
    import services.process_control as process_control
    import services.recording as recording

    kpi, _ = mqtt_backend.get_kpi_snapshot()
    session_id = (kpi or {}).get("session_id")

    mqtt_backend.stop_physical_system()
    time.sleep(1.5)
    parts = ["Physical line **STOP** (MQTT) sent."]
    if recording.is_recording():
        ok_rec, path = recording.stop_recording()
        if ok_rec:
            parts.append("**Recording** stopped, file: `{}`.".format(path))
        else:
            parts.append("Stop recording: `{}`.".format(path))

    end_iso = datetime.datetime.now().isoformat()
    if session_id:
        try:
            neo4j_backend.finalize_live_session(str(session_id), end_iso)
            parts.append("Session **{}** closed in Neo4j (end_time set).".format(session_id))
        except Exception as ex:
            parts.append("Neo4j session finalize: `{}`.".format(ex))

    n, msg = process_control.stop_main_service()
    parts.append("Stopped main_service. {}".format(msg))
    level, reset_msg = reset_realtime_dashboard_data(
        notify_main_service=False, clear_graph=False
    )
    if level == "error":
        parts.append("Dashboard reset failed: {}".format(reset_msg))
    else:
        parts.append(reset_msg)
    return True, " ".join(parts)

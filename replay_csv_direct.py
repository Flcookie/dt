# replay_csv_direct.py — Read historical CSV and push events through buffer → KPI → Neo4j (no MQTT).
#
# Usage: python replay_csv_direct.py <path.csv> [speed_multiplier]
# Env: CONFIG_FILE (optional), same as main_service / Streamlit.

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime

import common
import event_pipeline
import neo4j_writer

_log = logging.getLogger("replay_csv_direct")


def replay_kpi_state_path() -> str:
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".replay_kpi.json")
    )


def _write_kpi_state(pub: dict, *, completed: bool = False) -> None:
    path = replay_kpi_state_path()
    payload = {
        "t": time.time(),
        "data": pub,
        "completed": bool(completed),
    }
    fd, tmp = tempfile.mkstemp(
        prefix="replay_kpi_", suffix=".json", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_sorted_events(log_path: str) -> list[dict]:
    with open(log_path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))

    def sort_key(r: dict) -> float:
        ts = r.get("time") or ""
        try:
            return datetime.fromisoformat(str(ts).strip()).timestamp()
        except (TypeError, ValueError):
            return 0.0

    rows.sort(key=sort_key)
    return rows


def kpi_interval_sec(cfg: dict) -> float:
    """How often (wall-clock seconds) the replay writes the KPI sidecar."""
    return float((cfg.get("event_buffer") or {}).get("kpi_print_interval_sec", 2.0))


def _sleep_for_original_gap(
    prev_ts: float | None, time_str: str | None, speed: float
) -> float | None:
    """Sleep for this event's gap to the previous one (divided by ``speed``).

    Returns this event's timestamp, or None when it cannot be used as the next reference.
    No previous reference: short fixed pause (0.05 s). Any error while pacing (bad time,
    ``speed == 0``): 0.1 s pause and the reference is reset. Negative gaps do not sleep.
    """
    if prev_ts is not None and time_str:
        try:
            curr = datetime.fromisoformat(str(time_str).strip()).timestamp()
            delay = (curr - prev_ts) / speed
            if delay > 0:
                time.sleep(delay)
            return curr
        except Exception:
            time.sleep(0.1)
            return None

    try:
        curr = datetime.fromisoformat(str(time_str).strip()).timestamp()
    except Exception:
        curr = None
    time.sleep(0.05)
    return curr


def replay_events_paced(
    pipeline: event_pipeline.EventPipeline,
    events: list[dict],
    speed: float,
    kpi_interval: float,
) -> None:
    """Feed events through the pipeline at their recorded pacing (gap / ``speed``).

    The KPI sidecar is written right after the first event and then at most every
    ``kpi_interval`` wall-clock seconds; the caller writes the final ``completed`` state.
    """
    next_kpi = time.time()
    prev_ts: float | None = None
    for ev in events:
        prev_ts = _sleep_for_original_gap(prev_ts, ev.get("time"), speed)
        pipeline.ingest_event(dict(ev))

        now = time.time()
        if now >= next_kpi:
            _write_kpi_state(pipeline.kpi_publish_payload())
            next_kpi = now + kpi_interval


def run_replay(log_path: str, speed: float) -> None:
    cfg = common.load_config("config.json")
    pipeline = event_pipeline.EventPipeline(cfg, replay_mode=True)
    events = load_sorted_events(log_path)
    if not events:
        _log.warning("No rows in %s", log_path)
        return

    first_t = str(events[0].get("time") or "").strip()
    last_t = str(events[-1].get("time") or "").strip()
    _ft = str(first_t).strip().replace("Z", "+00:00")
    if len(_ft) > 10 and _ft[10] == " ":
        _ft = _ft[:10] + "T" + _ft[11:]
    sid = common.new_event_log_session_id(datetime.fromisoformat(_ft))
    pipeline.init_session(
        sid,
        "csv_import",
        start_time_iso=first_t,
        source_file=os.path.basename(log_path),
        status="running",
    )

    kpi_interval = kpi_interval_sec(cfg)

    print(
        "Direct replay: {} events from {} (speed={}x, no MQTT)".format(
            len(events), log_path, speed
        ),
        flush=True,
    )
    t0 = time.time()
    replay_events_paced(pipeline, events, speed, kpi_interval)

    pipeline.drain_buffer_tail()
    neo4j_writer.finalize_session(
        sid, last_t or datetime.now().isoformat(), status="completed"
    )
    _write_kpi_state(pipeline.kpi_publish_payload(), completed=True)
    elapsed = time.time() - t0
    print(
        "Done. {} events in {:.1f}s (direct pipeline)".format(len(events), elapsed),
        flush=True,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    default_log = "event-logs/event_log_260312_180229.csv"
    log_path = os.path.normpath(sys.argv[1] if len(sys.argv) > 1 else default_log)
    speed = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    if not os.path.isfile(log_path):
        print("File not found: {}".format(log_path), file=sys.stderr)
        sys.exit(1)
    try:
        run_replay(log_path, speed)
    finally:
        neo4j_writer.close()


if __name__ == "__main__":
    main()

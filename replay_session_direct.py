# replay_session_direct.py — Replay one Neo4j session through EventPipeline (timed, like live).
#
# Usage: python replay_session_direct.py <session_id> [speed_multiplier]
# Reads events from the existing Session in Neo4j only; does **not** create another Session
# or write events back. KPI sidecar: .replay_kpi.json (session_id = source session).

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_STREAMLIT_APP = os.path.join(_ROOT, "streamlit_app")
if _STREAMLIT_APP not in sys.path:
    sys.path.insert(0, _STREAMLIT_APP)

import common  # noqa: E402
import event_pipeline  # noqa: E402
import services.neo4j_backend as neo4j_backend  # noqa: E402
import neo4j_writer  # noqa: E402
from replay_csv_direct import _write_kpi_state  # noqa: E402

_log = logging.getLogger("replay_session_direct")


def run_replay_from_neo4j_session(source_session_id: str, speed: float) -> None:
    sid_src = (source_session_id or "").strip()
    if not sid_src:
        _log.warning("empty session id")
        return

    events = neo4j_backend.fetch_session_events_log_format(sid_src)
    if not events:
        _log.warning("No events for session %s", sid_src)
        return

    cfg = common.load_config("config.json")
    pipeline = event_pipeline.EventPipeline(
        cfg, replay_mode=True, persist_neo4j=False
    )
    pipeline.attach_existing_session_kpi_only(sid_src)

    kpi_interval = float(
        (cfg.get("event_buffer") or {}).get("kpi_print_interval_sec", 2.0)
    )
    next_kpi = time.time()

    print(
        "Session replay: {} events, session={} (KPI only, no Neo4j write; speed={}x)".format(
            len(events), sid_src, speed
        ),
        flush=True,
    )
    t0 = time.time()
    prev_ts: float | None = None

    for ev in events:
        ts_str = ev.get("time")
        if prev_ts is not None and ts_str:
            try:
                curr = datetime.fromisoformat(str(ts_str).strip()).timestamp()
                delay = (curr - prev_ts) / speed
                if delay > 0:
                    time.sleep(delay)
                prev_ts = curr
            except Exception:
                prev_ts = None
                time.sleep(0.1)
        else:
            try:
                prev_ts = datetime.fromisoformat(str(ts_str).strip()).timestamp()
            except Exception:
                prev_ts = None
            time.sleep(0.05)

        pipeline.ingest_event(dict(ev))

        now = time.time()
        if now >= next_kpi:
            _write_kpi_state(pipeline.kpi_publish_payload(), completed=False)
            next_kpi = now + kpi_interval

    pipeline.drain_buffer_tail()
    _write_kpi_state(pipeline.kpi_publish_payload(), completed=True)
    print(
        "Done. {} events in {:.1f}s (session {})".format(
            len(events), time.time() - t0, sid_src
        ),
        flush=True,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(level)s %(name)s %(message)s",
    )
    if len(sys.argv) < 2:
        print("Usage: replay_session_direct.py <session_id> [speed]", file=sys.stderr)
        sys.exit(1)
    src = sys.argv[1].strip()
    spd = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    try:
        run_replay_from_neo4j_session(src, spd)
    finally:
        neo4j_writer.close()
        neo4j_backend.close_driver()


if __name__ == "__main__":
    main()

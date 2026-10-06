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
from replay_csv_direct import (  # noqa: E402
    _write_kpi_state,
    kpi_interval_sec,
    replay_events_paced,
)

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

    kpi_interval = kpi_interval_sec(cfg)

    print(
        "Session replay: {} events, session={} (KPI only, no Neo4j write; speed={}x)".format(
            len(events), sid_src, speed
        ),
        flush=True,
    )
    t0 = time.time()
    replay_events_paced(pipeline, events, speed, kpi_interval)

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

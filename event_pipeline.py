# event_pipeline.py — shared MQTT ingest path + optional CSV direct replay (buffer / KPI / Neo4j).

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import common
import event_buffer
import kpi_calculator
import neo4j_writer

_log = logging.getLogger("event_pipeline")


class EventPipeline:
    """One buffer + KPI + Neo4j session; used by main_service (MQTT) and replay_csv_direct (CSV)."""

    def __init__(self, cfg: dict, *, replay_mode: bool, persist_neo4j: bool = True):
        buffer_cfg = cfg.get("event_buffer", {}) or {}
        kpi_cfg = cfg.get("kpi_config", {}) or {}
        obs_mode = "replay" if replay_mode else kpi_cfg.get(
            "observation_time_mode", "realtime"
        )
        window_key = "replay_window_ms" if replay_mode else "window_ms"
        window_ms = buffer_cfg.get(window_key) or buffer_cfg.get("window_ms", 300)
        self.buffer = event_buffer.EventBuffer(
            window_ms=int(window_ms),
            max_size=buffer_cfg.get("max_size"),
        )
        self.kpi = kpi_calculator.KpiCalculator(
            observation_time_mode=obs_mode,
            finish_events=kpi_cfg.get("finish_events", ["FINISH"]),
            scrap_events=kpi_cfg.get("scrap_events", ["SCRAP"]),
        )
        self._session_id: str | None = None
        self.replay_mode = replay_mode
        self.persist_neo4j = persist_neo4j
        self._last_flush_count = 0
        self.flush_since_last_print = 0
        self.total_flush_count = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def init_session(
        self,
        session_id: str,
        description: str = "live",
        *,
        start_time_iso: str | None = None,
        source_file: str | None = None,
        end_time_iso: str | None = None,
        event_count: int | None = None,
        status: str | None = None,
    ) -> None:
        st = start_time_iso or datetime.now().isoformat()
        stat = status
        if stat is None:
            stat = "running" if description == "live" else "completed"
        if self.persist_neo4j:
            neo4j_writer.start_session(
                session_id,
                description,
                start_time_iso=st,
                end_time_iso=end_time_iso,
                event_count=event_count,
                source_file=source_file,
                status=stat,
            )
        self._session_id = session_id

    def attach_existing_session_kpi_only(self, session_id: str) -> None:
        """Replay-from-graph: KPI/buffer use this id in payloads; no Neo4j session create or event writes."""
        sid = (session_id or "").strip()
        self._session_id = sid if sid else None

    def process_command(self, cmd: dict[str, Any]) -> None:
        action = cmd.get("action")
        if action == "reset_kpi":
            self.buffer.clear()
            self.kpi.reset()
            _log.info("KPI reset")
        elif action == "start_new_session":
            sid = common.new_event_log_session_id()
            desc = str(cmd.get("description", "") or "live")
            neo4j_writer.start_session(
                sid,
                desc,
                start_time_iso=datetime.now().isoformat(),
                status="running",
            )
            self._session_id = sid
            self.buffer.clear()
            self.kpi.reset()
            _log.info("New session: %s", sid)
        elif action == "reset_all":
            sid = common.new_event_log_session_id()
            desc = str(cmd.get("description", "") or "live")
            neo4j_writer.start_session(
                sid,
                desc,
                start_time_iso=datetime.now().isoformat(),
                status="running",
            )
            self._session_id = sid
            self.buffer.clear()
            self.kpi.reset()
            _log.info("Reset All: new session %s %s", sid, desc or "")
        elif action == "clear_neo4j":
            neo4j_writer.clear_all_events()
            sid = common.new_event_log_session_id()
            neo4j_writer.start_session(
                sid,
                "live",
                start_time_iso=datetime.now().isoformat(),
                status="running",
            )
            self._session_id = sid
            self.buffer.clear()
            self.kpi.reset()
            _log.info("Neo4j cleared, new session: %s", sid)
        elif action == "finalize_session":
            sid = str(cmd.get("session_id") or self._session_id or "")
            end_iso = str(cmd.get("end_time_iso") or datetime.now().isoformat())
            st = str(cmd.get("status") or "completed")
            if sid:
                neo4j_writer.finalize_session(sid, end_iso, status=st)
                _log.info("Session finalized: %s", sid)

    def ingest_event(self, event: dict[str, Any]) -> tuple[int, int]:
        """Apply one component event. Returns (n_ready_flushed, n_forced)."""
        ready_events, n_forced = self.buffer.add_and_flush(event)
        if n_forced > 0:
            _log.warning(
                "max_size forced flush of %s events (window_ms=%s, max_size=%s)",
                n_forced,
                self.buffer.window_ms,
                self.buffer.max_size,
            )
        n = len(ready_events)
        self._last_flush_count = n
        self.flush_since_last_print += n
        self.total_flush_count += n
        for ev in ready_events:
            self.kpi.on_event(ev)
        if self.persist_neo4j:
            try:
                neo4j_writer.write_events_batch(ready_events, self._session_id)
            except Exception as e:
                _log.error("Neo4j write error（KPI 已更新，图库未写入）: %s", e)
        return n, n_forced

    def drain_buffer_tail(self) -> None:
        """After ordered replay, flush any events still inside the time window."""
        ready = self.buffer.drain_all_ordered()
        if not ready:
            return
        self._last_flush_count = len(ready)
        self.flush_since_last_print += len(ready)
        self.total_flush_count += len(ready)
        for ev in ready:
            self.kpi.on_event(ev)
        if self.persist_neo4j:
            try:
                neo4j_writer.write_events_batch(ready, self._session_id)
            except Exception as e:
                _log.error("Neo4j tail flush error: %s", e)

    def get_snapshot(self) -> dict[str, Any]:
        return self.kpi.get_snapshot()

    def kpi_publish_payload(self) -> dict[str, Any]:
        pub = dict(self.get_snapshot())
        pub["session_id"] = self._session_id
        pub["run_mode"] = "replay" if self.replay_mode else "physical"
        return pub

"""
Replay session state shared by the Home control panel and the History panel: the replay
worker process in st.session_state (``replay_proc``) and the KPI caches to clear before a new
replay starts.

``replay_csv_path`` / ``replay_event_baseline`` belonged to the former CSV-upload replay block;
nothing sets them any more, but they are still initialised / cleared here as before.
"""
from __future__ import annotations

import os
import time

import streamlit as st

import services.mqtt_backend as mqtt_backend


def ensure_replay_session_state() -> None:
    if "replay_proc" not in st.session_state:
        st.session_state.replay_proc = None
    if "replay_csv_path" not in st.session_state:
        st.session_state.replay_csv_path = None


def running_replay_process():
    """The replay worker process if one was started and is still running, else None."""
    p = st.session_state.get("replay_proc")
    if p is not None and p.poll() is None:
        return p
    return None


def clean_up_finished_replay() -> None:
    """If the replay worker has exited on its own, clear what it left in the session."""
    p = st.session_state.get("replay_proc")
    if p is not None and p.poll() is not None:
        stop_replay_and_clear_state()


def stop_replay_and_clear_state() -> None:
    """Stop the replay worker if it is still running, then clear what it left in the session:
    forget the process, delete the uploaded temp CSV and drop the Neo4j progress baseline.

    Also used after a replay has finished on its own (only the clean-up part applies then).
    """
    p = st.session_state.get("replay_proc")
    if p is not None:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                pass
        st.session_state.replay_proc = None
    pth = st.session_state.get("replay_csv_path")
    st.session_state.replay_csv_path = None
    st.session_state.pop("replay_event_baseline", None)
    if pth and os.path.isfile(pth):
        try:
            os.unlink(pth)
        except OSError:
            pass


def clear_kpi_before_replay() -> None:
    """Clear the KPI snapshot and the page's cached KPI before a replay worker starts, so the
    dashboard does not show the previous run (CSV replay may open a new Neo4j session)."""
    mqtt_backend.clear_kpi_snapshot()
    for _k in ("_kpi_cache", "_kpi_tupd"):
        st.session_state.pop(_k, None)
    time.sleep(0.15)

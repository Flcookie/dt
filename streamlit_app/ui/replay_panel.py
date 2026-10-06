"""
CSV 回放控件 + Neo4j 写入进度（供 Main · Control 使用；与旧 History 页逻辑一致）。
"""
from __future__ import annotations

import os
import tempfile
import time

import streamlit as st

from paths import PROJECT_ROOT

import services.mqtt_backend as mqtt_backend
import services.neo4j_backend as neo4j_backend
import services.process_control as process_control
import services.recording as recording
import ui.live_refresh as ui_live_refresh


def ensure_replay_session_state() -> None:
    if "replay_proc" not in st.session_state:
        st.session_state.replay_proc = None
    if "replay_csv_path" not in st.session_state:
        st.session_state.replay_csv_path = None


def _clear_replay_child_and_temp_file() -> None:
    """Kill replay subprocess if alive, remove temp CSV, drop Neo4j progress baseline."""
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


def _reset_replay_downstream_for_new_run() -> None:
    """Clear cached KPI before a replay worker starts (CSV replay may open a new Neo4j session)."""
    mqtt_backend.clear_kpi_snapshot()
    for _k in ("_kpi_cache", "_kpi_tupd"):
        st.session_state.pop(_k, None)
    time.sleep(0.15)


def csv_data_row_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def render_csv_replay_block(*, key_prefix: str) -> None:
    """带边框的 CSV 回放区；所有 widget key 加前缀以免多页冲突。"""
    ensure_replay_session_state()

    # 外层由 Main 页分组容器包边，此处不再套一层边框以免重复
    with st.container(border=False):
        p = st.session_state.replay_proc
        if p is not None and p.poll() is not None:
            _clear_replay_child_and_temp_file()
            st.success("Replay finished.")
            if process_control.is_main_service_running():
                _n, _msg = process_control.stop_main_service()
                st.info("{}".format(_msg))

        _recording_block = recording.is_recording()

        _preset_labels = [
            "Custom",
            "1×",
            "2×",
            "5×",
            "10×",
            "20×",
            "50×",
        ]
        _preset_values = [None, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        _sel_key = "{}_replay_speed_preset".format(key_prefix)
        _num_key = "{}_replay_speed_num".format(key_prefix)

        _proc_now = st.session_state.replay_proc
        _replay_live = _proc_now is not None and _proc_now.poll() is None

        # Uploader first (Streamlit needs `uploaded` before Start). Full-width row avoids
        # a short-tall-short misalignment with the drop zone.
        uploaded = st.file_uploader(
            "CSV",
            type=["csv"],
            key="{}_csv_upload".format(key_prefix),
        )

        c_spd, c_go, c_end = st.columns(
            [2.1, 1.0, 1.0],
            gap="small",
            vertical_alignment="bottom",
        )
        with c_spd:
            _ix = st.selectbox(
                "Speed",
                range(len(_preset_labels)),
                index=4,
                format_func=lambda i: _preset_labels[i],
                key=_sel_key,
                help="vs realtime; pick Custom for a manual multiplier.",
            )
            if _preset_values[_ix] is None:
                speed = st.number_input(
                    "× realtime",
                    min_value=0.5,
                    max_value=100.0,
                    value=float(st.session_state.get(_num_key, 10.0)),
                    step=0.5,
                    key=_num_key,
                )
            else:
                speed = float(_preset_values[_ix])

        _can_start = uploaded is not None and not _recording_block
        with c_go:
            if st.button(
                "Start replay",
                disabled=not _can_start,
                use_container_width=True,
                key="{}_replay_go".format(key_prefix),
            ):
                if not uploaded:
                    st.error("Upload a CSV first.")
                else:
                    # Always stop an existing replay worker so «Start» is idempotent (restart from CSV top).
                    _clear_replay_child_and_temp_file()

                    _ok_ms, _ms_msg = process_control.ensure_main_service_replay()
                    if not _ok_ms:
                        st.error(_ms_msg)
                    _abort_replay = not _ok_ms
                    _local = os.path.normpath(
                        os.path.join(PROJECT_ROOT, "config_local.json")
                    )
                    if not _abort_replay and os.path.isfile(_local):
                        try:
                            mqtt_backend.switch_config_file("config_local.json")
                            time.sleep(2)
                            st.success("Switched to **config_local** and reconnected.")
                        except Exception as e:
                            st.error("Failed to switch local config: {}".format(e))
                            _abort_replay = True
                    elif not _abort_replay:
                        st.warning(
                            "**config_local.json** not found — using **{}** (add file for local broker).".format(
                                mqtt_backend.active_config_name()
                            )
                        )

                    if not _abort_replay:
                        _reset_replay_downstream_for_new_run()
                        path = os.path.join(
                            tempfile.gettempdir(),
                            "lego_replay_{}.csv".format(int(time.time())),
                        )
                        with open(path, "wb") as f:
                            f.write(uploaded.getbuffer())
                        st.session_state.replay_csv_path = path
                        try:
                            st.session_state.replay_event_baseline = (
                                neo4j_backend.count_session_events(None)
                            )
                            st.session_state.replay_proc = (
                                mqtt_backend.run_replay_subprocess(path, speed)
                            )
                            st.success(
                                "Replay started · child PID **{}** (new session + KPI reset)".format(
                                    st.session_state.replay_proc.pid
                                )
                            )
                        except Exception as e:
                            st.error(str(e))
                            try:
                                os.unlink(path)
                            except OSError:
                                pass
                            st.session_state.replay_csv_path = None
        with c_end:
            if st.button(
                "Stop replay",
                disabled=not _replay_live,
                use_container_width=True,
                key="{}_replay_kill".format(key_prefix),
                type="primary",
            ):
                _clear_replay_child_and_temp_file()
                _n, _msg = process_control.stop_main_service()
                st.success("{}".format(_msg))
                st.rerun()

        if _replay_live:
            st.caption("Replaying · PID **{}**".format(_proc_now.pid))


@st.fragment(run_every=ui_live_refresh.live_ui_refresh_delta())
def replay_progress_fragment() -> None:
    """Neo4j 回放写入进度（与 KPI / Twin 同周期刷新）。"""
    ensure_replay_session_state()
    proc = st.session_state.get("replay_proc")
    path = st.session_state.get("replay_csv_path")
    if proc is None or path is None or not os.path.isfile(path):
        return
    if proc.poll() is not None:
        return
    total = csv_data_row_count(path)
    if total <= 0:
        return
    cur = neo4j_backend.count_session_events(None)
    base = int(st.session_state.get("replay_event_baseline") or 0)
    done = max(0, cur - base)
    st.subheader("Neo4j write progress")
    st.progress(min(1.0, done / float(total)))
    st.text("{} / {} rows".format(done, total))

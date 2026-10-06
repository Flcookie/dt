# test_part_trace_panel_ui.py — records current behavior of ui.part_trace_panel.render_part_trace_panel
# (standalone Part Trace page and the Digital Twin embed) with Streamlit's AppTest.
# Neo4j / MQTT inputs are fakes built in the same shape as neo4j_backend.query_part_flow returns.
#
# Skipped when streamlit is not installed.

import datetime
import os
import sys

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st  # noqa: E402

import part_track.part_track_conformance as ptc  # noqa: E402
import services.mqtt_backend as mqtt_backend  # noqa: E402
import services.neo4j_backend as neo4j_backend  # noqa: E402

SID = "event_log_20260509_164519"
_T0 = datetime.datetime(2026, 5, 9, 16, 45, 19)


def _path(outcome="FINISH", with_fail=False):
    steps = [("corner2", "START")]
    for st_, sp in [("station11", "splitter1"), ("station21", "splitter2"), ("station31", None),
                    ("station41", "splitter3"), ("station61", None), ("station71", None)]:
        steps += [(st_, "LOAD"), (st_, "PROCESS"), (st_, "UNLOAD"), (st_, "TRANSFER")]
        if sp:
            steps += [(sp, "FORWARD"), (sp, "TRANSFER")]
    if with_fail:
        steps.insert(9, ("station21", "FAIL"))
    steps += [("corner1", "TRANSFER"), ("splitter5", "CHECKOUT"), ("splitter5", outcome)]
    return steps


def _part(pid, offset, **kw):
    out = []
    for i, (comp, act) in enumerate(_path(**kw)):
        t = _T0 + datetime.timedelta(seconds=offset + 2 * i)
        lc = neo4j_backend._lifecycle_label(act)
        out.append({
            "component_id": comp, "activity": act, "time": t.strftime("%H:%M:%S"),
            "lifecycle": lc, "is_entry": lc == "entry", "is_exit": lc == "exit",
            "timestamp": t.timestamp(),
        })
    flow = " → ".join("[{}] {}@{}".format(s["time"], s["component_id"], s["activity"]) for s in out)
    return {"part_id": pid, "steps": out, "flow": flow}


PARTS = [_part("p_ok", 0), _part("p_fail", 20, with_fail=True), _part("p_scrap", 40, outcome="SCRAP")]


def _app():
    import streamlit as st  # noqa: F811

    import ui.part_trace_panel as ptp

    ptp.render_part_trace_panel(**st.session_state.get("_test_kwargs", {}))


@pytest.fixture
def env(monkeypatch):
    calls = []
    cfg = {"error": None, "kpi": {}, "physical_sid": None, "replay_sid": None}

    def query_part_flow(part_id, session_id=None):
        calls.append(("query_part_flow", part_id, session_id))
        if cfg["error"]:
            return {"parts": [], "session_id": None, "error": cfg["error"]}
        if session_id is None:  # current behavior with real Neo4j: "latest" resolves to nothing
            return {"parts": [], "session_id": None, "error": "No session in graph."}
        parts = [p for p in PARTS if part_id is None or p["part_id"] == part_id.strip()]
        return {"parts": [dict(p) for p in parts], "session_id": session_id, "session_description": ""}

    m = monkeypatch.setattr
    m(neo4j_backend, "query_part_flow", query_part_flow)
    m(neo4j_backend, "list_recent_sessions", lambda n: [{"id": SID}])
    m(mqtt_backend, "get_kpi_snapshot", lambda: (calls.append(("kpi",)), (dict(cfg["kpi"]), 0.0))[1])
    m(mqtt_backend, "physical_kpi_session_id", lambda: cfg["physical_sid"])
    m(mqtt_backend, "get_replay_pipeline_session_id", lambda: cfg["replay_sid"])
    st.cache_data.clear()

    def run(kwargs=None, state=None, query_params=None):
        at = AppTest.from_function(_app, default_timeout=120)  # first run is a cold import
        at.session_state["_test_kwargs"] = kwargs or {"from_query_params": True}
        for k, v in (state or {}).items():
            at.session_state[k] = v
        for k, v in (query_params or {}).items():
            at.query_params[k] = v
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return type("Env", (), {"calls": calls, "cfg": cfg, "run": staticmethod(run)})


def _texts(at):
    out = [e.value for e in at.markdown] + [e.value for e in at.caption] + [e.value for e in at.text]
    return "\n".join(str(x) for x in out)


def _page_info(at):
    return [t.value for t in at.text if str(t.value).startswith("Page ")]


TWIN = dict(from_query_params=False, use_page_session=True, use_coordinated_twin_session=True,
            coordinated_twin_session_id=SID, kpi_for_replay={})


def test_latest_session_option_shows_no_session(env):
    at = env.run()
    assert [w.value for w in at.warning] == ["No session in graph."]
    assert ("query_part_flow", None, None) in env.calls
    assert at.selectbox(key="pt_detail_pick").options == ["—"]


def test_picked_session_overview_lists_parts_and_detail(env):
    at = env.run(state={"trace_session_ix": 1})
    assert SID in [t.value for t in at.text]
    assert at.selectbox(key="pt_detail_pick").options == ["—", "p_fail", "p_ok", "p_scrap"]
    at.selectbox(key="pt_detail_pick").set_value("p_fail").run()
    assert "##### Part `p_fail`" in _texts(at)


def test_single_part_paging_and_clamp(env):
    at = env.run(state={"trace_session_ix": 1})
    at.text_input(key="part_trace_part_id").set_value("p_fail").run()
    assert _page_info(at) == ["Page 1/2 · 20 steps"]
    at.button(key="pt_next_btn").click().run()
    assert _page_info(at) == ["Page 2/2 · 20 steps"]
    at.button(key="pt_next_btn").click().run()
    assert _page_info(at) == ["Page 2/2 · 20 steps"]
    at.selectbox(key="pt_page_size").set_value(50).run()  # page index clamped to the last page
    assert _page_info(at) == ["Page 1/1 · 20 steps"]
    at.radio(key="pt_event_level").set_value("All events").run()
    assert _page_info(at) == ["Page 1/1 · 35 steps"]
    assert at.text[-1].value.startswith("[16:45:39] corner2@START")


def test_unknown_part_shows_empty_tables(env):
    at = env.run(state={"trace_session_ix": 1})
    at.text_input(key="part_trace_part_id").set_value("nope").run()
    assert "Part not found or no data in this session — tables stay visible." in _texts(at)
    assert not _page_info(at)


def test_part_id_from_query_params_only_with_flag(env):
    at = env.run(state={"trace_session_ix": 1}, query_params={"part_id": " p_scrap "})
    assert at.text_input(key="part_trace_part_id").value == "p_scrap"
    at = env.run(kwargs={"from_query_params": False}, state={"trace_session_ix": 1},
                 query_params={"part_id": "p_scrap"})
    assert at.text_input(key="part_trace_part_id").value == ""


def test_replay_kpi_trims_steps(env):
    first_ts = PARTS[0]["steps"][0]["timestamp"]
    env.cfg["kpi"] = {"run_mode": "replay", "chart_time_unix": first_ts + 10}
    at = env.run(state={"trace_session_ix": 1})
    at.text_input(key="part_trace_part_id").set_value("p_ok").run()
    # raw steps at +0..+10 s are kept (6); Process level shows 4 of them
    assert _page_info(at) == ["Page 1/1 · 4 steps"]
    assert ("kpi",) in env.calls


def test_twin_embed_uses_preloaded_data_without_queries(env):
    rows = ptc.build_session_table_rows(PARTS)
    at = env.run(kwargs=dict(TWIN, twin_preloaded_parts=PARTS, twin_preloaded_rows=rows))
    assert env.calls == []
    assert at.session_state["_dt_trace_rows_pt"] == rows
    assert not at.radio and not at.text_input


def test_twin_embed_without_preload_queries_session_and_hides_errors(env):
    at = env.run(kwargs=dict(TWIN))
    assert env.calls == [("query_part_flow", None, SID)]
    assert "_dt_trace_rows_pt" in at.session_state
    env.cfg["error"] = "boom"
    at = env.run(kwargs=dict(TWIN), state={"_dt_trace_rows_pt": "stale"})
    assert not at.warning
    assert "_dt_trace_rows_pt" not in at.session_state


def test_twin_trace_query_param_opens_dialog_index(env):
    rows = ptc.build_session_table_rows(PARTS)
    at = env.run(kwargs=dict(TWIN, twin_preloaded_parts=PARTS, twin_preloaded_rows=rows),
                 query_params={"dt_pt_trace": "2"})
    assert at.session_state["ptc_twin_trace_modal_idx"] == 2
    assert "dt_pt_trace" not in at.query_params


@pytest.mark.parametrize(
    "state, cfg, expected_sid",
    [
        ({"cp_data_source": "live"}, {"physical_sid": "phys", "replay_sid": "rep"}, "phys"),
        ({"cp_data_source": "local", "dt_resolved_session": "dt"}, {"replay_sid": "rep"}, "rep"),
        ({"cp_data_source": "local", "dt_resolved_session": "dt"}, {}, "dt"),
    ],
)
def test_page_session_source_without_twin_coordination(env, state, cfg, expected_sid):
    env.cfg.update(cfg)
    env.run(kwargs=dict(from_query_params=False, use_page_session=True), state=state)
    assert ("query_part_flow", None, expected_sid) in env.calls

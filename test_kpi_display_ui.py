# test_kpi_display_ui.py — key regressions for the KPI dashboard (ui.kpi_display with its trend
# charts in ui.kpi_trends), rendered with Streamlit's AppTest.
#
# Inputs are small hand-built KPI snapshots; expected values follow from them (see comments).
# The time used by empty charts is fixed by replacing ui.kpi_trends.datetime; the configured
# station list is fixed by replacing default_station_ids (no config file, database or MQTT).
#
# Skipped when streamlit is not installed.

import datetime
import json
import os
import sys
import types

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ui.kpi_display as kpi_display  # noqa: E402
import ui.kpi_trends as kpi_trends  # noqa: E402

T0 = 1_800_000_000.0
FIXED_NOW = datetime.datetime(2027, 1, 15, 8, 0, 0)
PEAK_KEYS = ("_kpi_rate_trend_y_peak_history", "_kpi_scrap_trend_y_peak_history", "_kpi_rate_trend_y_session_id")

LIVE = {
    "run_mode": "physical",
    "session_id": "S-live",
    "observation_start_ts": T0,
    "chart_time_unix": T0 + 60,
    "observation_time_sec": 60.0,
    "system": {"num_completions": 2, "num_scraps": 1, "wip_instantaneous": 1, "wip_average": 1.75,
               "complete_rate": 0.0333, "avg_cycle_time_fin": 42.0, "avg_cycle_time_all": 40.5},
    "trend_sys_wip_history": [[T0, 0], [T0 + 10, 2], [T0 + 30, 3], [T0 + 60, 1]],
    # (ts, completions/s, scraps/s) -> charted as pcs/min
    "trend_throughput_rates": [[T0 + 30, 0.05, 0.0], [T0 + 60, 0.04, 0.01]],
}


def _ts(offset):
    """Plotly's serialization of the chart's local-time x values."""
    return datetime.datetime.fromtimestamp(T0 + offset).isoformat()


INPUT_MODULE = "_kpi_display_ui_test_input"  # how the test hands the snapshot to the app script


def _app():
    import sys

    import ui.kpi_display as kd

    kd.render_kpi_dashboard(sys.modules["_kpi_display_ui_test_input"].kpi)


@pytest.fixture
def dash(monkeypatch):
    class _FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 15, 8, 0, 0)

    monkeypatch.setattr(kpi_trends, "datetime",
                        types.SimpleNamespace(datetime=_FixedDatetime, timedelta=datetime.timedelta))
    monkeypatch.setattr(kpi_display, "default_station_ids", lambda: ("station11", "station21"))
    snapshot = types.SimpleNamespace(kpi=None)
    monkeypatch.setitem(sys.modules, INPUT_MODULE, snapshot)
    at = AppTest.from_function(_app, default_timeout=60)

    def render(kpi):
        snapshot.kpi = kpi
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return render


def _chart(at, key):
    (el,) = [e for e in at.get("plotly_chart") if e.proto.id.endswith("-" + key)]
    return json.loads(el.proto.spec)


def _traces(spec):
    return {t["name"]: t for t in spec["data"]}


def _peaks(at):
    return {k: at.session_state[k] for k in PEAK_KEYS if k in at.session_state}


@pytest.mark.parametrize("kpi", [None, {}])
def test_empty_snapshot_draws_placeholder_trends(dash, kpi):
    at = dash(kpi)
    assert len(at.get("plotly_chart")) == 6  # 2 trend charts + 4 station fraction bars
    wip = _chart(at, "kpi_trend_wip_over_time")
    t = _traces(wip)
    assert t["WIP"]["y"] == [0] and t["WIP"]["opacity"] == 0
    assert t["WIP"]["x"] == [FIXED_NOW.isoformat()]
    assert wip["layout"]["yaxis"]["range"] == [0, 10]
    assert wip["layout"]["xaxis"]["range"] == ["2027-01-15T07:58:00", "2027-01-15T08:00:00"]
    rates = _chart(at, "kpi_trend_completion_scrap")
    assert rates["layout"]["yaxis"]["range"] == [0.0, 3.0]
    assert rates["layout"]["yaxis2"]["range"] == [0.0, 1.0]
    assert _peaks(at) == {}  # the rate-axis peak history is only kept once rates are drawn


def test_live_snapshot_trend_data_and_system_cards(dash):
    at = dash(LIVE)
    wip = _traces(_chart(at, "kpi_trend_wip_over_time"))
    # x range: first point - 2 s .. chart time + 2 s; the series is padded to both ends
    assert wip["WIP"]["x"] == [_ts(-2), _ts(0), _ts(10), _ts(30), _ts(60), _ts(62)]
    assert wip["WIP"]["y"] == [0, 0, 2, 3, 1, 1]
    # time-weighted mean of the drawn window: (0*10 + 2*20 + 3*30 + 1*30) / 90
    assert wip["AVG WIP (window)"]["y"] == [pytest.approx(160 / 90)] * 6
    spec = _chart(at, "kpi_trend_completion_scrap")
    rates = _traces(spec)
    assert rates["Completion"]["x"] == [_ts(-2), _ts(30), _ts(60), _ts(62)]
    assert rates["Completion"]["y"] == [0.0, 3.0, 2.4, 2.4]
    assert rates["Scrap"]["y"] == [0.0, 0.0, 0.6, 0.6]
    assert spec["layout"]["yaxis"]["range"] == [0.0, pytest.approx(3.6)]  # peak * 1.2
    assert spec["layout"]["yaxis2"]["range"] == [0.0, pytest.approx(0.72)]
    assert _peaks(at) == {
        "_kpi_rate_trend_y_session_id": str(T0),
        "_kpi_rate_trend_y_peak_history": [3.0],
        "_kpi_scrap_trend_y_peak_history": [0.6],
    }
    cards = "".join(m.value for m in at.markdown)
    assert ">1.998<" in cards  # completion rate 0.0333/s * 60
    assert ">1.000<" in cards  # scrap rate 1 scrap / 60 s * 60


def test_repeated_refresh_keeps_rate_axis_peak(dash):
    dash(LIVE)
    lower = dict(LIVE, trend_throughput_rates=[[T0 + 30, 0.02, 0.0], [T0 + 60, 0.01, 0.0]])
    at = dash(lower)
    spec = _chart(at, "kpi_trend_completion_scrap")
    assert _traces(spec)["Completion"]["y"] == [0.0, 1.2, 0.6, 0.6]
    assert _peaks(at)["_kpi_rate_trend_y_peak_history"] == [3.0, 1.2]
    assert spec["layout"]["yaxis"]["range"] == [0.0, pytest.approx(3.6)]  # earlier peak still sets the axis
    assert spec["layout"]["yaxis2"]["range"] == [0.0, pytest.approx(0.72)]


def test_switch_to_replay_resets_rate_axis_peak(dash):
    dash(LIVE)
    replay = dict(LIVE, run_mode="replay", session_id="S-replay", observation_start_ts=T0 + 0.5,
                  trend_throughput_rates=[[T0 + 30, 0.02, 0.0], [T0 + 60, 0.01, 0.0]])
    at = dash(replay)
    spec = _chart(at, "kpi_trend_completion_scrap")
    assert _peaks(at) == {
        "_kpi_rate_trend_y_session_id": str(T0 + 0.5),
        "_kpi_rate_trend_y_peak_history": [1.2],
        "_kpi_scrap_trend_y_peak_history": [0.0],
    }
    assert spec["layout"]["yaxis"]["range"] == [0.0, pytest.approx(1.44)]
    assert spec["layout"]["yaxis2"]["range"] == [0.0, 1.0]  # no scrap peak yet -> the empty-axis height
    at = dash(LIVE)  # and back: reset again
    assert _peaks(at)["_kpi_rate_trend_y_peak_history"] == [3.0]


def test_rates_fall_back_to_departure_history(dash):
    kpi = dict(LIVE, chart_time_unix=T0 + 40, trend_sys_wip_history=[[T0, 0], [T0 + 40, 1]])
    del kpi["trend_throughput_rates"]
    # cumulative (ts, completions, scraps); rates are taken over the departures so far
    kpi["trend_departure_history"] = [[T0, 0, 0], [T0 + 20, 1, 0], [T0 + 40, 2, 1]]
    at = dash(kpi)
    rates = _traces(_chart(at, "kpi_trend_completion_scrap"))
    assert rates["Completion"]["x"] == [_ts(-2), _ts(20), _ts(40), _ts(42)]
    assert rates["Completion"]["y"] == [0.0, 3.0, 3.0, 3.0]  # 1/20 s and 2/40 s -> 3 pcs/min
    assert rates["Scrap"]["y"] == [0.0, 0.0, 1.5, 1.5]  # 1/40 s -> 1.5 pcs/min

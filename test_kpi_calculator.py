# test_kpi_calculator.py — System / Stage / Station KPI (streaming)

import os

import pytest

import event_buffer
import kpi_calculator


def test_part_id_schema_aliases_entity_and_partid():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    base = "2026-03-12T18:00:00.000"
    kpi.on_event({"time": base, "component_id": "corner2", "entity_id": "px", "activity": "START"})
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:10.000",
            "component_id": "splitter5",
            "partId": "px",
            "activity": "FINISH",
        }
    )
    snap = kpi.get_snapshot()
    assert snap["finished_count"] == 1
    assert "px" in kpi._distinct_part_ids


def test_throughput():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    base = "2026-03-12T18:00:00.000"
    kpi.on_event({"time": base, "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.on_event(
        {"time": "2026-03-12T18:00:10.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    snap = kpi.get_snapshot()
    assert snap["finished_count"] == 1
    assert snap["observation_time_sec"] >= 10.0
    assert abs(snap["throughput"] - 0.1) < 0.01


def test_system_wip_start_finish_scrap():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    base = "2026-03-12T18:00:00.000"
    kpi.on_event({"time": base, "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "corner2", "part_id": "p2", "activity": "START"}
    )
    assert kpi.current_wip == 2
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    assert kpi.current_wip == 1
    kpi.on_event(
        {"time": "2026-03-12T18:00:03.000", "component_id": "splitter5", "part_id": "p2", "activity": "SCRAP"}
    )
    assert kpi.current_wip == 0
    snap = kpi.get_snapshot()
    assert snap["finished_count"] == 1
    assert snap["scrap_count"] == 1
    assert abs(snap["scrap_rate"] - 0.5) < 0.01
    assert snap["avg_cycle_all_sample_count"] == 2


def test_second_start_same_part_before_first_finish_is_ignored_for_wip():
    """While a part has an unclosed lap, a second corner2 START does not add WIP again."""
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.on_event({"time": "2026-03-12T18:00:01.000", "component_id": "corner2", "part_id": "p1", "activity": "START"})
    assert kpi.current_wip == 1
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    assert kpi.current_wip == 0
    assert kpi.get_snapshot()["finished_count"] == 1
    assert kpi.get_snapshot()["duplicate_start_count"] == 1


def test_cycle_time_finished():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.on_event(
        {"time": "2026-03-12T18:00:05.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    snap = kpi.get_snapshot()
    assert snap["flow_time_count"] == 1
    assert abs(snap["avg_cycle_time_finished_sec"] - 5.0) < 0.01


def test_divide_by_zero():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="realtime")
    snap = kpi.get_snapshot()
    assert snap["observation_time_sec"] >= 0
    assert snap["throughput"] >= 0
    assert "utilization" in snap
    assert "station_live" in snap
    assert not (snap["throughput"] != snap["throughput"])
    assert snap.get("scrap_rate", 0) == 0


def test_station_live_busy_transfer():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station11", "part_id": "p9", "activity": "LOAD"}
    )
    snap = kpi.get_snapshot()
    live = snap["station_live"]["station11"]
    assert kpi._stn_state["station11"]["part"] == "p9"
    assert live["current_part_id"] == "p9"
    assert live["current_state"] == "BUSY"
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "station11", "part_id": "p9", "activity": "UNLOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "station11", "activity": "TRANSFER"}
    )
    snap2 = kpi.get_snapshot()
    assert snap2["station_live"]["station11"]["current_part_id"] == ""
    assert snap2["station_live"]["station11"]["current_state"] == "IDLE"


def test_station_live_pass():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.000",
            "component_id": "station21",
            "part_id": "p3",
            "activity": "PASS",
        }
    )
    snap = kpi.get_snapshot()
    live = snap["station_live"]["station21"]
    assert kpi._stn_state["station21"]["part"] == "p3"
    assert live["current_part_id"] == "p3"
    assert live["current_state"] == "IDLE"


def test_finish_case_insensitive():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.on_event(
        {"time": "2026-03-12T18:00:05.000", "component_id": "splitter5", "part_id": "p1", "activity": "finish"}
    )
    assert kpi.get_snapshot()["finished_count"] == 1


def test_reset():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="realtime")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"})
    kpi.reset()
    snap = kpi.get_snapshot()
    assert snap["finished_count"] == 0
    assert kpi.current_wip == 0
    assert snap["flow_time_count"] == 0
    assert kpi.fail_event_count == 0


def test_two_finishes_same_part():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p3", "activity": "START"})
    kpi.on_event(
        {"time": "2026-03-12T18:00:10.000", "component_id": "splitter5", "part_id": "p3", "activity": "FINISH"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:11.000", "component_id": "corner2", "part_id": "p3", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:26.000", "component_id": "splitter5", "part_id": "p3", "activity": "FINISH"}
    )
    s = kpi.get_snapshot()
    assert s["finished_count"] == 2
    assert s["flow_time_count"] == 2
    assert abs(s["avg_cycle_time_finished_sec"] - 12.5) < 0.02


def test_fail_on_station_increments_fail_count_not_scrap():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event({"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p12", "activity": "START"})
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.500",
            "component_id": "station41",
            "part_id": "p12",
            "activity": "LOAD",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:01.000",
            "component_id": "station41",
            "part_id": "p12",
            "activity": "FAIL",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:02.000",
            "component_id": "splitter5",
            "part_id": "p12",
            "activity": "FINISH",
        }
    )
    snap = kpi.get_snapshot()
    assert kpi.fail_event_count == 1
    assert snap["finished_count"] == 1
    assert snap["scrap_count"] == 0
    assert kpi._stn_state["station41"]["state"] == "FAIL"
    sp = snap["state_probability"]["station41"]
    assert "fail" in sp
    assert sp["fail"] > 0.0


def test_station_fail_unload_blocked_transfer_idle():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.000",
            "component_id": "station51",
            "part_id": "p1",
            "activity": "LOAD",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:01.000",
            "component_id": "station51",
            "part_id": "p1",
            "activity": "FAIL",
        }
    )
    assert kpi._stn_state["station51"]["state"] == "FAIL"
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:02.000",
            "component_id": "station51",
            "part_id": "p1",
            "activity": "UNLOAD",
        }
    )
    assert kpi._stn_state["station51"]["state"] == "BLOCKED"
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:03.000",
            "component_id": "station51",
            "activity": "TRANSFER",
        }
    )
    assert kpi._stn_state["station51"]["state"] == "IDLE"


def test_station_unload_from_idle_stays_idle():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.000",
            "component_id": "station52",
            "part_id": "p1",
            "activity": "UNLOAD",
        }
    )
    assert kpi._stn_state["station52"]["state"] == "IDLE"


def test_fail_on_splitter_not_counted():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.000",
            "component_id": "splitter5",
            "part_id": "pX",
            "activity": "FAIL",
        }
    )
    assert kpi.fail_event_count == 0


def test_stage1_entry_exit_flow_time():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station11", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:10.000", "component_id": "station11", "part_id": "p1", "activity": "TRANSFER"}
    )
    s = kpi.get_snapshot()
    st1 = s["stages"]["stage1"]
    assert st1["num_departures"] == 1
    assert st1["wip_instantaneous"] == 0
    assert abs(st1["avg_flow_time"] - 10.0) < 0.01


def test_stage2_splitter1_forward_transfer_exits():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station21", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "splitter1", "part_id": "p1", "activity": "RETURN"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "splitter1", "part_id": "p1", "activity": "TRANSFER"}
    )
    assert kpi.get_snapshot()["stages"]["stage2"]["num_departures"] == 0
    kpi.on_event(
        {"time": "2026-03-12T18:00:03.000", "component_id": "splitter1", "part_id": "p1", "activity": "FORWARD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:04.000", "component_id": "splitter1", "part_id": "p1", "activity": "TRANSFER"}
    )
    assert kpi.get_snapshot()["stages"]["stage2"]["num_departures"] == 1


def test_stage_wip_sum_equals_system_wip():
    """Each open lap is attributed to exactly one stage (active, transit, or pre-M1)."""
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "corner2", "part_id": "p2", "activity": "START"}
    )
    snap = kpi.get_snapshot()
    stage_sum = sum(snap["stages"][f"stage{i}"]["wip_instantaneous"] for i in range(1, 7))
    assert snap["wip_instantaneous"] == 2
    assert stage_sum == 2
    assert snap["stages"]["stage1"]["wip_instantaneous"] == 2

    kpi.on_event(
        {"time": "2026-03-12T18:00:05.000", "component_id": "station11", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:15.000", "component_id": "station11", "part_id": "p1", "activity": "TRANSFER"}
    )
    snap = kpi.get_snapshot()
    stage_sum = sum(snap["stages"][f"stage{i}"]["wip_instantaneous"] for i in range(1, 7))
    assert snap["wip_instantaneous"] == 2
    assert stage_sum == 2
    assert snap["stages"]["stage1"]["wip_instantaneous"] == 1
    assert snap["stages"]["stage2"]["wip_instantaneous"] == 1


def test_station22_load_reenters_looping_stage2():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "station21", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "splitter1", "part_id": "p1", "activity": "FORWARD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:03.000", "component_id": "splitter1", "part_id": "p1", "activity": "TRANSFER"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:04.000", "component_id": "station22", "part_id": "p1", "activity": "LOAD"}
    )
    snap = kpi.get_snapshot()
    assert snap["stages"]["stage2"]["wip_instantaneous"] == 1
    assert snap["wip_instantaneous"] == 1


def test_cross_stage_load_reconcile_does_not_create_departure():
    """If a part appears in next stage without formal previous exit, only WIP is reconciled."""
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "station11", "part_id": "p1", "activity": "LOAD"}
    )
    # Missing station11 TRANSFER, direct next-stage LOAD.
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "station21", "part_id": "p1", "activity": "LOAD"}
    )
    s = kpi.get_snapshot()["stages"]
    # Stage1 reconciled down to keep one-stage occupancy, but no formal departure counted.
    assert s["stage1"]["wip_instantaneous"] == 0
    assert s["stage1"]["num_departures"] == 0
    # Part now belongs to Stage2 only.
    assert s["stage2"]["wip_instantaneous"] == 1


def test_stage4_splitter3_and_4_dedup_one_departure_per_load():
    """Same part: if both splitters see FORWARD+TRANSFER, only one Stage4 exit (one LOAD token)."""
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:00.000",
            "component_id": "station41",
            "part_id": "p1",
            "activity": "LOAD",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:01.000",
            "component_id": "splitter3",
            "part_id": "p1",
            "activity": "FORWARD",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:02.000",
            "component_id": "splitter4",
            "part_id": "p1",
            "activity": "FORWARD",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:03.000",
            "component_id": "splitter3",
            "part_id": "p1",
            "activity": "TRANSFER",
        }
    )
    kpi.on_event(
        {
            "time": "2026-03-12T18:00:04.000",
            "component_id": "splitter4",
            "part_id": "p1",
            "activity": "TRANSFER",
        }
    )
    s4 = kpi.get_snapshot()["stages"]["stage4"]
    assert s4["num_departures"] == 1, "second splitter TRANSFER should be ignored"
    assert s4["wip_instantaneous"] == 0


def test_looping_stage_repeated_load_creates_new_pass_sample():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station41", "part_id": "p1", "activity": "LOAD"}
    )
    # Re-load in same looping stage without formal exit: should not be swallowed.
    kpi.on_event(
        {"time": "2026-03-12T18:00:10.000", "component_id": "station41", "part_id": "p1", "activity": "LOAD"}
    )
    # One normal stage-4 exit path.
    kpi.on_event(
        {"time": "2026-03-12T18:00:12.000", "component_id": "splitter3", "part_id": "p1", "activity": "FORWARD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:15.000", "component_id": "splitter3", "part_id": "p1", "activity": "TRANSFER"}
    )
    s4 = kpi.get_snapshot()["stages"]["stage4"]
    assert s4["num_departures"] == 2
    assert s4["wip_instantaneous"] == 0


def test_trend_throughput_rates_only_at_departures():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:01:00.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    snap = kpi.get_snapshot()
    thr = snap["trend_throughput_rates"]
    assert len(thr) == 1
    assert thr[0][1] == round(1 / 60.0, 5)
    assert snap["trend_departure_history"][-1][1] == 1


def test_trend_rate_history_uses_rolling_window_not_lifetime_cumulative():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    base = "2026-03-12T18:00:00.000"
    # Ten good finishes, then five scraps in a row — rolling window should dip.
    for i in range(10):
        kpi.on_event(
            {
                "time": base,
                "component_id": "corner2",
                "part_id": f"p{i}",
                "activity": "START",
            }
        )
        kpi.on_event(
            {
                "time": f"2026-03-12T18:00:{10 + i:02d}.000",
                "component_id": "splitter5",
                "part_id": f"p{i}",
                "activity": "FINISH",
            }
        )
    for i in range(10, 15):
        kpi.on_event(
            {
                "time": base,
                "component_id": "corner2",
                "part_id": f"p{i}",
                "activity": "START",
            }
        )
        kpi.on_event(
            {
                "time": f"2026-03-12T18:01:{i:02d}.000",
                "component_id": "splitter5",
                "part_id": f"p{i}",
                "activity": "SCRAP",
            }
        )
    trend = kpi.get_snapshot()["trend_rate_history"]
    assert trend
    last_comp, last_scrap = trend[-1][1], trend[-1][2]
    assert last_scrap > 15.0
    assert last_comp < 90.0
    snap = kpi.get_snapshot()
    assert snap["yield_rate"] > 0.6


def test_avg_wip_includes_tail_duration():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    # Keep WIP=1 until 100s, then finish.
    kpi.on_event(
        {"time": "2026-03-12T18:01:40.000", "component_id": "splitter5", "part_id": "p1", "activity": "FINISH"}
    )
    s = kpi.get_snapshot()
    assert abs(float(s["avg_wip"]) - 1.0) < 0.02


def test_forced_exit_is_counted_but_not_departure():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:01.000", "component_id": "station11", "part_id": "p1", "activity": "LOAD"}
    )
    # Missing station11 TRANSFER; next stage LOAD triggers reconciliation only.
    kpi.on_event(
        {"time": "2026-03-12T18:00:02.000", "component_id": "station21", "part_id": "p1", "activity": "LOAD"}
    )
    st = kpi.get_snapshot()["stages"]
    assert st["stage1"]["num_departures"] == 0
    assert kpi.get_snapshot()["debug"] == {}


def test_forced_exit_debug_counter_exposed_when_enabled():
    os.environ["KPI_WIP_DEBUG"] = "1"
    try:
        kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
        kpi.on_event(
            {"time": "2026-03-12T18:00:00.000", "component_id": "corner2", "part_id": "p1", "activity": "START"}
        )
        kpi.on_event(
            {"time": "2026-03-12T18:00:01.000", "component_id": "station11", "part_id": "p1", "activity": "LOAD"}
        )
        kpi.on_event(
            {"time": "2026-03-12T18:00:02.000", "component_id": "station21", "part_id": "p1", "activity": "LOAD"}
        )
        dbg = kpi.get_snapshot()["debug"]
        assert dbg["stage_forced_exits"]["stage1"] == 1
    finally:
        os.environ.pop("KPI_WIP_DEBUG", None)


def test_utilization_counts_fail_as_occupied_time():
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station41", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:10.000", "component_id": "station41", "part_id": "p1", "activity": "FAIL"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:20.000", "component_id": "station41", "part_id": "p1", "activity": "UNLOAD"}
    )
    s = kpi.get_snapshot()
    # Busy 10s + Fail 10s over 20s occupied timeline before UNLOAD.
    assert s["utilization"]["station41"] > 0.9


def test_block_event_transitions_busy_to_blocked():
    """PROCESS→BLOCK wait is BLOCKED, not extended BUSY (优化5 §2.1)."""
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    kpi.on_event(
        {"time": "2026-03-12T18:00:00.000", "component_id": "station31", "part_id": "p1", "activity": "LOAD"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:03.000", "component_id": "station31", "part_id": "p1", "activity": "PROCESS"}
    )
    kpi.on_event(
        {"time": "2026-03-12T18:00:13.000", "component_id": "station31", "part_id": "p1", "activity": "BLOCK"}
    )
    assert kpi._stn_state["station31"]["state"] == "BLOCKED"


def test_scrap_lap_keeps_visited_slots_done_not_scrap_cells():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import part_track_conformance as ptc

    steps = [
        {"timestamp": 1.0, "time": "2026-05-09T16:00:00", "component_id": "corner2", "activity": "START"},
        {"timestamp": 2.0, "time": "2026-05-09T16:00:01", "component_id": "station11", "activity": "LOAD"},
        {"timestamp": 3.0, "time": "2026-05-09T16:00:02", "component_id": "station11", "activity": "PROCESS"},
        {"timestamp": 4.0, "time": "2026-05-09T16:00:03", "component_id": "station11", "activity": "UNLOAD"},
        {"timestamp": 5.0, "time": "2026-05-09T16:00:04", "component_id": "splitter5", "activity": "SCRAP"},
    ]
    rep = ptc.replay_part_trace(steps)
    dg = rep["display_grid"]
    assert dg.get("st11") == "DONE"
    assert dg.get("st21_22") == "NOT_DONE"
    lab, style = ptc.conformance_column_display(rep)
    assert style == "scrap"
    ic, body, _ = ptc._trace_conformance_span(
        rep["laps"][0]["final_grid"],
        rep["laps"][0].get("lap_failed_stations") or set(),
        lap_outcome="SCRAP",
    )
    assert body == "Scrap"
    assert ic == "\u2717"
    html = ptc.format_complete_trace_meaningful_html(rep)
    assert "Scrap" in html
    assert "Normal" not in html


def test_station41_station71_pass_events_are_meaningful():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import part_track_conformance as ptc

    for comp in ("station41", "station71"):
        ev = {"component_id": comp, "activity": "PASS"}
        assert ptc.event_is_meaningful(ev)


def test_splitter2_corner1_splitter5_return_events_are_meaningful():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import part_track_conformance as ptc

    assert ptc.event_is_meaningful(
        {"component_id": "splitter2", "activity": "FORWARD"}
    )
    assert ptc.event_is_meaningful(
        {"component_id": "splitter2", "activity": "RETURN"}
    )
    assert ptc.event_is_meaningful(
        {"component_id": "corner1", "activity": "TRANSFER"}
    )
    assert ptc.event_is_meaningful(
        {"component_id": "corner1", "activity": "RETURN"}
    )
    assert ptc.event_is_meaningful(
        {"component_id": "splitter5", "activity": "RETURN"}
    )


def test_scrap_on_non_splitter5_does_not_close_lap():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import part_track_conformance as ptc

    steps = [
        {"timestamp": 1.0, "time": "2026-05-09T16:00:00", "component_id": "corner2", "activity": "START"},
        {"timestamp": 2.0, "time": "2026-05-09T16:00:01", "component_id": "station11", "activity": "LOAD"},
        {"timestamp": 3.0, "time": "2026-05-09T16:00:02", "component_id": "station41", "activity": "SCRAP"},
    ]
    rep = ptc.replay_part_trace(steps)
    assert rep["lap_open"] is True
    assert not rep["laps"]


def test_flow_step_grid_separates_m4_m5_first_and_second_pass():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import flow_conformance_engine as fce

    st = fce.new_flow_state()
    for idx in (0, 1, 2, 3, 4):
        st.reached[idx] = True
    dg = fce.flow_state_to_step_display_grid(st)
    assert dg[3] == "DONE"
    assert dg[4] == "DONE"
    assert dg[5] == "NOT_DONE"
    assert dg[6] == "NOT_DONE"


def test_factory_floor_event_cursor_no_dup_or_gap():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "streamlit_app"))
    import factory_floor_sim as ffs

    events = [
        {"timestamp": 1.0, "event_id": "a", "part_id": "p1", "component_id": "corner2", "activity": "START", "time": "t1"},
        {"timestamp": 1.0, "event_id": "b", "part_id": "p1", "component_id": "corner2", "activity": "TRANSFER", "time": "t2"},
        {"timestamp": 2.0, "event_id": "c", "part_id": "p1", "component_id": "station11", "activity": "LOAD", "time": "t3"},
    ]
    first = ffs.events_after_cursor(events, None, "")
    assert len(first) == 3
    sim = ffs.empty_sim_state()
    ffs.replay_events(sim, first)
    ts, eid = ffs.cursor_after_events(first)
    second = ffs.events_after_cursor(events, ts, eid)
    assert second == []
    third = ffs.events_after_cursor(events, 1.0, "a")
    assert len(third) == 2
    assert third[0]["event_id"] == "b"


_ROOT = os.path.dirname(os.path.abspath(__file__))
_EVENT_LOG_260312 = os.path.join(_ROOT, "event-logs", "event_log_260312_180229.csv")
_EVENT_LOG_260509 = os.path.join(_ROOT, "event-logs", "event_log_260509_164517.csv")


@pytest.mark.skipif(
    not os.path.isfile(_EVENT_LOG_260509),
    reason="sample plant log not in repo",
)
def test_plant_log_260509_stage_wip_matches_system():
    import pandas as pd

    df = pd.read_csv(_EVENT_LOG_260509)
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    mismatches = 0
    for row in df.to_dict("records"):
        kpi.on_event(
            {
                "time": row["time"],
                "component_id": row["component_id"],
                "part_id": row["part_id"],
                "activity": row["activity"],
            }
        )
        snap = kpi.get_snapshot()
        sys_w = snap["wip_instantaneous"]
        stage_sum = sum(
            snap["stages"][f"stage{i}"]["wip_instantaneous"] for i in range(1, 7)
        )
        if sys_w != stage_sum:
            mismatches += 1
    assert mismatches == 0, "stage WIP sum must equal system WIP on every event"
    assert snap["wip_instantaneous"] > 0


@pytest.mark.skipif(
    not os.path.isfile(_EVENT_LOG_260312),
    reason="sample plant log not in repo",
)
def test_plant_log_260312_regression():
    import pandas as pd

    df = pd.read_csv(_EVENT_LOG_260312)
    df = df.sort_values("time")
    buf = event_buffer.EventBuffer(window_ms=50, max_size=500)
    kpi = kpi_calculator.KpiCalculator(observation_time_mode="replay")
    for ev in df.to_dict("records"):
        for e in buf.add_and_flush(ev)[0]:
            kpi.on_event(e)
    last_t = pd.Timestamp(df.iloc[-1]["time"])
    drain_ev = {
        "time": (last_t + pd.Timedelta(seconds=5)).isoformat(),
        "component_id": "corner2",
        "part_id": "_buffer_drain",
        "activity": "TRANSFER",
    }
    for e in buf.add_and_flush(drain_ev)[0]:
        kpi.on_event(e)
    s = kpi.get_snapshot()
    assert s["finished_count"] == 10
    assert s["scrap_count"] == 0
    assert abs(float(s["yield_rate"]) - 1.0) < 0.01
    assert "stages" in s and len(s["stages"]) == 6
    assert "stage1" in s["stages"] and "stage6" in s["stages"]


if __name__ == "__main__":
    test_part_id_schema_aliases_entity_and_partid()
    test_throughput()
    test_system_wip_start_finish_scrap()
    test_second_start_same_part_before_first_finish_is_ignored_for_wip()
    test_cycle_time_finished()
    test_finish_case_insensitive()
    test_fail_on_station_increments_fail_count_not_scrap()
    test_fail_on_splitter_not_counted()
    test_stage1_entry_exit_flow_time()
    test_stage2_splitter1_forward_transfer_exits()
    test_stage_wip_sum_equals_system_wip()
    test_station22_load_reenters_looping_stage2()
    test_cross_stage_load_reconcile_does_not_create_departure()
    test_stage4_splitter3_and_4_dedup_one_departure_per_load()
    test_looping_stage_repeated_load_creates_new_pass_sample()
    test_avg_wip_includes_tail_duration()
    test_forced_exit_is_counted_but_not_departure()
    test_forced_exit_debug_counter_exposed_when_enabled()
    test_utilization_counts_fail_as_occupied_time()
    test_reset()
    test_two_finishes_same_part()
    test_divide_by_zero()
    test_station_live_busy_transfer()
    test_station_live_pass()
    print("All KPI tests passed.")

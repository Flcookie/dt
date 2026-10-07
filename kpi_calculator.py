# kpi_calculator.py — System / Stage / Station KPIs (streaming, event-driven)
#
# System: WIP = open “lap” jobs: corner2 START +1, splitter5 FINISH/SCRAP -1 (floor 0).
#   **At most one +1 per part** until that part FINISH/SCRAP: duplicate/half-open STARTs are ignored
#   so WIP is not 2× physical part count. corner2 RETURN/TRANSFER do not change WIP.
#   Debug: ``KPI_WIP_DEBUG=1`` prints WIP steps for corner2 / splitter5.
# Stage: entry on LOAD at anchor stations; exits per splitter TRANSFER rules and station TRANSFERs.
#   **One stage per open lap at a time**: sum of stage ``wip_instantaneous`` equals system WIP.
#   Parts between formal exit and next anchor LOAD sit in ``_part_transit_stage`` (still counted).
#   corner2 START assigns transit stage 1 until first anchor LOAD. Stage4: splitter3/4 exit at most
#   once per Stage4 entry LOAD (open question 1). Looping stages: a repeated LOAD closes a pass.
# Station: BUSY / FAIL / BLOCKED / IDLE state machine; utilization = P_busy + P_fail.
# BLOCK @ station*: downstream blocked before UNLOAD → BLOCKED (not extended BUSY).
# splitter5 CHECKOUT: pre-notification before FINISH/SCRAP; ignored for KPI counts.
#
# Part id: part_id, partId, entity_id, entityId — see _extract_part_id.
#
# Open questions (implemented behaviour kept as is):
#   1. Stage4 exits: the original note said "one per station41 LOAD"; the code allows one per
#      Stage4 entry LOAD (station41 / station51 / station52).
#   2. Looping stages 2, 4, 6: why a part stays counted in the same stage after a formal exit,
#      and why stage 6 (station71 only) loops at all.
#   3. Realtime window: why it falls back to the last event 30 s after it (_observation_end_ts).
#   4. Station shares cover the time since the station's first event, not the whole window.

from __future__ import annotations

import datetime
import os
from collections import defaultdict
from typing import Any


def _kpi_wip_debug_enabled() -> bool:
    """Set ``KPI_WIP_DEBUG=1`` (or true/yes/on) to print WIP transition diagnostics."""
    v = (os.environ.get("KPI_WIP_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on", "y")

STAGE_ENTRY: dict[str, int] = {
    "station11": 1,
    "station21": 2,
    "station22": 2,
    "station31": 3,
    "station41": 4,
    "station51": 4,
    "station52": 4,
    "station61": 5,
    "station71": 6,
}

STATION_KPI_IDS: frozenset[str] = frozenset(
    (
        "station11",
        "station21",
        "station22",
        "station31",
        "station41",
        "station51",
        "station52",
        "station61",
        "station71",
    )
)

_SPLITTER_MARK_ACTS = frozenset(("FORWARD", "RETURN"))

# A repeated LOAD in these stages closes the previous pass (open question 2).
_LOOPING_STAGES = (2, 4, 6)

# Activities that should carry a part id; without one a (capped) warning is printed.
_ACTIVITIES_NEEDING_PART_ID = ("START", "FINISH", "LOAD", "UNLOAD", "TRANSFER", "SCRAP", "FAIL")

# TRANSFER at these components is the formal exit of the given stage.
_TRANSFER_EXIT_STAGE: dict[str, int] = {
    "station11": 1,
    "station31": 3,
    "station61": 5,
    "corner1": 6,
}

# Splitters whose TRANSFER exits a stage only after a FORWARD mark
# (splitter1 -> stage 2; splitter3 / splitter4 -> stage 4).
_FORWARD_EXIT_SPLITTERS = frozenset(("splitter1", "splitter3", "splitter4"))


def _parse_ts(time_str: str) -> float:
    s = str(time_str).strip()
    if not s:
        return datetime.datetime.now().timestamp()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) > 10 and s[10] == " ":
        s = s[:10] + "T" + s[11:]
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return datetime.datetime.now().timestamp()


def _extract_part_id(event: dict | None) -> str:
    if not event:
        return ""
    return str(
        event.get("part_id")
        or event.get("partId")
        or event.get("entity_id")
        or event.get("entityId")
        or ""
    ).strip()


def _time_weighted_average_wip(
    wip_history: list[tuple[float, int]], observation_seconds: float, observation_end_ts: float
) -> float:
    """Average WIP over the observation window, weighted by how long each WIP value held.

    ``wip_history`` holds (time of change, WIP after the change). Each value counts until the
    next change; the last one until ``observation_end_ts``. Negative spans count as 0. The sum
    is divided by the whole observation window (from the first event), so time before the
    first recorded change contributes WIP 0.
    """
    if not wip_history or observation_seconds <= 0:
        return 0.0
    wip_seconds = 0.0
    for i in range(len(wip_history) - 1):
        change_ts, wip = wip_history[i]
        next_change_ts, _ = wip_history[i + 1]
        wip_seconds += float(wip) * max(0.0, next_change_ts - change_ts)
    last_change_ts, last_wip = wip_history[-1]
    wip_seconds += float(last_wip) * max(0.0, observation_end_ts - last_change_ts)
    return wip_seconds / observation_seconds


# Trend charts look at the most recent departures (FINISH or SCRAP), not lifetime totals.
TREND_ROLLING_DEPARTURES = 20
# Trend series use only the most recent history entries / finished cycle times.
_TREND_HISTORY_POINTS = 200
_TREND_CYCLE_TIME_SAMPLES = 1000
# Realtime observation window: see KpiCalculator._observation_end_ts.
_REALTIME_STALE_AFTER_SEC = 30.0


def _rolling_departure_shares_pct(
    departure_history: list[tuple[float, int, int]],
    *,
    window: int = TREND_ROLLING_DEPARTURES,
) -> list[tuple[float, float, float]]:
    """(ts, completion %, scrap %) of the recent departures, at every history point.

    ``departure_history`` holds (ts, cumulative completions, cumulative scraps). From each
    point the window reaches back until it holds at least ``window`` departures (or the start
    of the given history); points whose window holds no departure are left out.
    Percentages are rounded to 2 decimals.
    """
    if not departure_history or window <= 0:
        return []
    out: list[tuple[float, float, float]] = []
    for i in range(len(departure_history)):
        ts, completions, scraps = departure_history[i]
        window_start = i
        departures_in_window = 0
        while window_start > 0 and departures_in_window < window:
            window_start -= 1
            departures_in_window += (
                departure_history[window_start + 1][1] - departure_history[window_start][1]
            ) + (departure_history[window_start + 1][2] - departure_history[window_start][2])
        completions_at_start = departure_history[window_start][1]
        scraps_at_start = departure_history[window_start][2]
        window_completions = int(completions) - int(completions_at_start)
        window_scraps = int(scraps) - int(scraps_at_start)
        window_departures = window_completions + window_scraps
        if window_departures <= 0:
            continue
        completion_pct = round(100.0 * window_completions / window_departures, 2)
        scrap_pct = round(100.0 * window_scraps / window_departures, 2)
        out.append((float(ts), completion_pct, scrap_pct))
    return out


def _rolling_departure_rates_per_sec(
    departure_history: list[tuple[float, int, int]],
    *,
    window: int = TREND_ROLLING_DEPARTURES,
) -> list[tuple[float, float, float]]:
    """(ts, completions per second, scraps per second) at every departure point.

    A departure point is a history entry where the cumulative completions or scraps changed.
    From each one the window reaches back over earlier departure points until it holds at
    least ``window`` departures. When it reaches the first departure point, it starts at the
    history entry just before it (or the first entry). Rate = departures in the window /
    seconds since the window start, rounded to 5 decimals; zero-length windows are left out.
    """
    if not departure_history or window <= 0:
        return []
    departure_points: list[int] = []
    for i in range(1, len(departure_history)):
        ts, completions, scraps = departure_history[i]
        prev_completions, prev_scraps = departure_history[i - 1][1], departure_history[i - 1][2]
        if int(completions) != int(prev_completions) or int(scraps) != int(prev_scraps):
            departure_points.append(i)
    if not departure_points:
        return []
    out: list[tuple[float, float, float]] = []
    for k in range(len(departure_points)):
        point = departure_points[k]
        ts, completions, scraps = departure_history[point]
        window_start_k = k
        departures_in_window = 0
        while window_start_k > 0 and departures_in_window < window:
            window_start_k -= 1
            older = departure_points[window_start_k]
            newer = departure_points[window_start_k + 1]
            departures_in_window += (departure_history[newer][1] - departure_history[older][1]) + (
                departure_history[newer][2] - departure_history[older][2]
            )
        if window_start_k == 0:
            before_first_departure = departure_points[0] - 1
            if before_first_departure < 0:
                start_ts, completions_at_start, scraps_at_start = departure_history[0]
            else:
                start_ts, completions_at_start, scraps_at_start = departure_history[before_first_departure]
        else:
            start_ts, completions_at_start, scraps_at_start = departure_history[
                departure_points[window_start_k]
            ]
        window_seconds = float(ts) - float(start_ts)
        if window_seconds <= 0:
            continue
        out.append(
            (
                float(ts),
                round((int(completions) - int(completions_at_start)) / window_seconds, 5),
                round((int(scraps) - int(scraps_at_start)) / window_seconds, 5),
            )
        )
    return out


class KpiCalculator:
    def __init__(
        self,
        observation_time_mode: str = "realtime",
        finish_events: list[str] | None = None,
        scrap_events: list[str] | None = None,
    ):
        fe = finish_events or ["FINISH"]
        se = scrap_events or ["SCRAP"]
        self._finish_upper = {str(x).strip().upper() for x in fe}
        self._scrap_upper = {str(x).strip().upper() for x in se}
        self.observation_time_mode = observation_time_mode

        cap_raw = os.environ.get("KPI_MISSING_PART_WARN_MAX", "40")
        try:
            self._missing_part_warn_cap = max(0, int(cap_raw))
        except ValueError:
            self._missing_part_warn_cap = 40
        self._missing_part_warns_emitted = 0

        self.reset()
        if _kpi_wip_debug_enabled():
            print(
                "[KPI WIP] _finish_upper =",
                sorted(self._finish_upper),
                "| expect FINISH in set:",
                "FINISH" in self._finish_upper,
            )
            print("[KPI WIP] _scrap_upper =", sorted(self._scrap_upper))

    def reset(self) -> None:
        self.observation_start_ts: float | None = None
        self.last_event_ts: float | None = None

        # --- System ---
        # System WIP = number of open laps; history gets (ts, WIP) when a lap opens or closes.
        self.sys_wip = 0
        self.sys_wip_history: list[tuple[float, int]] = []
        # (event_ts, completions, scraps) — rates derived when building trend series
        self.sys_rate_history: list[tuple[float, int, int]] = []
        # part_id -> time of its counted START (cycle time); kept after close, overwritten by the next.
        self.sys_start_time: dict[str, float] = {}
        self.num_completions = 0
        self.num_scraps = 0
        self.finished_cycle_times: list[float] = []
        self.scrapped_cycle_times: list[float] = []
        self.duplicate_start_count = 0
        # Part has a counted corner2 START, not yet closed by splitter5 FINISH/SCRAP
        self._open_lap: set[str] = set()

        # --- Stage ---
        self.stage_wip: dict[int, int] = {s: 0 for s in range(1, 7)}
        self.stage_wip_hist: dict[int, list[tuple[float, int]]] = {
            s: [] for s in range(1, 7)
        }
        self.stage_departures: dict[int, int] = {s: 0 for s in range(1, 7)}
        self.stage_forced_exits: dict[int, int] = {s: 0 for s in range(1, 7)}
        self.stage_flow_times: dict[int, list[float]] = defaultdict(list)
        # (part_id, stage) -> entry time; a formal exit turns it into a flow-time sample,
        # a forced leave or lap close drops it.
        self.stage_entry_time: dict[tuple[str, int], float] = {}
        # (splitter, part_id) -> last FORWARD / RETURN there; never cleared.
        self.last_splitter_mark: dict[tuple[str, str], str] = {}
        # part_id -> Stage4 exits still allowed: +1 per Stage4 entry LOAD, -1 per Stage4 exit or
        # forced leave of Stage4, dropped at lap close (open question 1).
        self._s4_pending_exits: dict[str, int] = {}
        # part_id -> stage 1..6 they are in (in-model); at most one stage per part
        self._part_current_stage: dict[str, int] = {}
        # open lap, exited anchor but not yet at next anchor (or pre-first-anchor after START)
        self._part_transit_stage: dict[str, int] = {}

        # --- Station (BUSY / FAIL / BLOCKED / IDLE) ---
        # station_id -> {"state", "start": when the current state began, "part": current part}
        self._stn_state: dict[str, dict[str, Any]] = {}
        # station_id -> seconds spent in each state, up to "start" of the current state
        self._stn_acc: dict[str, dict[str, float]] = {}
        # stations that received a state-machine activity; only these get real KPIs
        self._stn_touched: set[str] = set()
        self._distinct_part_ids: set[str] = set()
        # FAIL events at KPI stations (whatever the station's state)
        self.fail_event_count = 0

        self._missing_part_warns_emitted = 0

    @property
    def current_wip(self) -> int:
        return self.sys_wip

    @property
    def finished_count(self) -> int:
        return self.num_completions

    def _warn_missing_part_id(self, event: dict) -> None:
        if self._missing_part_warns_emitted >= self._missing_part_warn_cap:
            return  # also when the cap is 0 (warnings off)
        self._missing_part_warns_emitted += 1
        print(
            "[KPI WARNING] missing part id (checked part_id, partId, entity_id, entityId): {}".format(
                event
            )
        )
        if self._missing_part_warns_emitted == self._missing_part_warn_cap:
            print(
                "[kpi_calculator] Further missing-part warnings suppressed "
                "(set KPI_MISSING_PART_WARN_MAX=0 to disable or raise cap)."
            )

    def _append_sys_wip(self, ts: float) -> None:
        self.sys_wip_history.append((ts, self.sys_wip))
        # Keep departure cumulative series aligned with WIP trend timestamps so
        # Completion/Scrap charts share the x-axis even before the first FINISH.
        self.sys_rate_history.append(
            (ts, self.num_completions, self.num_scraps)
        )

    def _clear_part_stage_state(self, part_id: str) -> None:
        """Lap closed: drop the part's stage state (no departures or flow-time samples)."""
        self._part_current_stage.pop(part_id, None)
        self._part_transit_stage.pop(part_id, None)
        self._s4_pending_exits.pop(part_id, None)
        for key in [k for k in self.stage_entry_time if k[0] == part_id]:
            del self.stage_entry_time[key]

    def _sync_stage_wip_at(self, ts: float) -> None:
        """Derive stage WIP from open laps so sum(stage) == system WIP: each part counts in its
        current stage, else its transit stage, else stage 1. History gets (ts, WIP) on change."""
        counts = {s: 0 for s in range(1, 7)}
        for part_id in self._open_lap:
            stage = self._part_current_stage.get(part_id)
            if stage is None:
                stage = self._part_transit_stage.get(part_id, 1)
            counts[stage] += 1
        for stage in range(1, 7):
            if self.stage_wip[stage] != counts[stage]:
                self.stage_wip[stage] = counts[stage]
                self.stage_wip_hist[stage].append((ts, counts[stage]))

    def _stage_force_leave_without_departure(self, stage: int, part_id: str) -> None:
        """The part shows up in another stage without a formal exit from ``stage``: it leaves
        for WIP only, with no departure or flow-time sample. Uses up one pending Stage4 exit."""
        self.stage_forced_exits[stage] += 1
        self.stage_entry_time.pop((part_id, stage), None)
        if stage == 4 and self._s4_pending_exits.get(part_id, 0) > 0:
            self._s4_pending_exits[part_id] -= 1
        del self._part_current_stage[part_id]  # ``stage`` is the part's current stage

    def _stage_entry(self, stage: int, part_id: str, ts: float) -> None:
        """LOAD at an anchor station of ``stage``.

        Repeated LOAD in the same stage: a looping stage first closes the previous pass as a
        formal exit; stages 1, 3, 5 ignore it. LOAD in another stage: the part first leaves its
        current stage without a departure.
        """
        current_stage = self._part_current_stage.get(part_id)
        if current_stage == stage:
            if stage not in _LOOPING_STAGES:
                return
            self._stage_exit(stage, part_id, ts)
        elif current_stage is not None:
            self._stage_force_leave_without_departure(current_stage, part_id)
        self._part_transit_stage.pop(part_id, None)
        self._part_current_stage[part_id] = stage
        self.stage_entry_time[(part_id, stage)] = ts
        if stage == 4:
            self._s4_pending_exits[part_id] = self._s4_pending_exits.get(part_id, 0) + 1

    def _stage_exit(self, stage: int, part_id: str, ts: float) -> None:
        """Formal exit of ``stage``: a departure and flow-time sample only if the entry is on
        record. An open-lap part then counts in the next stage until its next entry; after a
        looping stage it stays in the same stage (open question 2)."""
        entry_ts = self.stage_entry_time.pop((part_id, stage), None)
        if entry_ts is not None:
            self.stage_flow_times[stage].append(ts - entry_ts)
            self.stage_departures[stage] += 1
        if self._part_current_stage.get(part_id) == stage:
            del self._part_current_stage[part_id]
        if part_id in self._open_lap:
            self._part_transit_stage[part_id] = stage if stage in _LOOPING_STAGES else min(6, stage + 1)

    def _stage_exit_on_event(self, component_id: str, activity: str, part_id: str, ts: float) -> None:
        """Formal stage exits: TRANSFER at a station / corner1, or TRANSFER at a splitter whose
        last mark for the part is FORWARD (not RETURN)."""
        if component_id in _FORWARD_EXIT_SPLITTERS:
            if activity in _SPLITTER_MARK_ACTS:
                self.last_splitter_mark[(component_id, part_id)] = activity
            elif activity == "TRANSFER" and self.last_splitter_mark.get((component_id, part_id)) == "FORWARD":
                if component_id == "splitter1":
                    self._stage_exit(2, part_id, ts)
                elif self._s4_pending_exits.get(part_id, 0) > 0:
                    # splitter3 / splitter4: one Stage4 exit per pending entry, so the other
                    # splitter in the same pass does not exit again (open question 1).
                    self._s4_pending_exits[part_id] -= 1
                    self._stage_exit(4, part_id, ts)
        elif activity == "TRANSFER" and component_id in _TRANSFER_EXIT_STAGE:
            self._stage_exit(_TRANSFER_EXIT_STAGE[component_id], part_id, ts)

    def _set_station_state(self, station_id: str, new_state: str, ts: float) -> None:
        """Book the time since the current state began (a late ``ts`` adds nothing), then switch."""
        station = self._stn_state[station_id]
        self._stn_acc[station_id][station["state"]] += max(0.0, ts - station["start"])
        station["state"] = new_state
        station["start"] = ts

    def _open_lap_on_start(self, part_id: str, ts: float) -> None:
        """corner2 START: open a lap (system WIP +1); a part already in an open lap is not recounted."""
        if part_id in self._open_lap:
            self.duplicate_start_count += 1
            if _kpi_wip_debug_enabled():
                print(
                    f"[KPI WIP] skip duplicate corner2 START {part_id!r} (lap still open), "
                    f"sys_wip={self.sys_wip}",
                    flush=True,
                )
            return

        self._open_lap.add(part_id)
        self._part_transit_stage[part_id] = 1
        wip_before = self.sys_wip
        self.sys_wip += 1
        if _kpi_wip_debug_enabled():
            print(
                f"[KPI WIP+1] (comp,act)=('corner2','START') part_id={part_id!r} "
                f"wip {wip_before}->{self.sys_wip}",
                flush=True,
            )
        self._append_sys_wip(ts)
        self.sys_start_time[part_id] = ts

    def _close_lap(self, part_id: str, ts: float, activity: str, *, finished: bool) -> None:
        """splitter5 FINISH (``finished``) or SCRAP: close the open lap (system WIP -1).

        Counts the completion/scrap and its cycle time from the lap's START.
        A part without an open lap changes nothing (only the debug line is printed).
        """
        had_open = part_id in self._open_lap
        wip_before = self.sys_wip
        if had_open:
            self._open_lap.discard(part_id)
            self._clear_part_stage_state(part_id)
            self.sys_wip = max(0, self.sys_wip - 1)
            if finished:
                self.num_completions += 1
            else:
                self.num_scraps += 1
            self._append_sys_wip(ts)
            start_ts = self.sys_start_time.get(part_id)
            if start_ts is not None:
                cycle_times = self.finished_cycle_times if finished else self.scrapped_cycle_times
                cycle_times.append(ts - start_ts)
        if _kpi_wip_debug_enabled():
            print(
                f"[KPI WIP-1 {'FINISH' if finished else 'SCRAP'}] part_id={part_id!r} "
                f"had_open={had_open} wip {wip_before}->{self.sys_wip} act={activity!r}",
                flush=True,
            )

    def _update_station_state(self, station_id: str, activity: str, part_id: str, ts: float) -> None:
        """Station state machine; other activities, and PASS without a part, are ignored.

        LOAD           any state     -> BUSY, current part = this part (if given)
        FAIL           BUSY          -> FAIL (every FAIL is counted, whatever the state)
        UNLOAD, BLOCK  BUSY or FAIL  -> BLOCKED (BLOCK: downstream blocked, not extended BUSY)
        TRANSFER       BLOCKED       -> IDLE, current part cleared
        PASS           any state     -> current part = this part
        The station is reported (``_stn_touched``) even if its state does not change.
        """
        if activity not in ("LOAD", "FAIL", "UNLOAD", "BLOCK", "TRANSFER", "PASS"):
            return
        if activity == "PASS" and not part_id:
            return
        if activity == "FAIL":
            self.fail_event_count += 1
        self._stn_touched.add(station_id)
        if station_id not in self._stn_state:  # first activity: IDLE from here
            self._stn_state[station_id] = {"state": "IDLE", "start": ts, "part": ""}
            self._stn_acc[station_id] = {"BUSY": 0.0, "FAIL": 0.0, "BLOCKED": 0.0, "IDLE": 0.0}
        station = self._stn_state[station_id]
        state = station["state"]

        if activity == "LOAD":
            self._set_station_state(station_id, "BUSY", ts)
            if part_id:
                station["part"] = part_id
        elif activity == "FAIL" and state == "BUSY":
            self._set_station_state(station_id, "FAIL", ts)
        elif activity in ("UNLOAD", "BLOCK") and state in ("BUSY", "FAIL"):
            self._set_station_state(station_id, "BLOCKED", ts)
        elif activity == "TRANSFER" and state == "BLOCKED":
            self._set_station_state(station_id, "IDLE", ts)
            station["part"] = ""
        elif activity == "PASS":
            station["part"] = part_id

    def on_event(self, event: dict) -> None:
        """Apply one component event (events without a "time" are ignored)."""
        raw_time = event.get("time")
        if not raw_time:
            return
        ts = _parse_ts(str(raw_time))
        component_id = str(event.get("component_id", "") or "").strip()
        part_id = _extract_part_id(event)
        activity = str(event.get("activity", "") or "").strip().upper()

        if not part_id and activity in _ACTIVITIES_NEEDING_PART_ID:
            self._warn_missing_part_id(event)

        if self.observation_start_ts is None:
            self.observation_start_ts = ts
        self.last_event_ts = ts  # the latest *received*, not the latest timestamp

        if part_id:
            self._distinct_part_ids.add(part_id)

        # -------- System WIP (only these branches mutate sys_wip) --------
        if _kpi_wip_debug_enabled() and component_id in ("corner2", "splitter5"):
            print(
                f"[WIP] {component_id} {activity} | wip before={self.sys_wip}",
                flush=True,
            )

        if component_id == "corner2" and activity == "START" and part_id:
            self._open_lap_on_start(part_id, ts)

        # CHECKOUT (pre-signal) is ignored. An activity configured as both FINISH and SCRAP
        # closes the lap as FINISH; the SCRAP pass then finds no open lap.
        if component_id == "splitter5" and part_id:
            if activity in self._finish_upper:
                self._close_lap(part_id, ts, activity, finished=True)
            if activity in self._scrap_upper:
                self._close_lap(part_id, ts, activity, finished=False)

        # -------- Stage entry (LOAD @ anchors) --------
        if activity == "LOAD" and component_id in STAGE_ENTRY and part_id:
            self._stage_entry(STAGE_ENTRY[component_id], part_id, ts)

        # -------- Stage exits --------
        if part_id:
            self._stage_exit_on_event(component_id, activity, part_id, ts)

        # -------- Station BUSY / FAIL / BLOCKED / IDLE --------
        if component_id in STATION_KPI_IDS:
            self._update_station_state(component_id, activity, part_id, ts)

        self._sync_stage_wip_at(ts)

    def _stage_kpis(self, observation_seconds: float, observation_end_ts: float) -> dict[str, dict[str, Any]]:
        """Per stage, over the whole observation window:

        - wip_instantaneous: parts in the stage now; wip_average: time-weighted (3 decimals);
        - num_departures: formal exits only (forced reconciliation exits are not counted);
        - throughput: departures per second of observation (4 decimals);
        - avg_flow_time: mean seconds from stage entry to formal exit (1 decimal, 0.0 if none).
        """
        stages_out: dict[str, dict[str, Any]] = {}
        for stage in range(1, 7):
            avg_wip = round(
                _time_weighted_average_wip(self.stage_wip_hist[stage], observation_seconds, observation_end_ts), 3
            )
            departures = self.stage_departures[stage]
            # stage_flow_times is a defaultdict: this lookup also creates the empty list for a
            # stage without samples (kept as is; the snapshot has always done this).
            flow_times = self.stage_flow_times[stage]
            avg_flow_time = round(sum(flow_times) / len(flow_times), 1) if flow_times else 0.0
            throughput_per_sec = round(departures / observation_seconds, 4)
            stages_out["stage{}".format(stage)] = {
                "wip_instantaneous": self.stage_wip[stage],
                "wip_average": avg_wip,
                "num_departures": departures,
                "throughput": throughput_per_sec,
                "avg_flow_time": avg_flow_time,
            }
        return stages_out

    def _station_kpis(
        self, observation_end_ts: float
    ) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, dict[str, str]]]:
        """Per station: utilization, state probabilities and live state.

        Shares are over the time since the station's first state event (open question 4); the
        current state's open interval counts up to ``observation_end_ts``. utilization = busy +
        fail share, clamped to 0..1.
        All shares are rounded to 4 decimals. A station without any state event reports
        idle = 1.0; so does one whose tracked time is still zero.
        """
        utilization: dict[str, float] = {}
        state_probability: dict[str, dict[str, float]] = {}
        station_live: dict[str, dict[str, str]] = {}

        for station_id in sorted(STATION_KPI_IDS):
            if station_id not in self._stn_touched or station_id not in self._stn_state:
                utilization[station_id] = 0.0
                state_probability[station_id] = {
                    "busy": 0.0,
                    "fail": 0.0,
                    "blocked": 0.0,
                    "idle": 1.0,
                }
                station_live[station_id] = {
                    "current_state": "IDLE",
                    "current_part_id": "",
                    "queue_hint": "",
                }
                continue

            station = self._stn_state[station_id]
            seconds_in_state = dict(self._stn_acc[station_id])  # copy: the open interval is not stored
            current_state = str(station["state"])
            open_interval = max(0.0, observation_end_ts - float(station["start"]))
            seconds_in_state[current_state] = seconds_in_state.get(current_state, 0.0) + open_interval

            tracked_seconds = (
                seconds_in_state.get("BUSY", 0.0)
                + seconds_in_state.get("FAIL", 0.0)
                + seconds_in_state.get("BLOCKED", 0.0)
                + seconds_in_state.get("IDLE", 0.0)
            )
            if tracked_seconds < 1e-9:
                busy_share = fail_share = blocked_share = 0.0
                idle_share = 1.0
            else:
                busy_share = seconds_in_state.get("BUSY", 0.0) / tracked_seconds
                fail_share = seconds_in_state.get("FAIL", 0.0) / tracked_seconds
                blocked_share = seconds_in_state.get("BLOCKED", 0.0) / tracked_seconds
                idle_share = seconds_in_state.get("IDLE", 0.0) / tracked_seconds

            utilization[station_id] = round(max(0.0, min(1.0, busy_share + fail_share)), 4)
            state_probability[station_id] = {
                "busy": round(busy_share, 4),
                "fail": round(fail_share, 4),
                "blocked": round(blocked_share, 4),
                "idle": round(idle_share, 4),
            }
            station_live[station_id] = {
                "current_state": current_state,
                "current_part_id": str(station.get("part") or "").strip(),
                "queue_hint": "",
            }
        return utilization, state_probability, station_live

    def _observation_end_ts(self) -> float:
        """End of the observation window that time-based KPIs divide by (Unix seconds).

        replay: the last event's time; the wall clock only while there is no event yet.
        realtime: now, unless the last event is more than ``_REALTIME_STALE_AFTER_SEC`` old;
        then the window ends at the last event. So once events stop, rates and averages keep
        decreasing for 30 s and then jump back to their value at the last event (open question 3).
        """
        if self.observation_time_mode == "replay":
            return float(self.last_event_ts) if self.last_event_ts is not None else datetime.datetime.now().timestamp()
        now = datetime.datetime.now().timestamp()
        last_event = float(self.last_event_ts) if self.last_event_ts is not None else now
        return last_event if (now - last_event) > _REALTIME_STALE_AFTER_SEC else now

    def _trend_series(self) -> dict[str, list]:
        """Chart series, built from the last ``_TREND_HISTORY_POINTS`` history entries.

        The history is cut *before* the rolling windows are computed, so a window never
        reaches further back than those entries.
        """
        recent_departures = self.sys_rate_history[-_TREND_HISTORY_POINTS:]
        return {
            # [ts, system WIP after the change]
            "trend_sys_wip_history": [
                [float(ts), int(wip)] for ts, wip in self.sys_wip_history[-_TREND_HISTORY_POINTS:]
            ],
            # [ts, completion %, scrap %] among the recent departures (see _rolling_departure_shares_pct)
            "trend_rate_history": [
                [ts, completion_pct, scrap_pct]
                for ts, completion_pct, scrap_pct in _rolling_departure_shares_pct(recent_departures)
            ],
            # [ts, cumulative completions, cumulative scraps]
            "trend_departure_history": [
                [float(ts), int(completions), int(scraps)]
                for ts, completions, scraps in self.sys_rate_history[-_TREND_HISTORY_POINTS:]
            ],
            # [ts, completions/s, scraps/s] at departure points (see _rolling_departure_rates_per_sec)
            "trend_throughput_rates": [
                [ts, completions_per_sec, scraps_per_sec]
                for ts, completions_per_sec, scraps_per_sec in _rolling_departure_rates_per_sec(
                    self.sys_rate_history[-_TREND_HISTORY_POINTS:]
                )
            ],
            "trend_finished_cycle_times": [
                round(float(x), 4) for x in self.finished_cycle_times[-_TREND_CYCLE_TIME_SAMPLES:]
            ],
        }

    def get_snapshot(self) -> dict[str, Any]:
        """All KPIs at this moment. Not read-only: it first brings stage WIP up to date.

        Definitions (as implemented):
        - observation window: first event .. ``_observation_end_ts()`` (at least 0.001 s);
        - complete_rate / throughput: completions per second of observation (4 decimals);
        - scrap_rate: scraps / departed parts, a fraction 0..1, not per time (3 decimals);
        - yield_rate: completions / departed parts (4 decimals);
        - wip_average: time-weighted system WIP over the window (3 decimals);
        - avg_cycle_time_fin / _all: mean START -> FINISH (and -> SCRAP) seconds (1 decimal).
        """
        # Observation window
        observation_end_ts = self._observation_end_ts()
        start_ts = self.observation_start_ts
        observation_seconds = max(0.001, (observation_end_ts - start_ts) if start_ts is not None else 0.001)

        # Stage WIP is re-derived from the open laps at the last event's time
        # (updates stage_wip / stage_wip_hist when they differ).
        if self.last_event_ts is not None:
            self._sync_stage_wip_at(float(self.last_event_ts))

        # System KPIs
        avg_system_wip = round(
            _time_weighted_average_wip(self.sys_wip_history, observation_seconds, observation_end_ts), 3
        )
        departed = self.num_completions + self.num_scraps
        scrap_fraction = round(self.num_scraps / departed, 3) if departed > 0 else 0.0
        completions_per_sec = round(self.num_completions / observation_seconds, 4)
        avg_cycle_time_finished = (
            round(sum(self.finished_cycle_times) / len(self.finished_cycle_times), 1)
            if self.finished_cycle_times
            else 0.0
        )
        all_cycle_times = self.finished_cycle_times + self.scrapped_cycle_times
        avg_cycle_time_all = round(sum(all_cycle_times) / len(all_cycle_times), 1) if all_cycle_times else 0.0

        stages_out = self._stage_kpis(observation_seconds, observation_end_ts)
        utilization, state_probability, station_live = self._station_kpis(observation_end_ts)

        # Time shown on charts: the last event, else the first event, else now.
        if self.last_event_ts is not None:
            chart_time_unix = float(self.last_event_ts)
        elif self.observation_start_ts is not None:
            chart_time_unix = float(self.observation_start_ts)
        else:
            chart_time_unix = datetime.datetime.now().timestamp()
        sim_time_iso = datetime.datetime.fromtimestamp(chart_time_unix).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        system_block = {
            "num_completions": self.num_completions,
            "num_scraps": self.num_scraps,
            "wip_instantaneous": self.sys_wip,
            "wip_average": avg_system_wip,
            "complete_rate": completions_per_sec,
            "scrap_rate": scrap_fraction,
            "avg_cycle_time_fin": avg_cycle_time_finished,
            "avg_cycle_time_all": avg_cycle_time_all,
            "duplicate_start_count": self.duplicate_start_count,
        }

        trends = self._trend_series()

        return {
            "system": system_block,
            "stages": stages_out,
            **trends,
            # --- backward-compatible top-level keys (MQTT / UI / tests) ---
            # Several are aliases of the same value: throughput == complete_rate (completions/s);
            # current_wip == instantaneous_wip == wip_instantaneous; avg_wip == wip_average.
            # scrap_rate is the scrap fraction (see above); flow_time_count is the number of
            # finished cycle-time samples, avg_cycle_all_sample_count of finished + scrapped.
            "throughput": completions_per_sec,
            "complete_rate": completions_per_sec,
            "finished_count": self.num_completions,
            "scrap_count": self.num_scraps,
            "scrap_rate": scrap_fraction,
            "observation_time_sec": round(observation_seconds, 1),
            "avg_cycle_time_finished_sec": avg_cycle_time_finished,
            "avg_cycle_time_all_sec": avg_cycle_time_all,
            "flow_time_count": len(self.finished_cycle_times),
            "avg_cycle_all_sample_count": len(all_cycle_times),
            "current_wip": self.sys_wip,
            "instantaneous_wip": self.sys_wip,
            "wip_instantaneous": self.sys_wip,
            "avg_wip": avg_system_wip,
            "wip_average": avg_system_wip,
            "utilization": utilization,
            "state_probability": state_probability,
            "station_live": station_live,
            "simulation_time_iso": sim_time_iso,
            "chart_time_unix": chart_time_unix,
            "observation_start_ts": self.observation_start_ts,
            "yield_rate": round(self.num_completions / departed, 4) if departed > 0 else 0.0,
            "duplicate_start_count": self.duplicate_start_count,
            "debug": {
                "duplicate_start_count": self.duplicate_start_count,
                "stage_forced_exits": {
                    "stage{}".format(s): self.stage_forced_exits[s]
                    for s in range(1, 7)
                },
            } if _kpi_wip_debug_enabled() else {},
        }

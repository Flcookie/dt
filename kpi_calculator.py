# kpi_calculator.py — System / Stage / Station KPIs (streaming, event-driven)
#
# System: WIP = open “lap” jobs: corner2 START +1, splitter5 FINISH/SCRAP -1 (floor 0).
#   **At most one +1 per part** until that part FINISH/SCRAP: duplicate/half-open STARTs are ignored
#   so WIP is not 2× physical part count. corner2 RETURN/TRANSFER do not change WIP.
#   Debug: ``KPI_WIP_DEBUG=1`` prints WIP steps for corner2 / splitter5.
# Stage: entry on LOAD at anchor stations; exits per splitter TRANSFER rules and station TRANSFERs.
#   **One stage per open lap at a time**: sum of stage ``wip_instantaneous`` equals system WIP.
#   Parts between formal exit and next anchor LOAD sit in ``_part_transit_stage`` (still counted).
#   corner2 START assigns transit stage 1 until first anchor LOAD. Stage4: splitter3/4 one exit per
#   ``station41`` LOAD per part. Looping substations (station22 / station51 / station52) re-enter same stage.
# Station: BUSY / FAIL / BLOCKED / IDLE state machine; utilization = P_busy + P_fail.
# BLOCK @ station*: downstream blocked before UNLOAD → BLOCKED (not extended BUSY).
# splitter5 CHECKOUT: pre-notification before FINISH/SCRAP; ignored for KPI counts.
#
# Part id: part_id, partId, entity_id, entityId — see _extract_part_id.

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


def _wip_average_confirmed(
    history: list[tuple[float, int]], observation_time: float, end_ts: float
) -> float:
    """Time-weighted average WIP with tail carry to ``end_ts``."""
    if not history or observation_time <= 0:
        return 0.0
    total = 0.0
    for i in range(len(history) - 1):
        t0, w = history[i]
        t1, _ = history[i + 1]
        total += float(w) * max(0.0, t1 - t0)
    last_t, last_w = history[-1]
    total += float(last_w) * max(0.0, end_ts - last_t)
    return total / observation_time


# Trend chart: rolling yield among last N departures (FINISH/SCRAP), not lifetime cumulative.
TREND_ROLLING_DEPARTURES = 20


def _rolling_departure_rates(
    rate_history: list[tuple[float, int, int]],
    *,
    window: int = TREND_ROLLING_DEPARTURES,
) -> list[tuple[float, float, float]]:
    """(ts, completion_pct, scrap_pct) over the last ``window`` departures ending at each point."""
    if not rate_history or window <= 0:
        return []
    out: list[tuple[float, float, float]] = []
    for i in range(len(rate_history)):
        ts, nc, ns = rate_history[i]
        j = i
        dep_in_window = 0
        while j > 0 and dep_in_window < window:
            j -= 1
            dep_in_window += (rate_history[j + 1][1] - rate_history[j][1]) + (
                rate_history[j + 1][2] - rate_history[j][2]
            )
        c0, s0 = rate_history[j][1], rate_history[j][2]
        d_comp = int(nc) - int(c0)
        d_scrap = int(ns) - int(s0)
        departed = d_comp + d_scrap
        if departed <= 0:
            continue
        comp_pct = round(100.0 * d_comp / departed, 2)
        scrap_pct = round(100.0 * d_scrap / departed, 2)
        out.append((float(ts), comp_pct, scrap_pct))
    return out


def _rolling_departure_throughput_rates(
    rate_history: list[tuple[float, int, int]],
    *,
    window: int = TREND_ROLLING_DEPARTURES,
) -> list[tuple[float, float, float]]:
    """(ts, completions/s, scraps/s) at each departure; window = last N departures by count."""
    if not rate_history or window <= 0:
        return []
    dep_indices: list[int] = []
    for i in range(1, len(rate_history)):
        ts, nc, ns = rate_history[i]
        p_nc, p_ns = rate_history[i - 1][1], rate_history[i - 1][2]
        if int(nc) != int(p_nc) or int(ns) != int(p_ns):
            dep_indices.append(i)
    if not dep_indices:
        return []
    out: list[tuple[float, float, float]] = []
    for di in range(len(dep_indices)):
        idx = dep_indices[di]
        ts, nc, ns = rate_history[idx]
        j = di
        dep_in_window = 0
        while j > 0 and dep_in_window < window:
            j -= 1
            p_idx = dep_indices[j]
            n_idx = dep_indices[j + 1]
            dep_in_window += (rate_history[n_idx][1] - rate_history[p_idx][1]) + (
                rate_history[n_idx][2] - rate_history[p_idx][2]
            )
        if j == 0:
            base_idx = dep_indices[0] - 1
            if base_idx < 0:
                t0, c0, s0 = rate_history[0]
            else:
                t0, c0, s0 = rate_history[base_idx]
        else:
            t0, c0, s0 = rate_history[dep_indices[j]]
        dt = float(ts) - float(t0)
        if dt <= 0:
            continue
        out.append(
            (
                float(ts),
                round((int(nc) - int(c0)) / dt, 5),
                round((int(ns) - int(s0)) / dt, 5),
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
        self.sys_wip = 0
        self.sys_wip_history: list[tuple[float, int]] = []
        # (event_ts, completions, scraps) — rates derived when building trend series
        self.sys_rate_history: list[tuple[float, int, int]] = []
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
        self.stage_entry_time: dict[tuple[str, int], float] = {}
        self.last_splitter_mark: dict[tuple[str, str], str] = {}
        # Stage4: at most one exit (splitter3 or splitter4 FORWARD→TRANSFER) per station41 LOAD per part
        self._s4_pending_exits: dict[str, int] = {}
        # part_id -> stage 1..6 they are in (in-model); at most one stage per part
        self._part_current_stage: dict[str, int] = {}
        # open lap, exited anchor but not yet at next anchor (or pre-first-anchor after START)
        self._part_transit_stage: dict[str, int] = {}

        # --- Station (BUSY / FAIL / BLOCKED / IDLE) ---
        self._stn_state: dict[str, dict[str, Any]] = {}
        self._stn_acc: dict[str, dict[str, float]] = {}
        self._stn_touched: set[str] = set()
        self._distinct_part_ids: set[str] = set()
        self.fail_event_count = 0

        self._missing_part_warns_emitted = 0

    @property
    def current_wip(self) -> int:
        return self.sys_wip

    @property
    def finished_count(self) -> int:
        return self.num_completions

    def _warn_missing_part_id(self, event: dict) -> None:
        if self._missing_part_warn_cap <= 0:
            return
        if self._missing_part_warns_emitted >= self._missing_part_warn_cap:
            return
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

    def _append_stage_wip(self, stage: int, ts: float) -> None:
        self.stage_wip_hist[stage].append((ts, self.stage_wip[stage]))

    def _clear_part_stage_state(self, part_id: str) -> None:
        self._part_current_stage.pop(part_id, None)
        self._part_transit_stage.pop(part_id, None)
        self._s4_pending_exits.pop(part_id, None)
        for key in [k for k in self.stage_entry_time if k[0] == part_id]:
            del self.stage_entry_time[key]

    def _sync_stage_wip_at(self, ts: float) -> None:
        """Derive stage WIP from open laps so sum(stage) == system WIP."""
        counts = {s: 0 for s in range(1, 7)}
        for pid in self._open_lap:
            cur = self._part_current_stage.get(pid)
            if cur is not None:
                counts[cur] += 1
            elif pid in self._part_transit_stage:
                counts[self._part_transit_stage[pid]] += 1
            else:
                counts[1] += 1
        for stage in range(1, 7):
            if self.stage_wip[stage] != counts[stage]:
                self.stage_wip[stage] = counts[stage]
                self._append_stage_wip(stage, ts)

    def _assign_transit_after_exit(self, stage: int, part_id: str) -> None:
        if part_id not in self._open_lap:
            return
        if stage in (2, 4, 6):
            self._part_transit_stage[part_id] = stage
        else:
            self._part_transit_stage[part_id] = min(6, stage + 1)

    def _stage_force_leave_without_departure(self, stage: int, part_id: str, ts: float) -> None:
        """Keep stage WIP sane when a part appears in another stage without a formal exit event.

        This is a reconciliation path only: it must NOT increment departures/throughput or flow-time samples.
        """
        self.stage_forced_exits[stage] += 1
        key = (part_id, stage)
        if key in self.stage_entry_time:
            del self.stage_entry_time[key]
        if stage == 4 and self._s4_pending_exits.get(part_id, 0) > 0:
            self._s4_pending_exits[part_id] -= 1
        if part_id in self._part_current_stage and self._part_current_stage[part_id] == stage:
            del self._part_current_stage[part_id]

    def _stage_entry(self, stage: int, part_id: str, ts: float) -> None:
        if not part_id:
            return
        cur = self._part_current_stage.get(part_id)
        if cur == stage:
            if stage in (2, 4, 6):
                # Looping stage: repeated LOAD counts as a completed pass.
                self._stage_exit(stage, part_id, ts)
            else:
                return
        if cur is not None and cur != stage:
            self._stage_force_leave_without_departure(cur, part_id, ts)
        if part_id in self._part_current_stage and self._part_current_stage[part_id] != stage:
            del self._part_current_stage[part_id]
        self._part_transit_stage.pop(part_id, None)
        self._part_current_stage[part_id] = stage
        self.stage_entry_time[(part_id, stage)] = ts
        if stage == 4 and part_id:
            self._s4_pending_exits[part_id] = self._s4_pending_exits.get(part_id, 0) + 1

    def _stage4_exit_dedup(self, part_id: str, ts: float) -> None:
        """One Stage4 exit per `station41` LOAD: ignore extra FORWARD+TRANSFER on splitter3/4 same lap."""
        if not part_id:
            return
        if self._s4_pending_exits.get(part_id, 0) <= 0:
            return
        self._s4_pending_exits[part_id] -= 1
        self._stage_exit(4, part_id, ts)

    def _stage_exit(self, stage: int, part_id: str, ts: float) -> None:
        if not part_id:
            return
        key = (part_id, stage)
        if key in self.stage_entry_time:
            self.stage_flow_times[stage].append(ts - self.stage_entry_time[key])
            del self.stage_entry_time[key]
            self.stage_departures[stage] += 1
        if part_id in self._part_current_stage and self._part_current_stage[part_id] == stage:
            del self._part_current_stage[part_id]
        self._assign_transit_after_exit(stage, part_id)

    def _stn_ensure(self, sid: str, ts: float) -> None:
        if sid not in self._stn_state:
            self._stn_state[sid] = {
                "state": "IDLE",
                "start": ts,
                "part": "",
            }
            self._stn_acc[sid] = {
                "BUSY": 0.0,
                "FAIL": 0.0,
                "BLOCKED": 0.0,
                "IDLE": 0.0,
            }

    def _stn_elapse(self, sid: str, ts: float) -> None:
        st = self._stn_state[sid]
        dt = max(0.0, ts - float(st["start"]))
        self._stn_acc[sid][str(st["state"])] += dt
        st["start"] = ts

    def _stn_set_state(self, sid: str, new_state: str, ts: float) -> None:
        self._stn_ensure(sid, ts)
        self._stn_elapse(sid, ts)
        self._stn_state[sid]["state"] = new_state

    def on_event(self, event: dict) -> None:
        time_str = event.get("time")
        if not time_str:
            return
        ts = _parse_ts(str(time_str))
        comp = str(event.get("component_id", "") or "").strip()
        part_id = _extract_part_id(event)
        act_u = str(event.get("activity", "") or "").strip().upper()

        if not part_id and act_u in (
            "START",
            "FINISH",
            "LOAD",
            "UNLOAD",
            "TRANSFER",
            "SCRAP",
            "FAIL",
        ):
            self._warn_missing_part_id(event)

        if self.observation_start_ts is None:
            self.observation_start_ts = ts
        self.last_event_ts = ts

        if part_id:
            self._distinct_part_ids.add(part_id)

        # -------- System WIP (only these branches mutate sys_wip) --------
        if _kpi_wip_debug_enabled() and comp in ("corner2", "splitter5"):
            print(
                f"[WIP] {comp} {act_u} | wip before={self.sys_wip}",
                flush=True,
            )

        if comp == "corner2" and act_u == "START" and part_id:
            if part_id in self._open_lap:
                self.duplicate_start_count += 1
                if _kpi_wip_debug_enabled():
                    print(
                        f"[KPI WIP] skip duplicate corner2 START {part_id!r} (lap still open), "
                        f"sys_wip={self.sys_wip}",
                        flush=True,
                    )
            else:
                self._open_lap.add(part_id)
                self._part_transit_stage[part_id] = 1
                w0 = self.sys_wip
                self.sys_wip += 1
                if _kpi_wip_debug_enabled():
                    print(
                        f"[KPI WIP+1] (comp,act)=({comp!r},{act_u!r}) part_id={part_id!r} "
                        f"wip {w0}->{self.sys_wip}",
                        flush=True,
                    )
                self._append_sys_wip(ts)
                self.sys_start_time[part_id] = ts

        if comp == "splitter5" and act_u == "CHECKOUT":
            pass  # hardware pre-signal; FINISH/SCRAP follow immediately

        if comp == "splitter5" and act_u in self._finish_upper and part_id:
            had = part_id in self._open_lap
            w_before = self.sys_wip
            if had:
                self._open_lap.discard(part_id)
                self._clear_part_stage_state(part_id)
                self.sys_wip = max(0, self.sys_wip - 1)
                self.num_completions += 1
                self._append_sys_wip(ts)
                st = self.sys_start_time.get(part_id)
                if st is not None:
                    self.finished_cycle_times.append(ts - st)
            if _kpi_wip_debug_enabled():
                print(
                    f"[KPI WIP-1 FINISH] part_id={part_id!r} had_open={had} "
                    f"wip {w_before}->{self.sys_wip} act={act_u!r}",
                    flush=True,
                )

        if comp == "splitter5" and act_u in self._scrap_upper and part_id:
            had = part_id in self._open_lap
            w_before = self.sys_wip
            if had:
                self._open_lap.discard(part_id)
                self._clear_part_stage_state(part_id)
                self.sys_wip = max(0, self.sys_wip - 1)
                self.num_scraps += 1
                self._append_sys_wip(ts)
                st = self.sys_start_time.get(part_id)
                if st is not None:
                    self.scrapped_cycle_times.append(ts - st)
            if _kpi_wip_debug_enabled():
                print(
                    f"[KPI WIP-1 SCRAP] part_id={part_id!r} had_open={had} "
                    f"wip {w_before}->{self.sys_wip} act={act_u!r}",
                    flush=True,
                )

        # -------- Stage entry (LOAD @ anchors) --------
        if act_u == "LOAD" and comp in STAGE_ENTRY and part_id:
            self._stage_entry(STAGE_ENTRY[comp], part_id, ts)

        # -------- Stage exits --------
        if comp == "station11" and act_u == "TRANSFER" and part_id:
            self._stage_exit(1, part_id, ts)

        if comp == "splitter1" and act_u in _SPLITTER_MARK_ACTS and part_id:
            self.last_splitter_mark[("splitter1", part_id)] = act_u
        if comp == "splitter1" and act_u == "TRANSFER" and part_id:
            if self.last_splitter_mark.get(("splitter1", part_id)) == "FORWARD":
                self._stage_exit(2, part_id, ts)

        if comp == "station31" and act_u == "TRANSFER" and part_id:
            self._stage_exit(3, part_id, ts)

        if comp == "splitter3" and act_u in _SPLITTER_MARK_ACTS and part_id:
            self.last_splitter_mark[("splitter3", part_id)] = act_u
        if comp == "splitter4" and act_u in _SPLITTER_MARK_ACTS and part_id:
            self.last_splitter_mark[("splitter4", part_id)] = act_u
        if comp == "splitter3" and act_u == "TRANSFER" and part_id:
            if self.last_splitter_mark.get(("splitter3", part_id)) == "FORWARD":
                self._stage4_exit_dedup(part_id, ts)
        if comp == "splitter4" and act_u == "TRANSFER" and part_id:
            if self.last_splitter_mark.get(("splitter4", part_id)) == "FORWARD":
                self._stage4_exit_dedup(part_id, ts)

        if comp == "station61" and act_u == "TRANSFER" and part_id:
            self._stage_exit(5, part_id, ts)

        if comp == "corner1" and act_u == "TRANSFER" and part_id:
            self._stage_exit(6, part_id, ts)

        # -------- Station BUSY / FAIL / BLOCKED / IDLE --------
        if comp in STATION_KPI_IDS:
            if act_u == "FAIL":
                self.fail_event_count += 1
                self._stn_touched.add(comp)
                self._stn_ensure(comp, ts)
                if self._stn_state[comp]["state"] == "BUSY":
                    self._stn_set_state(comp, "FAIL", ts)
            elif act_u == "LOAD":
                self._stn_touched.add(comp)
                self._stn_set_state(comp, "BUSY", ts)
                if part_id:
                    self._stn_state[comp]["part"] = part_id
            elif act_u == "UNLOAD":
                self._stn_touched.add(comp)
                self._stn_ensure(comp, ts)
                if self._stn_state[comp]["state"] in ("BUSY", "FAIL"):
                    self._stn_set_state(comp, "BLOCKED", ts)
            elif act_u == "BLOCK":
                self._stn_touched.add(comp)
                self._stn_ensure(comp, ts)
                if self._stn_state[comp]["state"] in ("BUSY", "FAIL"):
                    self._stn_set_state(comp, "BLOCKED", ts)
            elif act_u == "TRANSFER":
                self._stn_touched.add(comp)
                self._stn_ensure(comp, ts)
                if self._stn_state[comp]["state"] == "BLOCKED":
                    self._stn_set_state(comp, "IDLE", ts)
                    self._stn_state[comp]["part"] = ""
            elif act_u == "PASS" and part_id:
                self._stn_touched.add(comp)
                self._stn_ensure(comp, ts)
                self._stn_state[comp]["part"] = part_id

        self._sync_stage_wip_at(ts)

    def get_snapshot(self) -> dict[str, Any]:
        if self.observation_time_mode == "replay":
            end_ts = float(self.last_event_ts) if self.last_event_ts is not None else datetime.datetime.now().timestamp()
        else:
            now = datetime.datetime.now().timestamp()
            last = float(self.last_event_ts) if self.last_event_ts is not None else now
            end_ts = last if (now - last) > 30.0 else now

        start_ts = self.observation_start_ts
        obs_time = max(0.001, (end_ts - start_ts) if start_ts is not None else 0.001)

        if self.last_event_ts is not None:
            self._sync_stage_wip_at(float(self.last_event_ts))

        avg_sys_wip = round(_wip_average_confirmed(self.sys_wip_history, obs_time, end_ts), 3)

        departed = self.num_completions + self.num_scraps
        scrap_rate_kpi = round(self.num_scraps / departed, 3) if departed > 0 else 0.0
        complete_rate = round(self.num_completions / obs_time, 4)

        avg_ct_fin = (
            round(sum(self.finished_cycle_times) / len(self.finished_cycle_times), 1)
            if self.finished_cycle_times
            else 0.0
        )
        all_ct = self.finished_cycle_times + self.scrapped_cycle_times
        avg_ct_all = round(sum(all_ct) / len(all_ct), 1) if all_ct else 0.0

        stages_out: dict[str, dict[str, Any]] = {}
        for s in range(1, 7):
            hist = self.stage_wip_hist[s]
            avg_w = round(_wip_average_confirmed(hist, obs_time, end_ts), 3)
            dep_n = self.stage_departures[s]
            ft_list = self.stage_flow_times[s]
            avg_ft = round(sum(ft_list) / len(ft_list), 1) if ft_list else 0.0
            thr = round(dep_n / obs_time, 4)
            stages_out["stage{}".format(s)] = {
                "wip_instantaneous": self.stage_wip[s],
                "wip_average": avg_w,
                "num_departures": dep_n,
                "throughput": thr,
                "avg_flow_time": avg_ft,
            }

        utilization: dict[str, float] = {}
        state_probability: dict[str, dict[str, float]] = {}
        station_live: dict[str, dict[str, str]] = {}

        for sid in sorted(STATION_KPI_IDS):
            if sid not in self._stn_touched or sid not in self._stn_state:
                utilization[sid] = 0.0
                state_probability[sid] = {
                    "busy": 0.0,
                    "fail": 0.0,
                    "blocked": 0.0,
                    "idle": 1.0,
                }
                station_live[sid] = {
                    "current_state": "IDLE",
                    "current_part_id": "",
                    "queue_hint": "",
                }
                continue

            st = self._stn_state[sid]
            acc = dict(self._stn_acc[sid])
            cur = str(st["state"])
            tail = max(0.0, end_ts - float(st["start"]))
            acc[cur] = acc.get(cur, 0.0) + tail

            total_st = (
                acc.get("BUSY", 0.0)
                + acc.get("FAIL", 0.0)
                + acc.get("BLOCKED", 0.0)
                + acc.get("IDLE", 0.0)
            )
            if total_st < 1e-9:
                p_busy = p_fail = p_blocked = 0.0
                p_idle = 1.0
            else:
                p_busy = acc.get("BUSY", 0.0) / total_st
                p_fail = acc.get("FAIL", 0.0) / total_st
                p_blocked = acc.get("BLOCKED", 0.0) / total_st
                p_idle = acc.get("IDLE", 0.0) / total_st

            utilization[sid] = round(max(0.0, min(1.0, p_busy + p_fail)), 4)
            state_probability[sid] = {
                "busy": round(p_busy, 4),
                "fail": round(p_fail, 4),
                "blocked": round(p_blocked, 4),
                "idle": round(p_idle, 4),
            }
            station_live[sid] = {
                "current_state": cur,
                "current_part_id": str(st.get("part") or "").strip(),
                "queue_hint": "",
            }

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
            "wip_average": avg_sys_wip,
            "complete_rate": complete_rate,
            "scrap_rate": scrap_rate_kpi,
            "avg_cycle_time_fin": avg_ct_fin,
            "avg_cycle_time_all": avg_ct_all,
            "duplicate_start_count": self.duplicate_start_count,
        }

        _wip_trend = self.sys_wip_history[-200:]
        trend_sys_wip_history: list[list[float | int]] = [
            [float(ts), int(w)] for ts, w in _wip_trend
        ]
        trend_rate_history: list[list[float]] = [
            [ts, comp_pct, scrap_pct]
            for ts, comp_pct, scrap_pct in _rolling_departure_rates(
                self.sys_rate_history[-200:]
            )
        ]
        trend_departure_history: list[list[float | int]] = [
            [float(ts), int(nc), int(ns)]
            for ts, nc, ns in self.sys_rate_history[-200:]
        ]
        trend_throughput_rates: list[list[float]] = [
            [ts, comp_r, scrap_r]
            for ts, comp_r, scrap_r in _rolling_departure_throughput_rates(
                self.sys_rate_history[-200:]
            )
        ]
        _fct_cap = self.finished_cycle_times[-1000:]
        trend_finished_cycle_times: list[float] = [round(float(x), 4) for x in _fct_cap]

        return {
            "system": system_block,
            "stages": stages_out,
            "trend_sys_wip_history": trend_sys_wip_history,
            "trend_rate_history": trend_rate_history,
            "trend_departure_history": trend_departure_history,
            "trend_throughput_rates": trend_throughput_rates,
            "trend_finished_cycle_times": trend_finished_cycle_times,
            # --- backward-compatible top-level keys (MQTT / tests) ---
            "throughput": complete_rate,
            "complete_rate": complete_rate,
            "finished_count": self.num_completions,
            "scrap_count": self.num_scraps,
            "scrap_rate": scrap_rate_kpi,
            "observation_time_sec": round(obs_time, 1),
            "avg_cycle_time_finished_sec": avg_ct_fin,
            "avg_cycle_time_all_sec": avg_ct_all,
            "flow_time_count": len(self.finished_cycle_times),
            "avg_cycle_all_sample_count": len(all_ct),
            "current_wip": self.sys_wip,
            "instantaneous_wip": self.sys_wip,
            "wip_instantaneous": self.sys_wip,
            "avg_wip": avg_sys_wip,
            "wip_average": avg_sys_wip,
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

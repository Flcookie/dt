"""Flow conformance engine — pure port of ``code/flow.py`` step logic for replay."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

ACTIVE_ACTIVITY = "PROCESS"
N_STEPS = 9

FLOW_STEP_LABELS: tuple[str, ...] = (
    "S11",
    "S21/S22",
    "S31",
    "S41 (1st)",
    "S51/S52 (1st)",
    "S41 (2nd)",
    "S51/S52 (2nd)",
    "S61",
    "S71",
)

# Digital Twin Part Trace 表头 — 与 ``code/flow.py`` FLOW_STEPS 逐步一一对应（9 列）。
FLOW_STEP_HEADERS_M: tuple[str, ...] = (
    "M1-1",
    "M2-1·M2-2",
    "M3-1",
    "M4-1 1st",
    "M5-1·M5-2 1st",
    "M4-1 2nd",
    "M5-1·M5-2 2nd",
    "M6-1",
    "M7-1",
)

SLOT_TO_STEP_INDICES: dict[str, tuple[int, ...]] = {
    "st11": (0,),
    "st21_22": (1,),
    "st31": (2,),
    "st41": (3, 5),
    "st51_52": (4, 6),
    "st61": (7,),
    "st71": (8,),
}

STATION_TO_SLOT: dict[str, str] = {
    "station11": "st11",
    "station21": "st21_22",
    "station22": "st21_22",
    "station31": "st31",
    "station41": "st41",
    "station51": "st51_52",
    "station52": "st51_52",
    "station61": "st61",
    "station71": "st71",
}

# FAIL @ station → flow step index(es); rework marks only these steps (log evidence).
FAIL_STATION_TO_STEP: dict[str, tuple[int, ...]] = {
    "station11": (0,),
    "station21": (1,),
    "station22": (1,),
    "station31": (2,),
    "station41": (3, 5),
    "station51": (4, 6),
    "station52": (4, 6),
    "station61": (7,),
    "station71": (8,),
}


@dataclass
class FlowState:
    reached: list[bool] = field(default_factory=lambda: [False] * N_STEPS)
    reworked: list[bool] = field(default_factory=lambda: [False] * N_STEPS)
    n_rework: list[int] = field(default_factory=lambda: [1] * N_STEPS)
    anomaly: bool = False
    is_scrapped: bool = False
    is_finished: bool = False
    in_qc: bool = False
    after_qc: bool = False
    next_expected: int = 0
    previous_step: int = 0
    pre_qc_expected_step: int = 0
    pre_qc_previous_step: int = 0
    failed_stations: set[str] = field(default_factory=set)
    anomalies: list[dict[str, str]] = field(default_factory=list)

    def copy(self) -> FlowState:
        return FlowState(
            reached=list(self.reached),
            reworked=list(self.reworked),
            n_rework=list(self.n_rework),
            anomaly=self.anomaly,
            is_scrapped=self.is_scrapped,
            is_finished=self.is_finished,
            in_qc=self.in_qc,
            after_qc=self.after_qc,
            next_expected=self.next_expected,
            previous_step=self.previous_step,
            pre_qc_expected_step=self.pre_qc_expected_step,
            pre_qc_previous_step=self.pre_qc_previous_step,
            failed_stations=set(self.failed_stations),
            anomalies=list(self.anomalies),
        )


def new_flow_state() -> FlowState:
    return FlowState()


def _norm_cid(component_id: str) -> str:
    return str(component_id or "").strip().lower()


def _match_step(comp: str, act: str, state: FlowState) -> int:
    if act != ACTIVE_ACTIVITY:
        return -1
    if comp == "station11":
        return 0
    if comp in ("station21", "station22"):
        return 1
    if comp == "station31":
        return 2
    if comp == "station41":
        exp = state.pre_qc_expected_step if state.after_qc else state.next_expected
        prev = state.pre_qc_previous_step if state.after_qc else state.previous_step
        if exp == 5 or prev == 4:
            return 5
        if exp == 3 or prev == 2:
            return 3
        return 5 if state.reached[5] else 3
    if comp in ("station51", "station52"):
        exp = state.pre_qc_expected_step if state.after_qc else state.next_expected
        prev = state.pre_qc_previous_step if state.after_qc else state.previous_step
        if exp == 6 or prev == 5:
            return 6
        if exp == 4 or prev == 3:
            return 4
        return 6 if state.reached[6] else 4
    if comp == "station61":
        return 7
    if comp == "station71":
        return 8
    return -1


def _record_anomaly(
    state: FlowState,
    ev: dict,
    *,
    matched_step: int,
    is_forward_jump: bool,
    is_backward_jump: bool,
    is_repetition: bool,
) -> None:
    state.anomaly = True
    time_str = str(ev.get("time") or "").strip()
    if len(time_str) > 19:
        time_str = time_str[:19]
    exp_label = (
        FLOW_STEP_LABELS[state.next_expected]
        if state.next_expected < N_STEPS
        else "End of flow"
    )
    match_label = FLOW_STEP_LABELS[matched_step]
    if is_forward_jump:
        detail = "Station skipped: expected {}, detected {}.".format(
            exp_label, match_label
        )
    elif is_backward_jump:
        detail = "Unauthorized return: expected {}, detected {}.".format(
            exp_label, match_label
        )
    elif is_repetition:
        detail = "Unauthorized repetition: expected {}, detected {}.".format(
            exp_label, match_label
        )
    else:
        detail = "Anomaly: expected {}, detected {}.".format(exp_label, match_label)
    state.anomalies.append({"time": time_str, "detail": detail})


def apply_flow_event(state: FlowState, ev: dict) -> None:
    """Apply one MQTT-style event to flow state (``code/flow.py`` semantics)."""
    comp = _norm_cid(str(ev.get("component_id") or ""))
    act = str(ev.get("activity") or "").strip().upper()

    if comp == "splitter5" and act == "SCRAP":
        state.is_scrapped = True
        return

    if comp == "splitter5" and act == "FINISH":
        state.is_finished = True
        return

    if comp == "station71" and act == "UNLOAD":
        state.after_qc = True

    if act == "FAIL" and comp.startswith("station"):
        state.failed_stations.add(comp)

    if state.is_scrapped or act != ACTIVE_ACTIVITY:
        return

    matched_step = _match_step(comp, act, state)
    if matched_step == -1:
        return

    was_reworked_before = list(state.reworked)

    if state.after_qc:
        if state.reached[matched_step]:
            if was_reworked_before[matched_step]:
                state.n_rework[matched_step] += 1
            state.reworked[matched_step] = True
        else:
            state.reached[matched_step] = True
        state.after_qc = False
        state.previous_step = matched_step
        state.next_expected = matched_step + 1

    elif matched_step == state.next_expected:
        state.reached[matched_step] = True
        state.previous_step = matched_step
        state.next_expected = matched_step + 1

    else:
        is_forward_jump = matched_step > state.next_expected
        is_backward_jump = matched_step < state.previous_step
        is_repetition = matched_step == state.previous_step

        if matched_step == 8 and (is_forward_jump or is_backward_jump):
            if not state.in_qc:
                state.pre_qc_expected_step = state.next_expected
                state.pre_qc_previous_step = state.previous_step
        else:
            _record_anomaly(
                state,
                ev,
                matched_step=matched_step,
                is_forward_jump=is_forward_jump,
                is_backward_jump=is_backward_jump,
                is_repetition=is_repetition,
            )

        state.reached[matched_step] = True
        state.previous_step = matched_step
        state.next_expected = matched_step + 1

    if comp == "station71" and act == ACTIVE_ACTIVITY:
        state.in_qc = True


def apply_fail_rework_to_flow(state: FlowState) -> None:
    """At lap FINISH: mark rework only on steps tied to stations with FAIL in the log."""
    for st in state.failed_stations:
        for idx in FAIL_STATION_TO_STEP.get(st, ()):
            if state.reached[idx]:
                state.reworked[idx] = True


def flow_state_to_display_grid(state: FlowState) -> dict[str, str]:
    """Map 9-step flow state to 7-slot Digital Twin grid.

    SCRAP is recorded at splitter5 (not a matrix column); reached slots stay DONE/REWORK.
    """
    out: dict[str, str] = {}
    for slot, indices in SLOT_TO_STEP_INDICES.items():
        if slot == "st71" and state.in_qc and state.reached[8]:
            out[slot] = "QC"
            continue
        if any(state.reworked[i] for i in indices):
            out[slot] = "REWORK"
            continue
        if all(state.reached[i] for i in indices):
            out[slot] = "DONE"
        elif any(state.reached[i] for i in indices):
            out[slot] = "DONE"
        else:
            out[slot] = "NOT_DONE"
    return out


def flow_state_to_step_display_grid(state: FlowState) -> dict[int, str]:
    """9-step grid — one column per ``code/flow.py`` FLOW_STEPS row."""
    out: dict[int, str] = {}
    for step_i in range(N_STEPS):
        reached = state.reached[step_i]
        reworked = state.reworked[step_i]
        if state.is_scrapped and step_i == 8:
            out[step_i] = "SCRAP"
        elif state.is_scrapped:
            out[step_i] = "NOT_DONE"
        elif step_i == 8 and state.in_qc and reached:
            out[step_i] = "QC"
        elif reworked:
            out[step_i] = "REWORK"
        elif reached:
            out[step_i] = "DONE"
        else:
            out[step_i] = "NOT_DONE"
    return out


def flow_state_snapshot(state: FlowState) -> dict[str, Any]:
    return {
        "reached": list(state.reached),
        "reworked": list(state.reworked),
        "n_rework": list(state.n_rework),
        "anomaly": state.anomaly,
        "is_scrapped": state.is_scrapped,
        "is_finished": state.is_finished,
        "in_qc": state.in_qc,
        "anomalies": copy.deepcopy(state.anomalies),
        "failed_stations": sorted(state.failed_stations),
    }

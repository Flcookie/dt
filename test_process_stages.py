# test_process_stages.py — mainline process-stage model (part_track.process_stages) and the
# twin.twin_layout compatibility re-export. Expected values were taken from the model before
# it moved out of twin/twin_layout.py; they must not change.

import ast
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "streamlit_app")
for _p in (_APP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import part_track.process_stages as process_stages  # noqa: E402
import twin.twin_layout as twin_layout  # noqa: E402

NAMES = (
    "LOGGED_COMPONENT_IDS",
    "PROCESS_STAGE_ORDER",
    "STAGE_SEQUENCE_ACTIVITIES",
    "_STATION_TO_STAGE",
    "component_to_process_stage",
    "stage_entry_sequence",
    "stage_entry_sequence_clean",
)


def test_twin_layout_reexports_the_same_objects():
    for name in NAMES:
        assert getattr(twin_layout, name) is getattr(process_stages, name), name
    assert sorted(twin_layout.__all__) == sorted(NAMES)


def test_stage_order_and_component_mapping():
    assert process_stages.PROCESS_STAGE_ORDER == ("ST11", "ST21/22", "ST31", "ST41", "ST51", "ST61", "ST71")
    assert process_stages.STAGE_SEQUENCE_ACTIVITIES == frozenset({"PROCESS", "PASS"})
    got = {c: process_stages.component_to_process_stage(c) for c in sorted(process_stages.LOGGED_COMPONENT_IDS)}
    assert got == {
        "corner1": None, "corner2": None,
        "splitter1": None, "splitter2": None, "splitter3": None, "splitter4": None, "splitter5": None,
        "station11": "ST11", "station21": "ST21/22", "station22": "ST21/22", "station31": "ST31",
        "station41": "ST41", "station51": "ST51", "station52": "ST51", "station61": "ST61",
        "station71": "ST71",
    }
    assert process_stages.component_to_process_stage(" station22 ") == "ST21/22"
    assert process_stages.component_to_process_stage(None) is None
    assert process_stages.component_to_process_stage("Station11") is None


def test_stage_entry_sequences():
    steps = [
        {"component_id": c, "activity": a}
        for c, a in [
            ("corner2", "START"), ("station11", "LOAD"), ("station11", "PROCESS"), ("station11", "TRANSFER"),
            ("splitter1", "PASS"), ("station21", "PASS"), ("station21", "TRANSFER"), ("station22", "LOAD"),
            ("station31", "PROCESS"), ("station41", "process"), ("station51", "PROCESS"), ("station41", "PASS"),
            ("station52", "FAIL"), ("station61", "UNLOAD"), ("station71", " pass "),
        ]
    ]
    assert process_stages.stage_entry_sequence(steps) == [
        "ST11", "ST21/22", "ST31", "ST41", "ST51", "ST41", "ST51", "ST61", "ST71",
    ]
    assert process_stages.stage_entry_sequence_clean(steps) == [
        "ST11", "ST21/22", "ST31", "ST41", "ST51", "ST41", "ST71",
    ]


def test_part_track_does_not_import_twin():
    pkg = os.path.join(_APP, "part_track")
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(pkg, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            assert not any(m == "twin" or m.startswith("twin.") for m in mods), (fn, mods)

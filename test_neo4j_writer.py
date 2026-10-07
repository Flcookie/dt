# test_neo4j_writer.py — what neo4j_writer.write_events_batch sends to Neo4j: the Event fields
# derived from an event, the DF / DF_PROCESS / NEXT chains across batches, and the module's
# chain state after a failed write. The neo4j package is replaced by a fake driver that records
# every tx.run (query text + parameters); no database is needed.
#
# Not asserted here (open questions in neo4j_writer.py): what a driver retry of the same
# transaction writes, since the copies it advances keep the failed attempt's updates.

import datetime
import importlib.util
import json
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))


class _FakeDb:
    def __init__(self):
        self.runs = []  # (query, params) of every tx.run, in order
        self.sessions = 0
        self.fail_on_event_id = None  # raise when this Event is created

    def driver(self, uri, auth=None):
        db = self

        class Tx:
            def run(self, query, **params):
                db.runs.append((query, params))
                if params.get("event_id") is not None and params["event_id"] == db.fail_on_event_id:
                    raise RuntimeError("write failed")

        class Session:
            def __enter__(self):
                db.sessions += 1
                return self

            def __exit__(self, *exc):
                return False

            def execute_write(self, fn, *args):
                return fn(Tx(), *args)

        return types.SimpleNamespace(session=Session, close=lambda: None)


@pytest.fixture
def writer(monkeypatch, tmp_path):
    """neo4j_writer loaded against the fake driver; Event ids are e1, e2, ... in write order."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"neo4j": {"uri": "bolt://fake", "username": "u", "password": "p"}}))
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    db = _FakeDb()
    monkeypatch.setitem(sys.modules, "neo4j", types.SimpleNamespace(GraphDatabase=db))
    spec = importlib.util.spec_from_file_location("neo4j_writer_under_test", os.path.join(_ROOT, "neo4j_writer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ids = iter("e%d" % i for i in range(1, 100))
    mod.uuid = types.SimpleNamespace(uuid4=lambda: next(ids))
    mod.db = db
    mod.start_session("s1", start_time_iso="2026-03-12T18:00:00")
    db.runs.clear()
    return mod


def _ev(second, component, part, activity):
    return {"time": "2026-03-12T18:00:%02d" % second, "component_id": component, "part_id": part, "activity": activity}


_LINKS = {
    "MERGE (e1)-[:DF]->(e2)": "DF",
    "MERGE (e1)-[:DF_PROCESS]->(e2)": "DF_PROCESS",
    "MERGE (e1)-[:NEXT]->(e2)": "NEXT",
    "MERGE (s1)-[:DF]->(s2)": "Station DF",
    "MERGE (s1)-[:DF_PROCESS]->(s2)": "Station DF_PROCESS",
    "MERGE (en1)-[:DF]->(en2)": "Entity DF",
}


def _writes(db):
    """Each tx.run as ("Event", id) or (link, from id, to id), in order."""
    out = []
    for query, params in db.runs:
        if "CREATE (e:Event" in query:
            out.append(("Event", params["event_id"]))
        else:
            (link,) = [name for text, name in _LINKS.items() if text in query]
            out.append((link, params["p"], params["c"]))
    return out


def _event_params(db):
    return [params for query, params in db.runs if "CREATE (e:Event" in query]


def test_event_fields_for_physical_and_converted_events(writer):
    writer.write_events_batch([
        {"time": " 2026-03-12T18:00:01 ", "component_id": " station11 ", "part_id": " p1 ", "activity": " Load "},
        {"time": "", "component_id": "station11", "part_id": "skipped", "activity": "LOAD"},
        {"timestamp": 1000.0, "time": " given ", "component_id": " station21 ", "part_id": "p2",
         "part_type": "gear", "activity": "PROCESS"},
    ])
    first, converted = _event_params(writer.db)
    assert first == {
        "event_id": "e1", "session_id": "s1",
        "timestamp": datetime.datetime(2026, 3, 12, 18, 0, 1).timestamp(),
        "time_str": "2026-03-12T18:00:01", "station_id": "station11", "part_id": "p1",
        "part_type": "part", "activity": "Load",  # stripped, case kept
    }
    assert converted == {
        "event_id": "e2", "session_id": "s1", "timestamp": 1000.0, "time_str": "given",
        "station_id": "station21", "part_id": "p2", "part_type": "gear", "activity": "PROCESS",
    }


def test_chains_continue_across_batches(writer):
    writer.write_events_batch([_ev(1, "corner2", "p1", "START"), _ev(2, "corner2", "p2", "START")])
    writer.write_events_batch([_ev(3, "station11", "p1", "LOAD")])
    assert _writes(writer.db) == [
        ("Event", "e1"),
        ("Event", "e2"), ("NEXT", "e1", "e2"), ("Entity DF", "e1", "e2"),
        ("Event", "e3"),
        ("DF", "e1", "e3"), ("Station DF", "e1", "e3"),
        ("DF_PROCESS", "e1", "e3"), ("Station DF_PROCESS", "e1", "e3"),
        ("NEXT", "e2", "e3"), ("Entity DF", "e2", "e3"),
    ]
    assert writer._last_event_per_part == writer._last_process_event_per_part == {"p1": "e3", "p2": "e2"}
    assert writer._last_global_event_id == "e3"


@pytest.mark.parametrize("process_mining, expected_process_links", [
    ({}, [("DF_PROCESS", "e1", "e3")]),  # default activities: TRANSFER is skipped
    ({"df_process_activities": [" transfer "]}, []),  # only TRANSFER events: e2 starts the chain
    ({"enable_df_process_on_write": False}, []),
])
def test_process_chain_skips_activities_not_listed(writer, process_mining, expected_process_links):
    writer._config["process_mining"] = dict(process_mining, enable_station_df_on_write=False,
                                            enable_entity_df_on_write=False)
    writer.write_events_batch([_ev(1, "station11", "p1", "LOAD"), _ev(2, "station11", "p1", "TRANSFER"),
                               _ev(3, "station21", "p1", "PROCESS")])
    writes = _writes(writer.db)
    assert [w for w in writes if w[0] == "DF"] == [("DF", "e1", "e2"), ("DF", "e2", "e3")]
    assert [w for w in writes if w[0] == "DF_PROCESS"] == expected_process_links
    assert not [w for w in writes if w[0].startswith(("Station", "Entity"))]


def test_nothing_is_written_for_an_empty_batch_or_without_session(writer):
    sessions = writer.db.sessions
    writer.write_events_batch([])
    writer.write_events_batch([{"time": "", "part_id": "p1"}])  # every event skipped
    assert (writer.db.sessions, writer.db.runs) == (sessions, [])
    writer.clear_all_events()  # deletes the events and forgets the current session
    sessions, runs = writer.db.sessions, len(writer.db.runs)
    writer.write_events_batch([_ev(1, "corner2", "p1", "START")])
    assert (writer.db.sessions, len(writer.db.runs)) == (sessions, runs)


def test_failed_write_leaves_the_module_chain_state_unchanged(writer):
    writer.write_events_batch([_ev(1, "station11", "p1", "LOAD")])
    writer.db.fail_on_event_id = "e3"
    with pytest.raises(RuntimeError):
        writer.write_events_batch([_ev(2, "station11", "p1", "PROCESS"), _ev(3, "station11", "p1", "UNLOAD")])
    assert writer._last_event_per_part == writer._last_process_event_per_part == {"p1": "e1"}
    assert writer._last_global_event_id == "e1"
    writer.db.runs.clear()
    writer.write_events_batch([_ev(4, "station11", "p1", "UNLOAD")])
    assert [w for w in _writes(writer.db) if w[0] in ("DF", "DF_PROCESS", "NEXT")] == [
        ("DF", "e1", "e4"), ("DF_PROCESS", "e1", "e4"), ("NEXT", "e1", "e4"),
    ]

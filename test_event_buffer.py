# test_event_buffer.py — EventBuffer ordering and flushing, in particular events that share a
# timestamp. They used to raise TypeError (the buffer compared the event dicts to break the tie)
# and the second event was lost. Ties are now kept in the order the events entered the buffer.

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import event_buffer  # noqa: E402


def _ev(t, part, act="PROCESS"):
    return {"time": "2026-05-09T10:00:" + t, "component_id": "station11", "part_id": part, "activity": act}


def _parts(events):
    return [e["part_id"] for e in events]


def test_same_timestamp_events_flush_in_arrival_order():
    buf = event_buffer.EventBuffer(window_ms=500)
    a, b, c = _ev("00.000", "a"), _ev("00.000", "b"), _ev("00.000", "c")
    assert buf.add_and_flush(a) == ([], 0)
    assert buf.add_and_flush(b) == ([], 0)
    assert buf.add_and_flush(c) == ([], 0)
    assert buf.size == 3
    ready, forced = buf.add_and_flush(_ev("01.000", "d"))
    assert forced == 0
    assert ready == [a, b, c] and all(x is y for x, y in zip(ready, [a, b, c]))  # same, unchanged objects
    assert buf.size == 1


def test_ties_and_late_events_keep_timestamp_order_then_arrival_order():
    buf = event_buffer.EventBuffer(window_ms=500)
    arrivals = [_ev("01.000", "x"), _ev("00.500", "late_y"), _ev("01.000", "z"), _ev("00.500", "late_w"),
                _ev("01", "same_ts_other_format")]  # "01" parses to the same instant as "01.000"
    for ev in arrivals:
        assert buf.add_and_flush(ev) == ([], 0)
    ready, _ = buf.add_and_flush(_ev("05.000", "flush"))
    assert _parts(ready) == ["late_y", "late_w", "x", "z", "same_ts_other_format"]


def test_same_timestamp_forced_flush_takes_the_earliest_entered():
    buf = event_buffer.EventBuffer(window_ms=10_000, max_size=2)
    out = []
    for part in ("a", "b", "c", "d"):
        ready, forced = buf.add_and_flush(_ev("00.000", part))
        out.append((_parts(ready), forced))
    assert out == [([], 0), ([], 0), (["a"], 1), (["b"], 1)]
    assert _parts(buf.drain_all_ordered()) == ["c", "d"]


def test_same_timestamp_tail_drain_and_add():
    buf = event_buffer.EventBuffer(window_ms=500)
    buf.add(_ev("00.000", "a"))
    buf.add(_ev("00.000", "b"))  # add() without flushing has the same tie rule
    buf.add_and_flush(_ev("00.000", "c"))
    assert _parts(buf.drain_all_ordered()) == ["a", "b", "c"]
    assert buf.size == 0 and buf.drain_all_ordered() == []


def test_clear_then_reuse_with_same_timestamps():
    buf = event_buffer.EventBuffer(window_ms=500)
    buf.add_and_flush(_ev("00.000", "old1"))
    buf.add_and_flush(_ev("00.000", "old2"))
    buf.clear()
    assert buf.size == 0
    buf.add_and_flush(_ev("00.000", "new1"))
    buf.add_and_flush(_ev("00.000", "new2"))
    ready, _ = buf.add_and_flush(_ev("02.000", "new3"))
    assert _parts(ready) == ["new1", "new2"]
    assert _parts(buf.drain_all_ordered()) == ["new3"]


def test_every_event_comes_out_exactly_once_in_timestamp_then_arrival_order():
    # 24 events over 4 distinct timestamps, arriving out of order; small window and max_size so
    # that cutoff flushes, forced flushes and the final drain all take part.
    seconds = ["02", "01", "02", "00", "03", "01", "02", "00", "01", "03", "00", "02"] * 2
    events = [_ev(s + ".000", "e%02d" % i) for i, s in enumerate(seconds)]
    buf = event_buffer.EventBuffer(window_ms=1500, max_size=5)
    out, forced_total = [], 0
    for ev in events:
        ready, forced = buf.add_and_flush(ev)
        out += ready
        forced_total += forced
    out += buf.drain_all_ordered()
    assert sorted(_parts(out)) == sorted(_parts(events))  # each event exactly once
    assert len(out) == len(events) and forced_total > 0
    # within each flushed batch / the drain, ties are in arrival order
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if a["time"] == b["time"]:
            assert events.index(a) < events.index(b)


def test_different_timestamps_are_ordered_as_before():
    buf = event_buffer.EventBuffer(window_ms=5000)
    for s, part in (("03", "c"), ("01", "a"), ("02", "b")):
        assert buf.add_and_flush(_ev(s + ".000", part)) == ([], 0)
    ready, _ = buf.add_and_flush(_ev("09.000", "z"))
    assert _parts(ready) == ["a", "b", "c"]

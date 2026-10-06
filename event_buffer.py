# event_buffer.py - buffer for out-of-order MQTT events
# Event-driven: uses latest event timestamp for cutoff, not wall clock.
# Time window + bisect insert, output events older than window_ms.

import bisect
import datetime
import threading


def parse_time_to_float(time_str: str) -> float:
    """ISO time string -> float for comparison."""
    try:
        return datetime.datetime.fromisoformat(time_str).timestamp()
    except (ValueError, TypeError) as e:
        print("[event_buffer] Invalid time format: '{}', error: {}".format(time_str, e))
        return datetime.datetime.now().timestamp()


class EventBuffer:
    """
    Buffer for MQTT events that may arrive out of order.
    Events are sorted by timestamp. Event-driven flush uses the latest
    received event's timestamp to determine which older events are safe to process.
    """

    def __init__(self, window_ms: int = 300, max_size: int | None = None):
        self.window_ms = window_ms
        self.max_size = max_size
        self._events = []  # (ts, event) sorted by ts
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """Current number of events in buffer (thread-safe)."""
        with self._lock:
            return len(self._events)

    def add(self, event: dict) -> None:
        """Insert event, keep sorted by timestamp."""
        time_str = event.get("time")
        if not time_str:
            return
        ts = parse_time_to_float(time_str)
        item = (ts, event)
        with self._lock:
            bisect.insort(self._events, item)

    def add_and_flush(self, event: dict) -> tuple[list[dict], int]:
        """
        Add event and return (events_safe_to_process, n_forced_flush).
        Event-driven: uses the new event's timestamp (or latest in buffer) to determine cutoff.
        Events with timestamp < (latest_ts - window_ms) are considered safe.
        n_forced_flush: count of events flushed due to max_size (not cutoff).
        """
        time_str = event.get("time")
        if not time_str:
            return [], 0

        ts = parse_time_to_float(time_str)
        item = (ts, event)

        with self._lock:
            bisect.insort(self._events, item)

            # Watermark must be max timestamp in buffer (late/out-of-order inserts are sorted
            # in — last element equals max today, but this stays correct if ordering changes).
            max_ts = max(e[0] for e in self._events)
            cutoff = max_ts - (self.window_ms / 1000.0)

            # Batch pop: avoid O(n) per pop(0) by slicing once
            i = 0
            while i < len(self._events) and self._events[i][0] < cutoff:
                i += 1
            ready = [ev for _, ev in self._events[:i]]
            self._events = self._events[i:]

            n_forced = 0
            if self.max_size is not None and len(self._events) > self.max_size:
                over = len(self._events) - self.max_size
                ready.extend(ev for _, ev in self._events[:over])
                self._events = self._events[over:]
                n_forced = over

            return ready, n_forced

    def clear(self) -> None:
        """Drop all pending events (e.g. session switch so stale rows are not flushed into a new session)."""
        with self._lock:
            self._events.clear()

    def drain_all_ordered(self) -> list[dict]:
        """Return all buffered events in time order and clear the buffer (end of ordered replay)."""
        with self._lock:
            out = [ev for _, ev in self._events]
            self._events.clear()
            return out

    def flush_ready(self, now_ts: float | None = None) -> list[dict]:
        """[Deprecated] Use add_and_flush() for event-driven processing."""
        raise NotImplementedError("flush_ready is deprecated. Use add_and_flush() instead.")

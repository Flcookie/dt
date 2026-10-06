"""Writes ``start_programs_progress.json`` for the Control Panel (SSH / programs status)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PROGRESS_PATH = os.path.normpath(os.path.join(_ROOT, "start_programs_progress.json"))


def write_control_progress(
    operation: str,
    status: str,
    components: dict[str, dict],
) -> None:
    payload = {
        "operation": operation,
        "status": status,
        "components": components,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        with open(_PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _truncate(s: str, max_len: int = 400) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def record_host_result(
    components: dict[str, dict],
    hostname: str,
    *,
    ok: bool,
    message: str = "",
) -> None:
    components[hostname] = {
        "ok": bool(ok),
        "message": _truncate(message),
    }

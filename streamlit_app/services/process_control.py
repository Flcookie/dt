"""
main_service 进程与现场脚本 — 与 web_api 中 PID/subprocess 逻辑一致（无 FastAPI）。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
import threading
import time

import psutil

from paths import PROJECT_ROOT, ensure_paths

ensure_paths()
import common  # noqa: E402


def _logger_pc() -> logging.Logger:
    fn = getattr(common, "setup_logger", None)
    if callable(fn):
        return fn("process_control", logging.INFO)
    lg = logging.getLogger("process_control")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        lg.addHandler(h)
    return lg


_log = _logger_pc()

_MAIN_SERVICE_PID_FILE = os.path.normpath(os.path.join(PROJECT_ROOT, "main_service.pid"))
_main_service_process: subprocess.Popen | None = None
_PROGRESS_FILE = os.path.normpath(os.path.join(PROJECT_ROOT, "start_programs_progress.json"))
_LAST_CONTROL_ACTION_PATH = os.path.normpath(
    os.path.join(PROJECT_ROOT, ".last_control_action.json")
)
_CONTROL_HISTORY_PATH = os.path.normpath(
    os.path.join(PROJECT_ROOT, ".control_action_history.jsonl")
)

# Dashboard「上一次命令」显示名（与 Control Panel 按钮一致）
SCRIPT_ACTION_LABELS: dict[str, str] = {
    "start_programs": "Start Programs",
    "stop_programs": "Stop Programs",
    "shutdown": "Shutdown",
    "upload_code": "Upload Code",
    "upload_config": "Upload Config",
}

_LABEL_TO_PROGRESS_OP = {
    "Start Programs": "start_programs",
    "Stop Programs": "stop_programs",
    "Shutdown": "shutdown",
}


def _get_main_service_pid() -> int | None:
    if not os.path.exists(_MAIN_SERVICE_PID_FILE):
        return None
    try:
        with open(_MAIN_SERVICE_PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
        if not psutil.pid_exists(pid):
            try:
                os.remove(_MAIN_SERVICE_PID_FILE)
            except OSError:
                pass
            return None
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline() or [])
        if "main_service.py" in cmdline and "web_api" not in cmdline:
            return pid
    except (ValueError, OSError, psutil.NoSuchProcess, psutil.AccessDenied):
        try:
            os.remove(_MAIN_SERVICE_PID_FILE)
        except OSError:
            pass
    return None


def is_main_service_running() -> bool:
    return _get_main_service_pid() is not None


def _terminate_pid(pid: int, killed_pids: set[int]) -> int:
    if pid in killed_pids:
        return 0
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=10)
        killed_pids.add(pid)
        return 1
    except psutil.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
            )
        else:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        killed_pids.add(pid)
        return 1
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        killed_pids.add(pid)
        return 1
    except Exception as e:
        _log.warning("kill proc %s error: %s", pid, e)
        return 0


def kill_all_main_service_processes() -> int:
    """Kill dashboard-spawned + PID-file + orphaned main_service.py processes."""
    global _main_service_process
    killed = 0
    killed_pids: set[int] = set()

    if _main_service_process is not None and _main_service_process.poll() is None:
        killed += _terminate_pid(_main_service_process.pid, killed_pids)
    _main_service_process = None

    pid = _get_main_service_pid()
    if pid is not None:
        killed += _terminate_pid(pid, killed_pids)
    try:
        if os.path.exists(_MAIN_SERVICE_PID_FILE):
            os.remove(_MAIN_SERVICE_PID_FILE)
    except OSError:
        pass

    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                p = proc.pid
                if p in killed_pids:
                    continue
                cmdline = proc.info.get("cmdline") or []
                cmdline_str = " ".join(str(c) for c in cmdline) if cmdline else ""
                if "main_service.py" in cmdline_str and "web_api" not in cmdline_str:
                    killed += _terminate_pid(p, killed_pids)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        _log.warning("kill scan error: %s", e)

    if killed > 0:
        time.sleep(2)
    return killed


def start_main_service(mode: str = "realtime") -> tuple[bool, str]:
    """Start main_service.py; mode realtime or replay (--replay).

    realtime → config.json；replay → 优先 config_local.json（若存在），否则 config.json。
    """
    global _main_service_process
    import services.mqtt_backend as mqtt_backend

    if is_main_service_running():
        return False, "main_service already running — stop it first"
    n = kill_all_main_service_processes()
    if n > 0:
        time.sleep(3)
    if is_main_service_running():
        return False, "Could not terminate existing process — retry"
    try:
        if os.path.exists(_MAIN_SERVICE_PID_FILE):
            os.remove(_MAIN_SERVICE_PID_FILE)
    except OSError:
        pass

    local_path = os.path.normpath(os.path.join(PROJECT_ROOT, "config_local.json"))
    if mode == "replay" and os.path.isfile(local_path):
        cfg_name = "config_local.json"
    else:
        cfg_name = "config.json"
    mqtt_backend.switch_config_file(cfg_name)

    env = os.environ.copy()
    env["CONFIG_FILE"] = cfg_name
    args = [sys.executable, "main_service.py"]
    if mode == "replay":
        args.append("--replay")
    kw: dict = {}
    if sys.platform == "win32":
        try:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        except AttributeError:
            pass
    try:
        _main_service_process = subprocess.Popen(
            args,
            cwd=PROJECT_ROOT,
            env=env,
            **kw,
        )
    except Exception as e:
        return False, str(e) or "start failed"
    time.sleep(5)
    if _main_service_process.poll() is not None:
        return False, "Process exited immediately (check terminal logs)"
    if not is_main_service_running():
        return False, "Process running but PID file missing (permissions/path?)"
    return True, "main_service started ({}, CONFIG_FILE={})".format(mode, cfg_name)


def ensure_main_service_replay(*, wait_kpi_sec: float = 15.0) -> tuple[bool, str]:
    """Stop ``main_service`` so CSV replay can use the Neo4j driver directly (no MQTT event bus).

    Replay runs ``replay_csv_direct.py``; KPI for the dashboard is written to ``.replay_kpi.json``.
    ``wait_kpi_sec`` is unused (kept for call-site compatibility).
    """
    _ = wait_kpi_sec
    import services.mqtt_backend as mqtt_backend

    if is_main_service_running():
        stop_main_service()
        time.sleep(1.5)
        if is_main_service_running():
            return False, "Could not stop main_service — cannot start CSV replay"

    mqtt_backend.clear_kpi_snapshot()
    return True, "Ready — CSV replay will run the pipeline locally (no MQTT events)"


def stop_main_service() -> tuple[int, str]:
    global _main_service_process
    import services.mqtt_backend as mqtt_backend

    n = kill_all_main_service_processes()
    _main_service_process = None
    mqtt_backend.clear_kpi_snapshot()
    return n, "Stopped main_service ({} related process(es))".format(n)


def main_service_status() -> dict:
    pid = _get_main_service_pid()
    return {
        "running": pid is not None,
        "pid": pid,
        "pid_file_exists": os.path.exists(_MAIN_SERVICE_PID_FILE),
    }


def _progress_host_entries(components: dict) -> dict[str, dict]:
    return {
        k: v
        for k, v in (components or {}).items()
        if k != "_error" and isinstance(v, dict)
    }


def _success_from_progress(prog: dict) -> bool | None:
    """Derive overall success from SSH progress JSON; None if not decisive."""
    if not isinstance(prog, dict) or not prog:
        return None
    st = (prog.get("status") or "").strip()
    if st == "error":
        return False
    if st == "completed_with_errors":
        return False
    hosts = _progress_host_entries(prog.get("components") or {})
    if hosts and any(h.get("ok") is not True for h in hosts.values()):
        return False
    if st == "completed":
        return True
    return None


def control_action_status_label(row: dict) -> str:
    """Display status: successful / failed / unknown."""
    prog = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    from_prog = _success_from_progress(prog)
    if from_prog is True:
        return "successful"
    if from_prog is False:
        return "failed"
    ok = row.get("success")
    if ok is True:
        return "successful"
    if ok is False:
        return "failed"
    return "unknown"


def record_control_action(
    label: str, success: bool, message: str = "", *, output: str = ""
) -> None:
    """写入最近一次命令结果（供 LIVE 状态条展示；同步命令与后台线程结束时调用）。"""
    payload: dict | None = None
    try:
        now = datetime.datetime.now()
        payload = {
            "cmd": str(label or "—"),
            "success": bool(success),
            "time": now.strftime("%H:%M:%S"),
            "time_full": now.strftime("%Y-%m-%d %H:%M:%S"),
            "message": (message or "")[:800],
        }
        out_text = (output or "").strip()
        if out_text:
            payload["output"] = out_text[:16000]
        with open(_LAST_CONTROL_ACTION_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    try:
        if payload is not None:
            hist = dict(payload)
            op = _LABEL_TO_PROGRESS_OP.get(hist.get("cmd") or "")
            if op:
                prog = read_start_programs_progress()
                if (prog.get("operation") or "") == op:
                    hist["progress"] = {
                        "operation": prog.get("operation", ""),
                        "status": prog.get("status", ""),
                        "updated_at": prog.get("updated_at", ""),
                        "components": prog.get("components", {}),
                    }
                    eff = _success_from_progress(hist["progress"])
                    if eff is not None:
                        hist["success"] = eff
                        payload["success"] = eff
            with open(_CONTROL_HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(hist, ensure_ascii=False) + "\n")
    except OSError:
        pass


def reset_control_log_session() -> None:
    """Start Programs 提交时清空控制日志，开启新一轮记录。"""
    try:
        with open(_CONTROL_HISTORY_PATH, "w", encoding="utf-8"):
            pass
    except OSError:
        pass


def read_last_control_action() -> dict | None:
    try:
        with open(_LAST_CONTROL_ACTION_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def read_control_action_history(max_items: int = 60) -> list[dict]:
    """Chronological control action history for the current session (since last Start Programs)."""
    if max_items <= 0:
        return []
    try:
        if not os.path.exists(_CONTROL_HISTORY_PATH):
            return []
        with open(_CONTROL_HISTORY_PATH, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        out: list[dict] = []
        for ln in lines:
            try:
                item = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        if len(out) > max_items:
            out = out[-max_items:]
        return out
    except OSError:
        return []


def format_control_action_record_body(row: dict) -> str:
    """Plain log body for one control action (no title banner)."""
    lines: list[str] = []

    msg = (row.get("message") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if msg:
        lines.extend(msg.split("\n"))

    out = (row.get("output") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if out:
        if lines:
            lines.append("")
        lines.extend(out.split("\n"))

    prog = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    comps = prog.get("components") if isinstance(prog, dict) else {}
    if isinstance(comps, dict) and comps:
        lines.append(
            "{} · {} · {}".format(
                prog.get("operation", "—"),
                prog.get("updated_at", "—"),
                prog.get("status", "—"),
            )
        )
        for k in sorted(comps.keys()):
            if k == "_error":
                continue
            info = comps.get(k) or {}
            c_ok = bool(info.get("ok"))
            c_status = "ok" if c_ok else "failed"
            c_msg = (
                (info.get("message") or "")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .strip()
            )
            lines.append("{} — {}".format(k, c_status))
            if c_msg:
                lines.extend(c_msg.split("\n"))
        err = comps.get("_error")
        if isinstance(err, dict):
            em = (err.get("message") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            if em:
                lines.append("_error — failed")
                lines.extend(em.split("\n"))

    return "\n".join(lines)


def format_control_action_record(row: dict) -> str:
    """One control action as plain log text including title banner."""
    cmd = str(row.get("cmd") or "—")
    tm = str(row.get("time_full") or row.get("time") or "")
    status = control_action_status_label(row)
    header = "======== {} · {} · {} ========".format(cmd, tm, status)
    body = format_control_action_record_body(row)
    if body:
        return header + "\n" + body
    return header


def format_control_action_history_text(rows: list[dict]) -> str:
    """Chronological plain log for Control Runs dialog (current session)."""
    if not rows:
        return ""
    return "\n\n".join(format_control_action_record(row) for row in rows)


def _deployment_actions_log_path() -> str:
    return os.path.join(service_log_folder_abs(), "deployment_actions.log")


def _run_script_logged_worker(script_name: str, env: dict | None) -> None:
    """后台脚本：stdout/stderr 追加到 deployment_actions.log，结束时写入 last_control_action。"""
    os.makedirs(service_log_folder_abs(), exist_ok=True)
    log_path = _deployment_actions_log_path()
    label = SCRIPT_ACTION_LABELS.get(script_name, script_name)
    ok = False
    detail = ""
    env = env or os.environ.copy()
    script_path = os.path.join(PROJECT_ROOT, script_name + ".py")
    captured = ""
    try:
        with open(log_path, "a", encoding="utf-8") as lf:
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            lf.write("\n======== {} {} ========\n".format(ts, script_name))
            lf.flush()
            r = subprocess.run(
                [sys.executable, script_path],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            captured = (r.stdout or "") + (r.stderr or "")
            if captured:
                lf.write(captured)
                if not captured.endswith("\n"):
                    lf.write("\n")
            ok = r.returncode == 0
            detail = "exit {}".format(r.returncode)
            lf.write("======== end rc={} ========\n".format(r.returncode))
    except Exception as e:
        detail = str(e)
        ok = False
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write("ERROR: {}\n".format(detail))
        except OSError:
            pass
    record_control_action(label, ok, detail, output=captured)


def run_script_background(script_name: str, enforce_config: str | None = None):
    """后台跑仓库根目录脚本；输出写入 ``event-logs/deployment_actions.log``，结束写 ``.last_control_action.json``。"""
    env = os.environ.copy()
    if enforce_config:
        env["CONFIG_FILE"] = enforce_config
    threading.Thread(
        target=_run_script_logged_worker,
        args=(script_name, env),
        daemon=True,
    ).start()


def tail_deployment_actions_log(max_lines: int = 200) -> tuple[str, str | None]:
    """``deployment_actions.log`` 尾部（upload_code / upload_config / start_programs 等合并日志）。"""
    p = _deployment_actions_log_path()
    if not os.path.isfile(p):
        return (
            "(尚无 deployment_actions.log — 在 LIVE 中执行 Upload Code/Config 或 Start/Stop Programs 后会出现。)",
            None,
        )
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return "".join(tail), p
    except OSError as e:
        return "Error reading {}: {}".format(p, e), p


def is_control_operation_running() -> bool:
    """True while start/stop programs (or shutdown) SSH progress is in flight."""
    prog = read_start_programs_progress()
    return (prog.get("status") or "").strip().lower() == "running"


def read_start_programs_progress() -> dict:
    try:
        if not os.path.exists(_PROGRESS_FILE):
            return {
                "status": "idle",
                "components": {},
                "operation": "",
                "updated_at": "",
            }
        with open(_PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "status": data.get("status", "idle"),
            "components": data.get("components", {}),
            "operation": data.get("operation", ""),
            "updated_at": data.get("updated_at", ""),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "components": {},
            "operation": "",
            "updated_at": "",
        }


def _program_pid_paths_from_env() -> list[str]:
    """Optional local PID paths, e.g. ``DASHBOARD_PROGRAM_PID_FILES=/tmp/a.pid,/tmp/b.pid``."""
    raw = (os.environ.get("DASHBOARD_PROGRAM_PID_FILES") or "").strip()
    if not raw:
        return []
    return [
        os.path.normpath(p.strip())
        for p in raw.split(",")
        if p.strip()
    ]


def get_programs_status() -> str:
    """``activated`` / ``deactivated`` / ``partial`` — PID files (env) or SSH progress JSON."""
    pid_paths = _program_pid_paths_from_env()
    if pid_paths:
        alive = [os.path.isfile(p) for p in pid_paths]
        if all(alive):
            return "activated"
        if not any(alive):
            return "deactivated"
        return "partial"

    prog = read_start_programs_progress()
    op = (prog.get("operation") or "").strip()
    st = (prog.get("status") or "idle").strip()
    hosts = _progress_host_entries(prog.get("components") or {})

    if st == "error":
        return "partial"

    if st == "running":
        return "partial"

    if op == "stop_programs":
        if st == "completed":
            return "deactivated"
        if st == "completed_with_errors":
            if not hosts:
                return "partial"
            oks = [hosts[h].get("ok") is True for h in hosts]
            if not any(oks):
                return "deactivated"
            if all(oks):
                return "deactivated"
            return "partial"
        if st == "error":
            return "partial"

    if op == "start_programs":
        if st == "completed":
            if hosts and all(hosts[h].get("ok") is True for h in hosts):
                return "activated"
            return "deactivated"
        if st == "completed_with_errors":
            if not hosts:
                return "deactivated"
            oks = [hosts[h].get("ok") is True for h in hosts]
            if all(oks):
                return "activated"
            if not any(oks):
                return "deactivated"
            return "partial"

    if hosts:
        oks = [hosts[h].get("ok") is True for h in hosts]
        if all(oks):
            return "activated"
        if not any(oks):
            return "deactivated"
        return "partial"

    return "deactivated"


def get_system_status() -> str:
    """``start`` only after **Start System** (recording + physical MQTT START).

    ``main_service`` running alone (e.g. manual terminal / replay backend) is **not**
    treated as the physical line running — matches Live dashboard semantics.
    """
    if not is_main_service_running():
        return "stop"
    try:
        import services.recording as recording

        if recording.is_recording():
            return "start"
    except Exception:
        pass
    return "stop"


def service_log_folder_abs() -> str:
    """``log_folder`` from project ``config.json`` (default ``event-logs``), absolute path."""
    cfg_path = os.path.normpath(os.path.join(PROJECT_ROOT, "config.json"))
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        rel = cfg.get("log_folder", "event-logs")
    except OSError:
        rel = "event-logs"
    return os.path.normpath(os.path.join(PROJECT_ROOT, rel))


def tail_latest_main_service_log(max_lines: int = 100) -> tuple[str, str | None]:
    """Tail of the newest ``main_service_*.log`` under the configured log folder.

    Returns ``(text, path)`` where ``path`` is absolute or ``None`` if no file.
    """
    folder = service_log_folder_abs()
    if not os.path.isdir(folder):
        return "Log folder not found: {}".format(folder), None
    candidates: list[tuple[float, str]] = []
    try:
        for name in os.listdir(folder):
            if not name.startswith("main_service_") or not name.endswith(".log"):
                continue
            p = os.path.join(folder, name)
            try:
                candidates.append((os.path.getmtime(p), p))
            except OSError:
                pass
    except OSError as e:
        return "Cannot list log folder: {}".format(e), None
    if not candidates:
        return "No main_service_*.log in {}.".format(folder), None
    path = max(candidates, key=lambda x: x[0])[1]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return "".join(tail), path
    except OSError as e:
        return "Error reading {}: {}".format(path, e), path

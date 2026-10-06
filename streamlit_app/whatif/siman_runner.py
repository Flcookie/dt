"""Arena what-if via Config.txt + Input.txt → siman -B -Q model.p → Output.txt."""
from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

PARAMETER_KEYS: tuple[str, ...] = (
    "WIP Limit",
    "Stage1 Buffer Capacity",
    "Stage2 Buffer Capacity",
    "Stage3 Buffer Capacity",
    "Stage4 Buffer Capacity",
    "Stage5 Buffer Capacity",
    "Stage6 Buffer Capacity",
)

# Line order in Input.txt (ReadInput assignments)
_PARAM_INDEX: dict[str, int] = {name: i for i, name in enumerate(PARAMETER_KEYS)}

# Line order in Config.txt (ReadConfig assignments)
CONFIG_KEYS: tuple[str, ...] = ("ReplicasNum", "WarmUp", "SimLength")

SIMAN_EXE = Path(
    os.environ.get(
        "ARENA_SIMAN_EXE",
        r"C:\Program Files\Rockwell Software\Arena\siman.exe",
    )
)
MODEL_P = "model.p"
INPUT_FILE = "Input.txt"
CONFIG_FILE = "Config.txt"
OUTPUT_FILE = "Output.txt"
_COPY_ALONG = (MODEL_P, "MOTOWN_7Stations_Arena.csv", "model.dsn")


@dataclass(frozen=True)
class SweepPoint:
    x: float
    wip: float
    completion_rate: float
    scrap_rate: float
    lead_time: float


@dataclass(frozen=True)
class SweepResult:
    parameter: str
    points: tuple[SweepPoint, ...]


def _repo_root() -> Path:
    # streamlit_app/whatif/siman_runner.py -> repo root
    return Path(__file__).resolve().parents[2]


def default_work_folder() -> Path:
    return _repo_root() / "model"


def resolve_work_folder(raw: str | None) -> Path:
    text = str(raw or "").strip()
    return Path(text) if text else default_work_folder()


def work_folder_ready(work_folder: str | Path | None = None) -> bool:
    folder = resolve_work_folder(str(work_folder) if work_folder else None)
    return (
        (folder / MODEL_P).is_file()
        and (folder / INPUT_FILE).is_file()
        and (folder / CONFIG_FILE).is_file()
    )


def _read_numeric_lines(path: Path, *, need: int) -> list[float]:
    vals: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        vals.append(float(text))
    if len(vals) < need:
        raise ValueError(f"{path.name} needs {need} values, found {len(vals)}.")
    return vals[:need]


def _format_numeric_lines(values: list[float]) -> str:
    return "\n".join(
        str(int(round(v))) if float(v) == int(v) else str(v) for v in values
    )


def read_input_values(path: Path) -> list[float]:
    return _read_numeric_lines(path, need=len(PARAMETER_KEYS))


def write_input_values(path: Path, values: list[float]) -> None:
    if len(values) != len(PARAMETER_KEYS):
        raise ValueError(f"Expected {len(PARAMETER_KEYS)} input values.")
    path.write_text(_format_numeric_lines(values) + "\n", encoding="utf-8")


def read_config_values(path: Path) -> list[float]:
    """Return [ReplicasNum, WarmUp, SimLength]."""
    return _read_numeric_lines(path, need=len(CONFIG_KEYS))


def write_config_values(path: Path, values: list[float]) -> None:
    if len(values) != len(CONFIG_KEYS):
        raise ValueError(f"Expected {len(CONFIG_KEYS)} config values.")
    path.write_text(_format_numeric_lines(values) + "\n", encoding="utf-8")


def parameter_default(parameter: str, work_folder: str | Path | None = None) -> float:
    folder = resolve_work_folder(str(work_folder) if work_folder else None)
    path = folder / INPUT_FILE
    if not path.is_file():
        return 0.0
    idx = _PARAM_INDEX.get(parameter)
    if idx is None:
        return 0.0
    return read_input_values(path)[idx]


def replications_default(work_folder: str | Path | None = None) -> int:
    folder = resolve_work_folder(str(work_folder) if work_folder else None)
    path = folder / CONFIG_FILE
    if not path.is_file():
        return 1
    try:
        return max(1, int(round(read_config_values(path)[0])))
    except (ValueError, OSError):
        return 1


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def parse_output_file(path: Path) -> tuple[float, float, float, float]:
    """Return (WIP, completion, scrap, lead time) as median over replications."""
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path.name} after simulation.")

    wips: list[float] = []
    comps: list[float] = []
    scraps: list[float] = []
    leads: list[float] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = [p.strip() for p in text.split(",")]
        if len(parts) < 5:
            continue
        wips.append(float(parts[1]))
        comps.append(float(parts[2]))
        scraps.append(float(parts[3]))
        leads.append(float(parts[4]))

    if not wips:
        raise ValueError(f"No data rows in {path.name}.")

    return (
        _median(wips),
        _median(comps),
        _median(scraps),
        _median(leads),
    )


def _ensure_siman() -> Path:
    if not SIMAN_EXE.is_file():
        raise FileNotFoundError(
            f"siman.exe not found at {SIMAN_EXE}. Install Arena or set ARENA_SIMAN_EXE."
        )
    return SIMAN_EXE


def _prepare_run_dir(model_dir: Path, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in _COPY_ALONG:
        src = model_dir / name
        if src.is_file():
            shutil.copy2(src, run_dir / name)


def run_siman_once(
    model_dir: Path,
    values: list[float],
    *,
    run_name: str,
    config_values: list[float] | None = None,
) -> SweepPoint:
    model_dir = Path(model_dir).resolve()
    run_dir = model_dir / "runs" / run_name
    _prepare_run_dir(model_dir, run_dir)
    write_input_values(run_dir / INPUT_FILE, values)

    if config_values is None:
        config_values = read_config_values(model_dir / CONFIG_FILE)
    write_config_values(run_dir / CONFIG_FILE, config_values)

    out_path = run_dir / OUTPUT_FILE
    if out_path.is_file():
        out_path.unlink()

    siman = _ensure_siman()
    proc = subprocess.run(
        [str(siman), "-B", "-Q", MODEL_P],
        cwd=str(run_dir),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"siman failed (code {proc.returncode}): {tail}")

    wip, comp, scrap, lead = parse_output_file(out_path)
    return SweepPoint(
        x=0.0,
        wip=round(wip, 4),
        completion_rate=round(comp, 4),
        scrap_rate=round(scrap, 4),
        lead_time=round(lead, 4),
    )


def _worker_run_point(
    payload: tuple[str, str, list[float], list[float], float, str],
) -> SweepPoint:
    model_dir_s, param, base_values, config_values, x, run_name = payload
    model_dir = Path(model_dir_s)
    values = list(base_values)
    values[_PARAM_INDEX[param]] = x
    pt = run_siman_once(
        model_dir, values, run_name=run_name, config_values=config_values
    )
    return SweepPoint(
        x=x,
        wip=pt.wip,
        completion_rate=pt.completion_rate,
        scrap_rate=pt.scrap_rate,
        lead_time=pt.lead_time,
    )


def run_parameter_sweep(
    *,
    work_folder: str | Path,
    parameter: str,
    from_val: float,
    to_val: float,
    step: float,
    replications: int | None = None,
    progress_cb=None,
) -> SweepResult:
    if parameter not in _PARAM_INDEX:
        raise ValueError(f"Unknown parameter: {parameter}")
    if step <= 0:
        raise ValueError("Step must be greater than 0.")
    if to_val < from_val:
        raise ValueError("To must be greater than or equal to From.")

    folder = resolve_work_folder(str(work_folder))
    if not work_folder_ready(folder):
        raise FileNotFoundError(
            f"Work folder must contain {MODEL_P}, {INPUT_FILE}, and {CONFIG_FILE}: {folder}"
        )

    base = read_input_values(folder / INPUT_FILE)
    config = read_config_values(folder / CONFIG_FILE)
    if replications is not None:
        if int(replications) < 1:
            raise ValueError("Replications must be at least 1.")
        config = [float(int(replications)), config[1], config[2]]

    values_x: list[float] = []
    current = float(from_val)
    while current <= float(to_val) + 1e-9:
        values_x.append(current)
        current += float(step)

    if len(values_x) > 15:
        raise ValueError(f"Too many points ({len(values_x)}). Max 15.")

    workers = max(1, min(os.cpu_count() or 1, len(values_x)))
    payloads = [
        (
            str(folder.resolve()),
            parameter,
            base,
            config,
            x,
            f"pt_{parameter.replace(' ', '_')}_{int(round(x))}",
        )
        for x in values_x
    ]

    points: list[SweepPoint] = []
    if workers == 1 or len(payloads) == 1:
        for i, payload in enumerate(payloads):
            if progress_cb:
                progress_cb(i, len(payloads), payload[4])
            points.append(_worker_run_point(payload))
    else:
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker_run_point, p): p for p in payloads}
            for fut in as_completed(futures):
                payload = futures[fut]
                if progress_cb:
                    progress_cb(done, len(payloads), payload[4])
                points.append(fut.result())
                done += 1

    points.sort(key=lambda p: p.x)
    return SweepResult(parameter=parameter, points=tuple(points))


def sweep_to_chart_series(result: SweepResult) -> dict[str, list[float]]:
    return {
        "x": [p.x for p in result.points],
        "WIP": [p.wip for p in result.points],
        "Completion Rate": [p.completion_rate for p in result.points],
        "Scrap Rate": [p.scrap_rate for p in result.points],
        "Lead Time": [p.lead_time for p in result.points],
    }

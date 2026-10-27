"""演化任务的目录/清单/进度/日志解析（纯模块，不 import visualizer / backend.core / backend.app）。

这些函数原先写在 ``scripts/visualizer.py`` 里，搬到这里后由 blueprint 与
瘦身后的 visualizer.py 共享。可变全局状态从 ``backend.state`` 取。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re as _re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import paths, state

logger = logging.getLogger(__name__)


TERMINATE_GRACE_SECONDS = 5.0


def _reap_finished_runs() -> None:
    """Drop finished tasks from the registry and record their exit code.

    已被用户取消（status == "interrupted"）的任务保持原状态，不被退出码覆盖。
    """
    for run_id, run in state.snapshot():
        process = run.get("process")
        if process is None:
            # 服务重启后接管的条目：没有进程句柄，只能用 pid 判断是否已结束
            if state.pid_alive(run.get("pid")):
                continue
            state.forget_run(run_id)
            continue
        try:
            return_code = process.poll()
        except OSError:
            return_code = None
        if return_code is None:
            continue
        if run.get("status") == "running":
            run["status"] = "completed" if return_code == 0 else "failed"
        run["return_code"] = return_code
        state.forget_run(run_id)


def _signal_run(pid: int, sig: int) -> None:
    """对任务进程组发信号（子进程用 start_new_session=True 启动）。"""
    try:
        os.killpg(os.getpgid(pid), sig)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate_run(pid: int, run_id: str, process=None) -> bool:
    """先 SIGTERM，宽限期后仍存活则 SIGKILL。返回是否发出过终止信号。"""
    if process is None and not state.pid_runs_task(pid, run_id):
        return False
    _signal_run(pid, signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        if process is not None:
            try:
                if process.poll() is not None:
                    return True
            except OSError:
                return True
        elif not state.pid_alive(pid):
            return True
        time.sleep(0.2)
    logger.warning("Run %s ignored SIGTERM after %.0fs, sending SIGKILL", run_id, TERMINATE_GRACE_SECONDS)
    _signal_run(pid, signal.SIGKILL)
    if process is not None:
        try:
            process.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return True


def find_latest_checkpoint(base_folder):
    base_folder = os.path.normpath(base_folder)
    # Check whether the base folder is itself a checkpoint folder
    if os.path.basename(base_folder).startswith("checkpoint_"):
        return base_folder

    checkpoint_folders = glob.glob("**/checkpoint_*", root_dir=base_folder, recursive=True)
    if not checkpoint_folders:
        logger.info(f"No checkpoint folders found in {base_folder}")
        return None
    checkpoint_folders = [os.path.join(base_folder, folder) for folder in checkpoint_folders]
    checkpoint_folders.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    logger.debug(f"Found checkpoint folder: {checkpoint_folders[0]}")
    return checkpoint_folders[0]


def _latest_checkpoint_for(checkpoint_root):
    """Return the newest checkpoint folder under a run output dir, or None."""
    if not os.path.isdir(checkpoint_root):
        return None
    return find_latest_checkpoint(checkpoint_root)


def _count_checkpoint_programs(checkpoint_dir: Path) -> int:
    programs_dir = checkpoint_dir / "programs"
    try:
        return len([name for name in os.listdir(programs_dir) if name.endswith(".json")])
    except OSError:
        return 0


def _checkpoints_root(run_dir: Path) -> Path:
    return run_dir / "output" / "checkpoints"


def _list_checkpoints(run_dir: Path) -> list[dict[str, Any]]:
    """List checkpoint folders of one run, oldest first."""
    root = _checkpoints_root(run_dir)
    if not root.is_dir():
        return []
    checkpoints = []
    for path in root.iterdir():
        if not path.is_dir() or not path.name.startswith("checkpoint_"):
            continue
        try:
            iteration = int(path.name.rsplit("_", 1)[-1])
        except (IndexError, ValueError):
            continue
        try:
            updated_at = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            updated_at = ""
        checkpoints.append(
            {
                "name": path.name,
                "iteration": iteration,
                "programs": _count_checkpoint_programs(path),
                "updated_at": updated_at,
            }
        )
    checkpoints.sort(key=lambda item: (item["iteration"], item["name"]))
    return checkpoints


def _resolve_resume_checkpoint(value: str) -> Optional[str]:
    """Resolve `resume_from` (`<run_id>` or `<run_id>/<checkpoint_name>`).

    Only checkpoints that live inside a run owned by this service are accepted.
    """
    parts = value.strip().strip("/").split("/")
    if not 1 <= len(parts) <= 2 or not all(parts):
        return None
    run_dir = _resolve_run_dir(parts[0])
    if run_dir is None:
        return None
    if len(parts) == 1:
        latest = _latest_checkpoint_for(str(_checkpoints_root(run_dir)))
        return str(latest) if latest else None
    resolved = _resolve_checkpoint(run_dir, parts[1])
    return str(resolved) if resolved else None


def _resolve_checkpoint(run_dir: Path, checkpoint_name: str) -> Optional[Path]:
    """Resolve a checkpoint folder name inside a run, rejecting path traversal."""
    if not checkpoint_name or "/" in checkpoint_name or "\\" in checkpoint_name:
        return None
    if checkpoint_name in {".", ".."} or not checkpoint_name.startswith("checkpoint_"):
        return None
    root = _checkpoints_root(run_dir)
    candidate = (root / checkpoint_name).resolve()
    if candidate.parent != root.resolve() or not candidate.is_dir():
        return None
    return candidate


def _read_run_generation(run_dir):
    """Best-effort count of generated programs in a run output dir."""
    checkpoint = run_dir / "output" / "checkpoints"
    if not checkpoint.is_dir():
        return 0
    newest = _latest_checkpoint_for(str(checkpoint))
    if not newest:
        return 0
    programs_dir = os.path.join(newest, "programs")
    try:
        return len([name for name in os.listdir(programs_dir) if name.endswith(".json")])
    except OSError:
        return 0


def _run_summary(run_dir):
    """Describe one evolve_tasks/evolve_task_* run folder for the task list."""
    output_dir = run_dir / "output"
    input_dir = run_dir / "input"

    iterations = 0
    model = ""
    config_path = input_dir / "config.yaml"
    if config_path.is_file():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            config = None
        if isinstance(config, dict):
            if isinstance(config.get("max_iterations"), int):
                iterations = config["max_iterations"]
            llm = config.get("llm")
            if isinstance(llm, dict):
                if isinstance(llm.get("primary_model"), str):
                    model = llm["primary_model"]
                elif isinstance(llm.get("models"), list) and llm["models"]:
                    first = llm["models"][0]
                    if isinstance(first, dict) and isinstance(first.get("name"), str):
                        model = first["name"]

    best_metrics = {}
    best_info = output_dir / "best" / "best_program_info.json"
    if best_info.is_file():
        try:
            with open(best_info, "r", encoding="utf-8") as info_file:
                info = json.load(info_file)
            if isinstance(info, dict) and isinstance(info.get("metrics"), dict):
                best_metrics = info["metrics"]
        except (OSError, json.JSONDecodeError):
            pass

    # 任务名与模板：本轮启动时写入 input/task.json。
    task_manifest = {}
    task_path = input_dir / "task.json"
    if task_path.is_file():
        try:
            with open(task_path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
            if isinstance(manifest, dict):
                task_manifest = manifest
        except (OSError, json.JSONDecodeError):
            pass

    try:
        updated_at = datetime.fromtimestamp(os.path.getmtime(run_dir)).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        updated_at = ""

    try:
        run_dir_display = run_dir.relative_to(paths.PROJECT_ROOT).as_posix()
    except ValueError:
        # Tests and ad-hoc runs point paths.GUI_RUNS_DIR outside the repo.
        run_dir_display = run_dir.as_posix()

    return {
        "id": run_dir.name,
        "name": task_manifest.get("name") or run_dir.name,
        "template": task_manifest.get("template", ""),
        "template_name": task_manifest.get("template_name", ""),
        "resumed_from": task_manifest.get("resumed_from") or "",
        "run_dir": run_dir_display,
        "output_dir": Path(os.path.relpath(output_dir, paths.PROJECT_ROOT)).as_posix(),
        # `iterations` 在详情接口里会被 _run_progress 的“每轮明细”覆盖，
        # 因此计划轮数另外用 planned_iterations 暴露给 UI。
        "planned_iterations": iterations,
        "iterations": iterations,
        "programs": _read_run_generation(run_dir),
        "best_metrics": best_metrics,
        "model": model,
        "updated_at": updated_at,
        # 演化结束会自动写 output/report/report.json，列表页据此决定是否显示「报告」入口
        "has_report": (output_dir / "report" / "report.json").is_file(),
    }


_LOG_TIMESTAMP_RE = _re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_LOG_ITERATION_DONE_RE = _re.compile(
    r"Iteration (\d+): Program (\S+) \(parent: (\S+)\) completed in ([\d.]+)s"
)
_LOG_ITERATION_ERROR_RE = _re.compile(r"Iteration (\d+) error: (.*)")
_LOG_TOKEN_RE = _re.compile(r"tokens: (\d+) \(prompt: (\d+), completion: (\d+)\)")
_LOG_METRICS_RE = _re.compile(r"Metrics: (.+)$")
_LOG_CHECKPOINT_RE = _re.compile(r"Saved checkpoint at iteration (\d+)")
_LOG_ISLAND_RE = _re.compile(
    r"Island (\d+): (\d+) programs?, best=(-?[\d.]+), avg=(-?[\d.]+), "
    r"diversity=([\d.]+), gen=(\d+)"
)
_LOG_ISLAND_BEST_RE = _re.compile(r"\(best: ([0-9a-fA-F-]+)\)")
_LOG_BEST_PROGRAM_RE = _re.compile(r"🌟 New best solution found at iteration (\d+): (\S+)")

# 启动阶段的日志标志：出现任意一条就说明进程已经跑起来了，只是还没跑完第一轮
_RUN_STARTUP_MARKERS = (
    "Initialized OpenEvolve",
    "Initialized evaluator",
    "Initialized prompt sampler",
    "Adding initial program",
    "Starting process-based evolution",
)


def _parse_timestamp(line):
    match = _LOG_TIMESTAMP_RE.match(line)
    if not match:
        return ""
    return match.group(1).replace(",", ".")


def _parse_log_text(log_text):
    """Extract per-iteration progress from an experiment.log.

    The log mixes lines from the main process and from worker processes, so the
    result is ordered by iteration number rather than by file order.
    """
    iterations = {}
    checkpoints = []
    islands = {}
    migrations = []
    best_events = []
    started_at = ""
    finished_at = ""
    total_started = None
    token_totals = {"prompt": 0, "completion": 0}
    for line in log_text.splitlines():
        timestamp = _parse_timestamp(line)
        if timestamp:
            if not started_at:
                started_at = timestamp
            finished_at = timestamp

        match = _LOG_ITERATION_DONE_RE.search(line)
        if match:
            iteration = int(match.group(1))
            entry = iterations.setdefault(iteration, {})
            entry.update(
                {
                    "iteration": iteration,
                    "status": "completed",
                    "program_id": match.group(2),
                    "parent_id": match.group(3),
                    "duration_seconds": float(match.group(4)),
                    "finished_at": timestamp,
                }
            )
            tokens = _LOG_TOKEN_RE.search(line)
            if tokens:
                entry["tokens"] = {
                    "total": int(tokens.group(1)),
                    "prompt": int(tokens.group(2)),
                    "completion": int(tokens.group(3)),
                }
                token_totals["prompt"] += int(tokens.group(2))
                token_totals["completion"] += int(tokens.group(3))
            continue

        match = _LOG_ITERATION_ERROR_RE.search(line)
        if match:
            iteration = int(match.group(1))
            message = match.group(2).strip()
            tokens = _LOG_TOKEN_RE.search(message)
            entry = iterations.setdefault(iteration, {})
            entry.update(
                {
                    "iteration": iteration,
                    "status": "failed",
                    "error": _LOG_TOKEN_RE.sub("", message).strip(),
                    "finished_at": timestamp,
                }
            )
            if tokens:
                entry["tokens"] = {
                    "total": int(tokens.group(1)),
                    "prompt": int(tokens.group(2)),
                    "completion": int(tokens.group(3)),
                }
                token_totals["prompt"] += int(tokens.group(2))
                token_totals["completion"] += int(tokens.group(3))
            if "LLM generation failed" in message:
                entry["stage"] = "llm"
            elif "SEARCH block" in message:
                entry["stage"] = "diff"
            elif "evaluat" in message.lower():
                entry["stage"] = "evaluation"
            continue

        match = _LOG_METRICS_RE.search(line)
        if match and iterations:
            latest = iterations[max(iterations)]
            latest["metrics_text"] = match.group(1).strip()
            continue

        match = _LOG_CHECKPOINT_RE.search(line)
        if match:
            checkpoints.append(int(match.group(1)))
            continue

        match = _LOG_ISLAND_RE.search(line)
        if match:
            best = _LOG_ISLAND_BEST_RE.search(line)
            # log_island_status() prints the whole table at once; keep the last.
            islands[int(match.group(1))] = {
                "index": int(match.group(1)),
                "programs": int(match.group(2)),
                "best_score": float(match.group(3)),
                "avg_score": float(match.group(4)),
                "diversity": float(match.group(5)),
                "generation": int(match.group(6)),
                "best_program_id": best.group(1) if best else "",
                "snapshot_at": timestamp,
            }
            continue

        match = _LOG_BEST_PROGRAM_RE.search(line)
        if match:
            best_events.append(
                {"iteration": int(match.group(1)), "program_id": match.group(2), "at": timestamp}
            )
            continue

        if "Performing migration" in line:
            migration = _re.search(r"at iteration (\d+)", line)
            if migration:
                migrations.append(int(migration.group(1)))
            continue

        if "Starting process-based evolution" in line:
            total = _re.search(r"for (\d+) iterations", line)
            if total:
                total_started = int(total.group(1))
        elif "Evolution complete!" in line or "✅ Evolution completed" in line:
            pass

    completed = sum(1 for entry in iterations.values() if entry.get("status") == "completed")
    failed = sum(1 for entry in iterations.values() if entry.get("status") == "failed")
    return {
        "iterations": [iterations[key] for key in sorted(iterations)],
        "completed_iterations": completed,
        "failed_iterations": failed,
        "current_iteration": max(iterations) if iterations else 0,
        "total_started": total_started,
        "checkpoints": checkpoints,
        "islands": [islands[key] for key in sorted(islands)],
        "migrations": migrations,
        "best_events": best_events,
        "started_at": started_at,
        "finished_at": finished_at,
        "tokens": token_totals,
    }


def _island_metrics(run_dir, log_islands):
    """Merge log island snapshots with the newest checkpoint's grid population.

    The log only prints island status at checkpoints, so a run that has not
    checkpointed yet still shows what the manager reported at startup. Once a
    checkpoint exists, per-island program counts and best ids come from there.
    """
    islands = {entry["index"]: dict(entry) for entry in log_islands}
    checkpoint = _latest_checkpoint_for(str(run_dir / "output")) if (run_dir / "output").is_dir() else None
    if not checkpoint:
        return [islands[key] for key in sorted(islands)]

    try:
        with open(os.path.join(checkpoint, "metadata.json"), "r", encoding="utf-8") as meta_file:
            meta = json.load(meta_file)
    except (OSError, json.JSONDecodeError):
        return [islands[key] for key in sorted(islands)]

    ids_by_island = meta.get("islands") or []
    best_by_island = meta.get("island_best_programs") or []
    generations = meta.get("island_generations") or []
    scores = {}
    programs_dir = os.path.join(checkpoint, "programs")
    try:
        for name in os.listdir(programs_dir):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(programs_dir, name), "r", encoding="utf-8") as program_file:
                program = json.load(program_file)
            score = (program.get("metrics") or {}).get("combined_score")
            if isinstance(score, (int, float)):
                scores[program.get("id")] = float(score)
    except OSError:
        pass

    count = max(len(ids_by_island), len(islands))
    for index in range(count):
        entry = islands.setdefault(index, {"index": index})
        program_ids = ids_by_island[index] if index < len(ids_by_island) else []
        best_id = best_by_island[index] if index < len(best_by_island) else None
        entry["programs"] = len(program_ids)
        entry["program_ids"] = list(program_ids)
        entry["best_program_id"] = best_id or entry.get("best_program_id", "")
        entry["generation"] = (
            generations[index] if index < len(generations) else entry.get("generation", 0)
        )
        island_scores = [scores[pid] for pid in program_ids if pid in scores]
        if island_scores:
            entry["best_score"] = max(island_scores)
            entry["avg_score"] = sum(island_scores) / len(island_scores)
        entry.setdefault("best_score", 0.0)
        entry.setdefault("avg_score", 0.0)
        entry.setdefault("diversity", 0.0)
    return [islands[key] for key in sorted(islands)]


def _infer_run_status(log_text, progress, has_checkpoint, log_age_seconds=None):
    """Classify a run from its log, falling back to on-disk artifacts."""
    # A user-requested shutdown also prints the "✅ Evolution completed" banner,
    # so check for it before the success markers.
    if "Shutdown requested" in log_text or "KeyboardInterrupt" in log_text:
        return "interrupted"
    if "Evolution complete!" in log_text or "✅ Evolution completed" in log_text:
        return "completed"
    if not log_text:
        return "interrupted" if has_checkpoint else "pending"
    stale = log_age_seconds is not None and log_age_seconds > paths.STALE_RUN_SECONDS
    if not progress.get("iterations"):
        # 还没有任何一轮：任务可能正在启动/评估初始程序，也可能启动就崩了。
        # 不能一律判 failed——初始评估慢的时候会闪一下"失败"再跳回运行中。
        if "Traceback (most recent call last)" in log_text:
            return "failed"
        if stale:
            started = any(marker in log_text for marker in _RUN_STARTUP_MARKERS)
            return "interrupted" if started else "failed"
        return "running"
    total = progress.get("total_started")
    last = progress.get("current_iteration") or 0
    if total and last >= total:
        # Every round reported, but the completion banner never appeared.
        return "interrupted"
    if stale:
        # No log activity for a long time: the process is gone, not running.
        return "interrupted"
    return "running"


def _read_log_text(log_path, max_bytes=2 * 1024 * 1024):
    """Read the tail of a log file without loading huge files whole."""
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as log_file:
            if size > max_bytes:
                log_file.seek(size - max_bytes)
                log_file.readline()  # drop the partial first line
            return log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _run_progress(run_dir):
    """Live progress for one run, derived from experiment.log plus checkpoints."""
    output_dir = run_dir / "output"
    log_text = _read_log_text(output_dir / "experiment.log")
    progress = _parse_log_text(log_text)
    checkpoint = _latest_checkpoint_for(str(output_dir)) if output_dir.is_dir() else None

    iterations = 0
    config_path = run_dir / "input" / "config.yaml"
    if config_path.is_file():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(config, dict) and isinstance(config.get("max_iterations"), int):
                iterations = config["max_iterations"]
        except (OSError, yaml.YAMLError):
            pass

    total = iterations or progress.get("total_started") or 0
    finished = progress["completed_iterations"] + progress["failed_iterations"]

    log_age_seconds = None
    try:
        log_age_seconds = max(0.0, time.time() - os.path.getmtime(output_dir / "experiment.log"))
    except OSError:
        pass

    status = _infer_run_status(
        log_text, progress, checkpoint is not None, log_age_seconds=log_age_seconds
    )
    return {
        "status": status,
        "total_iterations": total,
        "current_iteration": progress["current_iteration"],
        "finished_iterations": finished,
        "completed_iterations": progress["completed_iterations"],
        "failed_iterations": progress["failed_iterations"],
        "percent": round(100.0 * finished / total, 1) if total else 0.0,
        "iterations": progress["iterations"],
        "tokens": progress["tokens"],
        "checkpoints": progress["checkpoints"],
        "islands": _island_metrics(run_dir, progress["islands"]),
        "migrations": progress["migrations"],
        "best_events": progress["best_events"],
        "log_tail": log_text.splitlines()[-40:],
        "started_at": progress["started_at"],
        "finished_at": progress["finished_at"],
        "has_checkpoint": checkpoint is not None,
        "checkpoint_dir": (
            Path(os.path.relpath(checkpoint, paths.PROJECT_ROOT)).as_posix() if checkpoint else ""
        ),
    }


def _list_runs():
    """List every GUI-launched evolution run, newest first."""
    if not paths.GUI_RUNS_DIR.is_dir():
        return []
    runs = []
    for path in paths.GUI_RUNS_DIR.iterdir():
        if not path.is_dir() or not path.name.startswith("evolve_task_"):
            continue
        summary = _run_summary(path)
        progress = _run_progress(path)
        summary.update(
            {
                "status": progress["status"],
                "total_iterations": progress["total_iterations"],
                "finished_iterations": progress["finished_iterations"],
                "completed_iterations": progress["completed_iterations"],
                "failed_iterations": progress["failed_iterations"],
                "percent": progress["percent"],
                "current_iteration": progress["current_iteration"],
                "last_error": next(
                    (
                        entry.get("error", "")
                        for entry in reversed(progress["iterations"])
                        if entry.get("status") == "failed"
                    ),
                    "",
                ),
                "has_checkpoint": progress["has_checkpoint"],
                "checkpoint_dir": progress["checkpoint_dir"],
            }
        )
        runs.append(summary)
    runs.sort(key=lambda run: run["updated_at"], reverse=True)
    return runs


def _resolve_run_dir(run_id: str) -> Optional[Path]:
    """Map a run id to its directory, rejecting path traversal."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return None
    run_dir = paths.GUI_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return None
    return run_dir


def _resolve_run_output(run_id: str) -> Optional[str]:
    """Map a run id from /api/runs to its output dir, rejecting path traversal."""
    run_dir = _resolve_run_dir(run_id)
    return str(run_dir / "output") if run_dir is not None else None


def _build_file_tree(root, relative, limit=paths._MAX_TREE_ITEMS):
    """Scan one folder and return plain dicts the UI can render as a tree."""
    entries = []
    try:
        children = sorted(root.iterdir(), key=lambda path: (not path.is_dir(), path.name))
    except OSError:
        return entries
    for path in children[:limit]:
        if path.name == "__pycache__":
            continue
        rel = "/".join([*relative, path.name])
        if path.is_dir():
            entries.append({
                "type": "dir",
                "name": path.name,
                "path": rel,
                "children": _build_file_tree(path, [*relative, path.name], limit),
            })
        else:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            entries.append({"type": "file", "name": path.name, "path": rel, "size": size})
    return entries


def _current_run_status() -> Optional[dict[str, Any]]:
    if state.current_run is None:
        return None

    process = state.current_run["process"]
    return_code = process.poll()
    if return_code is not None and state.current_run["status"] == "running":
        state.current_run["status"] = "completed" if return_code == 0 else "failed"
        state.current_run["return_code"] = return_code

    log_path = state.current_run["log_path"]
    try:
        with open(log_path, "rb") as log_file:
            log_file.seek(max(0, os.path.getsize(log_path) - 16000))
            log = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        log = ""

    project_root = str(paths.PROJECT_ROOT)
    log = log.replace(f"{project_root}{os.sep}", "")

    return {
        "id": state.current_run["id"],
        "status": state.current_run["status"],
        "run_dir": Path(os.path.relpath(state.current_run["run_dir"], paths.PROJECT_ROOT)).as_posix(),
        "output_dir": Path(os.path.relpath(state.current_run["output_dir"], paths.PROJECT_ROOT)).as_posix(),
        "return_code": state.current_run.get("return_code"),
        "log": log,
    }

import glob
import ipaddress
import json
import logging
import math
import os
import re as _re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from numbers import Number
from typing import Any, Optional
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request
from manual import create_manual_blueprint

logger = logging.getLogger(__name__)
app = Flask(__name__, template_folder="templates")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_RUNS_DIR = PROJECT_ROOT / "evolve_tasks"


def _load_dotenv() -> None:
    """读取项目根目录的 .env.local / .env（本地开发用）。

    已存在的进程环境变量不会被覆盖，所以生产环境 `export OPENEVOLVE_API_KEY=...`
    启动时，export 的值优先于文件里的值。
    """
    for name in (".env.local", ".env"):
        path = PROJECT_ROOT / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


_load_dotenv()
# 公共演化求解器：通用评估器 + 任务模板库（模板只描述目标与约束，不含评估器）。
SOLVER_DIR = PROJECT_ROOT / "solver"
SOLVER_EVALUATOR_PATH = SOLVER_DIR / "evaluator.py"
TEMPLATE_DIR = SOLVER_DIR / "templates"
DEFAULT_TEMPLATE_ID = "famou-调参"
GLOBAL_CONFIG_PATH = GUI_RUNS_DIR / "global_config.yml"
# A run whose log has not grown for this long is treated as interrupted rather
# than running, so a killed process does not look alive forever.
STALE_RUN_SECONDS = 15 * 60
current_run: Optional[dict[str, Any]] = None
run_lock = threading.Lock()
# Every task started from this service, keyed by run id. Tasks may run
# concurrently; the registry is only used to terminate them on request.
active_runs: dict[str, dict[str, Any]] = {}


def _reap_finished_runs() -> None:
    """Drop finished tasks from the registry and record their exit code."""
    for run_id in list(active_runs):
        run = active_runs[run_id]
        return_code = run["process"].poll()
        if return_code is None:
            continue
        run["status"] = "completed" if return_code == 0 else "failed"
        run["return_code"] = return_code
        del active_runs[run_id]


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
        run_dir_display = run_dir.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        # Tests and ad-hoc runs point GUI_RUNS_DIR outside the repo.
        run_dir_display = run_dir.as_posix()

    return {
        "id": run_dir.name,
        "name": task_manifest.get("name") or run_dir.name,
        "template": task_manifest.get("template", ""),
        "template_name": task_manifest.get("template_name", ""),
        "resumed_from": task_manifest.get("resumed_from") or "",
        "run_dir": run_dir_display,
        "output_dir": Path(os.path.relpath(output_dir, PROJECT_ROOT)).as_posix(),
        # `iterations` 在详情接口里会被 _run_progress 的“每轮明细”覆盖，
        # 因此计划轮数另外用 planned_iterations 暴露给 UI。
        "planned_iterations": iterations,
        "iterations": iterations,
        "programs": _read_run_generation(run_dir),
        "best_metrics": best_metrics,
        "model": model,
        "updated_at": updated_at,
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
    if not progress.get("iterations"):
        # The log exists but no iteration ever reported: startup failed.
        return "failed"
    total = progress.get("total_started")
    last = progress.get("current_iteration") or 0
    if total and last >= total:
        # Every round reported, but the completion banner never appeared.
        return "interrupted"
    if log_age_seconds is not None and log_age_seconds > STALE_RUN_SECONDS:
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
            Path(os.path.relpath(checkpoint, PROJECT_ROOT)).as_posix() if checkpoint else ""
        ),
    }


def _list_runs():
    """List every GUI-launched evolution run, newest first."""
    if not GUI_RUNS_DIR.is_dir():
        return []
    runs = []
    for path in GUI_RUNS_DIR.iterdir():
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



def load_evolution_data(checkpoint_folder):
    meta_path = os.path.join(checkpoint_folder, "metadata.json")
    programs_dir = os.path.join(checkpoint_folder, "programs")
    if not os.path.exists(meta_path) or not os.path.exists(programs_dir):
        logger.info(f"Missing metadata.json or programs dir in {checkpoint_folder}")
        return {
            "archive": [],
            "nodes": [],
            "edges": [],
            "checkpoint_dir": checkpoint_folder,
            "feature_maps": [],
            "feature_stats": {},
        }
    with open(meta_path) as f:
        meta = json.load(f)

    nodes = []
    id_to_program = {}
    pids = set()
    for island_idx, id_list in enumerate(meta.get("islands", [])):
        for pid in id_list:
            prog_path = os.path.join(programs_dir, f"{pid}.json")

            # Keep track of PIDs and if one is double, append "-copyN" to the PID
            if pid in pids:
                base_pid = pid

                # If base_pid already has a "-copyN" suffix, strip it
                if "-copy" in base_pid:
                    base_pid = base_pid.rsplit("-copy", 1)[0]

                # Find the next available copy number
                copy_num = 1
                while f"{base_pid}-copy{copy_num}" in pids:
                    copy_num += 1
                pid = f"{base_pid}-copy{copy_num}"
            pids.add(pid)

            if os.path.exists(prog_path):
                with open(prog_path) as pf:
                    prog = json.load(pf)
                    sanitize_program_for_visualization(prog)
                # Embedding vectors are large and never rendered; drop them.
                prog.pop("embedding", None)
                prog["id"] = pid
                prog["island"] = island_idx
                nodes.append(prog)
                id_to_program[pid] = prog
            else:
                logger.debug(f"Program file not found: {prog_path}")

    edges = []
    for prog in nodes:
        parent_id = prog.get("parent_id")
        if parent_id and parent_id in id_to_program:
            edges.append({"source": parent_id, "target": prog["id"]})

    logger.info(f"Loaded {len(nodes)} nodes and {len(edges)} edges from {checkpoint_folder}")
    return {
        "archive": meta.get("archive", []),
        "nodes": nodes,
        "edges": edges,
        "checkpoint_dir": checkpoint_folder,
        "feature_maps": _build_feature_maps(meta, id_to_program),
        "feature_stats": meta.get("feature_stats", {}) or {},
    }


def _build_feature_maps(meta: dict, programs: dict) -> list[dict[str, Any]]:
    """Turn the saved MAP-Elites grids into per-island cells for the heat map."""
    stats = meta.get("feature_stats") or {}
    dimensions = [str(name) for name in stats.keys()] if isinstance(stats, dict) else []
    if len(dimensions) < 2:
        default = ["complexity", "diversity"]
        for name in default:
            if name not in dimensions:
                dimensions.append(name)

    feature_maps = []
    for island_idx, grid in enumerate(meta.get("island_feature_maps", []) or []):
        cells = []
        max_x = 0
        max_y = 0
        for key, program_id in (grid or {}).items():
            try:
                bins = [int(part) for part in str(key).split("-")]
            except ValueError:
                continue
            if len(bins) < 2:
                continue
            program = programs.get(program_id) or {}
            fitness = (program.get("metrics") or {}).get("combined_score")
            cells.append(
                {
                    "x": bins[0],
                    "y": bins[1],
                    "program_id": program_id,
                    "fitness": fitness if check_json_float(fitness) else None,
                }
            )
            max_x = max(max_x, bins[0])
            max_y = max(max_y, bins[1])
        feature_maps.append(
            {
                "island": island_idx,
                "dimensions": dimensions[:2],
                "bins_x": max_x + 1 if cells else 0,
                "bins_y": max_y + 1 if cells else 0,
                "cells": cells,
            }
        )
    return feature_maps


def sanitize_program_for_visualization(program: dict[str, Any]) -> None:
    for k, v in program["metrics"].items():
        if not check_json_float(v):
            program["metrics"][k] = None
        if "parent_metrics" in program["metadata"]:
            for k, v in program["metadata"]["parent_metrics"].items():
                if not check_json_float(v):
                    program["metadata"]["parent_metrics"][k] = None


def check_json_float(v: Optional[float]) -> bool:
    return isinstance(v, Number) and not (math.isinf(v) or math.isnan(v))


@app.route("/")
def index():
    return render_template("index.html", checkpoint_dir=checkpoint_dir)


checkpoint_dir = None  # Global variable to store the checkpoint directory


def _is_local_request() -> bool:
    remote_addr = request.remote_addr
    try:
        if remote_addr is None or not ipaddress.ip_address(remote_addr).is_loopback:
            return False
    except ValueError:
        return False

    host = urlsplit(request.host_url).hostname
    try:
        if host != "localhost" and (host is None or not ipaddress.ip_address(host).is_loopback):
            return False
    except ValueError:
        return False

    origin = request.headers.get("Origin")
    return not origin or origin.rstrip("/") == request.host_url.rstrip("/")


def _current_run_status() -> Optional[dict[str, Any]]:
    if current_run is None:
        return None

    process = current_run["process"]
    return_code = process.poll()
    if return_code is not None and current_run["status"] == "running":
        current_run["status"] = "completed" if return_code == 0 else "failed"
        current_run["return_code"] = return_code

    log_path = current_run["log_path"]
    try:
        with open(log_path, "rb") as log_file:
            log_file.seek(max(0, os.path.getsize(log_path) - 16000))
            log = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        log = ""

    project_root = str(PROJECT_ROOT)
    log = log.replace(f"{project_root}{os.sep}", "")

    return {
        "id": current_run["id"],
        "status": current_run["status"],
        "run_dir": Path(os.path.relpath(current_run["run_dir"], PROJECT_ROOT)).as_posix(),
        "output_dir": Path(os.path.relpath(current_run["output_dir"], PROJECT_ROOT)).as_posix(),
        "return_code": current_run.get("return_code"),
        "log": log,
    }


def _template_dirs() -> dict[str, Path]:
    """Map template id -> template directory (any folder holding template.yml)."""
    if not TEMPLATE_DIR.is_dir():
        return {}
    return {
        path.name: path
        for path in sorted(TEMPLATE_DIR.iterdir())
        if (path / "template.yml").is_file()
    }


def _template_summaries() -> list[dict[str, Any]]:
    """Id/name/description for every template, for the template picker."""
    import yaml

    summaries = []
    for template_id, template_dir in _template_dirs().items():
        name = template_id
        description = ""
        try:
            meta = yaml.safe_load((template_dir / "template.yml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            meta = None
        if isinstance(meta, dict):
            name = meta.get("name") or template_id
            description = meta.get("description") or ""
        summaries.append({"id": template_id, "name": name, "description": description})
    return summaries


def _load_solver_evaluator():
    """Import solver/evaluator.py by path, so runs share one evaluator."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("solver_evaluator", SOLVER_EVALUATOR_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载通用评估器：{SOLVER_EVALUATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_template(template_id: Optional[str] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read one task template: metadata, objective/validation and editor contents."""
    import yaml

    template_dirs = _template_dirs()
    if not template_dirs:
        return None, f"没有找到任何任务模板：{TEMPLATE_DIR}"

    selected = template_id if template_id in template_dirs else None
    if selected is None:
        selected = (
            DEFAULT_TEMPLATE_ID if DEFAULT_TEMPLATE_ID in template_dirs else sorted(template_dirs)[0]
        )
    template_dir = template_dirs[selected]

    try:
        global_config = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        meta = yaml.safe_load((template_dir / "template.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        return None, f"无法读取任务模板 {selected}：{error}"
    if not isinstance(meta, dict):
        return None, f"任务模板 {selected} 的 template.yml 必须是键值映射"

    files = meta.get("files") or {}
    names = {
        "initial_program": files.get("initial_program") or "init.py",
        "prompt": files.get("prompt") or "prompt.md",
        "evaluator": files.get("adapter") or "runner_adapter.py",
    }
    contents: dict[str, Any] = {}
    for key, name in names.items():
        try:
            contents[key] = (template_dir / name).read_text(encoding="utf-8")
        except OSError as error:
            return None, f"无法读取任务模板 {selected} 的 {name}：{error}"

    defaults = meta.get("defaults") or {}
    if not isinstance(defaults, dict):
        return None, f"任务模板 {selected} 的 defaults 必须是键值映射"
    iterations = defaults.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        iterations = global_config.get("max_iterations") if isinstance(global_config, dict) else 10

    return {
        "id": selected,
        "name": meta.get("name") or selected,
        "description": meta.get("description") or "",
        "objective": meta.get("objective"),
        "validation": meta.get("validation"),
        "iterations": iterations,
        **contents,
    }, None


@app.route("/launch")
def launch_page():
    if not _is_local_request():
        abort(403)

    defaults, error = _launch_defaults()
    if error:
        return jsonify({"error": error}), 500
    return render_template(
        "launch.html",
        templates=defaults["templates"],
        default_template=defaults["default_template"],
        template_name=defaults["template"]["name"],
        initial_program=defaults["initial_program"],
        evaluator=defaults["evaluator"],
        prompt=defaults["prompt"],
        iterations=defaults["iterations"],
    )


def _launch_defaults(template_id: Optional[str] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    import yaml

    template, error = _load_template(template_id)
    if error:
        return None, error

    if (
        not isinstance(template["iterations"], int)
        or isinstance(template["iterations"], bool)
        or template["iterations"] < 1
    ):
        return None, "全局配置中的 max_iterations 必须是正整数。"

    # Flat keys keep the legacy launch page and older clients working.
    payload = {
        "templates": _template_summaries(),
        "default_template": template["id"],
        "template": template,
    }
    payload.update(
        {
            key: template[key]
            for key in ("initial_program", "evaluator", "prompt", "iterations", "objective", "validation")
        }
    )
    # 可编辑的 YAML 文本：objective 决定怎么算分，validation 决定候选允许怎么改。
    for key in ("objective", "validation"):
        value = template[key]
        payload[f"{key}_yaml"] = (
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False) if value else ""
        )
    return payload, None


@app.route("/api/launch")
def launch_defaults_api():
    if not _is_local_request():
        abort(403)

    defaults, error = _launch_defaults(request.args.get("template"))
    if error:
        return jsonify({"error": error}), 500
    return jsonify(defaults)


def _parse_mapping_field(value: Any, field: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Accept a YAML/JSON string or a mapping for objective/validation overrides."""
    if value is None:
        return None, None
    if isinstance(value, str):
        import yaml

        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as error:
            return None, f"{field} 不是合法的 YAML/JSON：{error}"
    if value == "" or value is None:
        return None, None
    if not isinstance(value, dict):
        return None, f"{field} 必须是键值映射（或 YAML/JSON 文本）。"
    return value, None


@app.route("/api/run", methods=["GET", "POST"])
def run_api():
    global current_run

    if not _is_local_request():
        abort(403)

    if request.method == "GET":
        _reap_finished_runs()
        return jsonify({"run": _current_run_status()})

    if not request.is_json or request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"error": "请从本机进化配置页面提交任务。"}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求内容必须是 JSON 对象。"}), 400

    initial_program = payload.get("initial_program")
    prompt_text = payload.get("prompt")
    evaluator = payload.get("evaluator")
    iterations = payload.get("iterations")
    template_id = payload.get("template")
    task_name = payload.get("task_name")

    if not isinstance(initial_program, str) or not initial_program.strip():
        return jsonify({"error": "请填写初始程序代码。"}), 400
    if not isinstance(prompt_text, str):
        return jsonify({"error": "任务提示词必须是文本。"}), 400
    if not isinstance(evaluator, str) or not evaluator.strip():
        return jsonify({"error": "请填写真实评测脚本代码。"}), 400
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        return jsonify({"error": "进化轮数必须是正整数。"}), 400
    if not prompt_text.strip():
        return jsonify({"error": "任务提示词不能为空。"}), 400
    if template_id is not None and (not isinstance(template_id, str) or template_id not in _template_dirs()):
        return jsonify({"error": f"未知的任务模板：{template_id}"}), 400
    if task_name is not None and not isinstance(task_name, str):
        return jsonify({"error": "任务名称必须是文本。"}), 400
    resume_checkpoint = None
    if payload.get("resume_from") is not None:
        resume_from = payload["resume_from"]
        if not isinstance(resume_from, str) or not resume_from.strip():
            return jsonify({"error": "续跑来源必须是 <任务ID> 或 <任务ID>/<检查点名>。"}), 400
        resume_checkpoint = _resolve_resume_checkpoint(resume_from)
        if resume_checkpoint is None:
            return jsonify({"error": f"找不到可用于续跑的检查点：{resume_from}"}), 400
    import yaml

    # 目标函数与候选校验：优先用页面提交的值，否则用任务模板里的配置。
    template, template_error = _load_template(template_id)
    if template_error:
        return jsonify({"error": template_error}), 500
    objective, error = _parse_mapping_field(payload.get("objective"), "评分配置 objective")
    if error:
        return jsonify({"error": error}), 400
    validation, error = _parse_mapping_field(payload.get("validation"), "校验配置 validation")
    if error:
        return jsonify({"error": error}), 400
    if objective is None:
        objective = template["objective"]
    if validation is None:
        validation = template["validation"]
    try:
        solver_evaluator = _load_solver_evaluator()
        solver_evaluator.validate_objective(objective)
        solver_evaluator.validate_validation(validation)
    except (ImportError, OSError, ValueError) as error:
        return jsonify({"error": f"目标/校验配置无效：{error}"}), 400

    try:
        config = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        logger.exception("Could not load global OpenEvolve configuration")
        return jsonify({"error": f"无法读取全局运行配置：{error}"}), 500
    if not isinstance(config, dict):
        return jsonify({"error": "全局运行配置必须是 YAML 键值映射。"}), 500
    evaluator_config = config.get("evaluator")
    if not isinstance(evaluator_config, dict):
        return jsonify({"error": "全局配置缺少 evaluator 配置段。"}), 500
    evaluator_args = evaluator_config.setdefault("evaluator_args", {})
    if not isinstance(evaluator_args, dict):
        return jsonify({"error": "全局配置 evaluator.evaluator_args 必须是映射。"}), 500
    config["max_iterations"] = iterations
    config["prompt"] = {"template_dir": ".", "system_message": "prompt"}
    # 设置页里配置的模型供应商覆盖全局配置，本次任务立即生效
    _apply_gui_llm_settings(config)
    # 真实 key 只在内存里：写盘时换成 ${OPENEVOLVE_API_KEY} 占位符，由子进程环境变量注入
    llm_section = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    run_api_key = str(llm_section.get("api_key") or "")

    if not run_lock.acquire(blocking=False):
        return jsonify({"error": "正在启动另一个任务，请稍后再试。"}), 409
    try:
        _reap_finished_runs()

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        run_id = f"evolve_task_{timestamp}"
        run_dir = GUI_RUNS_DIR / run_id
        if run_dir.exists():
            run_id = f"{run_id}_{uuid.uuid4().hex[:8]}"
            run_dir = GUI_RUNS_DIR / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        initial_path = input_dir / "init.py"
        evaluator_path = input_dir / "evaluator_callback.py"
        adapter_path = input_dir / "runner_adapter.py"
        config_path = input_dir / "config.yaml"
        prompt_path = input_dir / "prompt.md"
        task_path = input_dir / "task.json"
        task_manifest = {
            "name": (task_name or "").strip() or template["name"],
            "template": template["id"],
            "template_name": template["name"],
            "objective": objective,
            "validation": validation,
            "resumed_from": resume_checkpoint,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            input_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir()
            initial_path.write_text(initial_program, encoding="utf-8")
            evaluator_path.write_text(
                SOLVER_EVALUATOR_PATH.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            adapter_path.write_text(evaluator, encoding="utf-8")
            prompt_path.write_text(prompt_text, encoding="utf-8")
            evaluator_args["runner_command"] = [
                sys.executable,
                str(adapter_path),
                "{program_path}",
            ]
            if objective is not None:
                evaluator_args["objective"] = objective
            if validation is not None:
                evaluator_args["validation"] = validation
            evaluator_args["reference_program_path"] = str(initial_path)
            # 任务目录里的 config.yaml 不含明文 key，只留占位符
            llm_dump = config.get("llm")
            if isinstance(llm_dump, dict) and run_api_key:
                llm_dump["api_key"] = "${OPENEVOLVE_API_KEY}"
            config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
            config_path.write_text(config_text, encoding="utf-8")
            task_path.write_text(
                json.dumps(task_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            logger.exception("Could not write GUI evolution input files")
            return jsonify({"error": f"无法保存任务输入文件：{error}"}), 500

        command = [
            sys.executable,
            str(PROJECT_ROOT / "openevolve-run.py"),
            str(initial_path),
            str(evaluator_path),
            "--iterations",
            str(iterations),
            "--output",
            str(output_dir),
        ]
        command.extend(["--config", str(config_path)])
        if resume_checkpoint:
            command.extend(["--checkpoint", resume_checkpoint])

        log_path = output_dir / "experiment.log"
        child_env = dict(os.environ)
        if run_api_key:
            child_env["OPENEVOLVE_API_KEY"] = run_api_key
        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=child_env,
                    start_new_session=True,
                )
        except OSError as error:
            logger.exception("Could not start GUI evolution run")
            return jsonify({"error": f"无法启动进化任务：{error}"}), 500

        current_run = {
            "id": run_id,
            "status": "running",
            "process": process,
            "run_dir": str(run_dir),
            "output_dir": str(output_dir),
            "log_path": str(log_path),
        }
        active_runs[run_id] = current_run
        return jsonify({"run": _current_run_status()}), 202
    finally:
        run_lock.release()


@app.route("/runs")
def runs_page():
    if not _is_local_request():
        abort(403)
    return render_template("runs.html")


@app.route("/api/runs")
def runs_api():
    if not _is_local_request():
        abort(403)
    _reap_finished_runs()
    return jsonify({"runs": _list_runs()})


def _resolve_run_dir(run_id: str) -> Optional[Path]:
    """Map a run id to its directory, rejecting path traversal."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return None
    run_dir = GUI_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return None
    return run_dir


@app.route("/api/runs/<run_id>")
def run_detail_api(run_id):
    """Full detail for one run: summary + live progress + input copies."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    progress = _run_progress(run_dir)
    detail = _run_summary(run_dir)
    detail.update(progress)
    # progress["iterations"] 是每轮明细数组，会覆盖计划轮数；
    # 这里保留两个语义清晰的键供 UI 使用。
    detail["iteration_details"] = progress["iterations"]

    input_dir = run_dir / "input"
    detail["input_files"] = {
        name: (input_dir / name).is_file()
        for name in _INPUT_FILE_NAMES
    }
    return jsonify({"run": detail})


_INPUT_FILE_NAMES = (
    "init.py",
    "prompt.md",
    "runner_adapter.py",
    "evaluator_callback.py",
    "config.yaml",
    "task.json",
)


@app.route("/api/runs/<run_id>/input/<name>")
def run_input_file_api(run_id, name):
    """Return the text content of one saved input file for preview."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    if name not in _INPUT_FILE_NAMES:
        return jsonify({"error": "不支持的文件。"}), 400
    path = run_dir / "input" / name
    if not path.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500
    truncated = len(text) > 200_000
    if truncated:
        text = text[:200_000]
    return jsonify({"name": name, "size": path.stat().st_size, "truncated": truncated, "content": text})


# 输出文件白名单：显示名 -> 相对于任务目录的实际路径
_OUTPUT_FILES = {
    "best_program.py": ("output", "best", "best_program.py"),
    "best_program_info.json": ("output", "best", "best_program_info.json"),
}


@app.route("/api/runs/<run_id>/output/<name>")
def run_output_file_api(run_id, name):
    """Return the text content of one saved output file (best program etc.)."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    if name not in _OUTPUT_FILES:
        return jsonify({"error": "不支持的文件。"}), 400
    path = run_dir.joinpath(*_OUTPUT_FILES[name])
    if not path.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500
    truncated = len(text) > 200_000
    if truncated:
        text = text[:200_000]
    return jsonify({"name": name, "size": path.stat().st_size, "truncated": truncated, "content": text})


_MAX_TREE_ITEMS = 200


def _build_file_tree(root, relative, limit=_MAX_TREE_ITEMS):
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


@app.route("/api/runs/<run_id>/files")
def run_files_api(run_id):
    """List a run's input / output / checkpoint files as a tree for browsing."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    sections = []
    # 显示名 / 实际目录 / 读取文件时使用的相对前缀
    for name, base, prefix in (
        ("input", run_dir / "input", "input"),
        ("output", run_dir / "output", "output"),
        ("checkpoints", run_dir / "output" / "checkpoints", "output/checkpoints"),
    ):
        if not base.is_dir():
            continue
        children = _build_file_tree(base, prefix.split("/"))
        if name == "output":
            # checkpoints 单独列为一段，避免重复
            children = [item for item in children if item["name"] != "checkpoints"]
        sections.append({"type": "dir", "name": name, "path": prefix, "children": children})

    return jsonify({
        "run_id": run_id,
        "run_dir": Path(os.path.relpath(run_dir, PROJECT_ROOT)).as_posix(),
        "tree": sections,
    })


@app.route("/api/runs/<run_id>/file")
def run_file_api(run_id):
    """Read one file inside the run folder. `path` must stay inside run_dir."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    relative = request.args.get("path", "")
    if not relative:
        return jsonify({"error": "缺少 path 参数。"}), 400
    base = run_dir.resolve()
    target = (run_dir / relative).resolve()
    if str(target) != str(base) and not str(target).startswith(str(base) + os.sep):
        return jsonify({"error": "路径不合法。"}), 400
    if not target.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        raw = target.read_bytes()
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500

    truncated = len(raw) > 300_000
    body = raw[:300_000] if truncated else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "该文件不是 UTF-8 文本文件（可能是二进制产物）。", "binary": True}), 415

    return jsonify({
        "name": target.name,
        "path": relative,
        "size": len(raw),
        "truncated": truncated,
        "content": text,
    })


@app.route("/api/runs/<run_id>/program/<program_id>")
def run_program_api(run_id, program_id):
    """Find one program across every checkpoint of a run.

    Programs from early iterations often disappear from later checkpoints, so the
    program page needs a lookup that is not limited to the newest checkpoint.
    """
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    root = _checkpoints_root(run_dir)
    if not root.is_dir():
        return jsonify({"error": "该任务还没有 checkpoint。"}), 404

    def checkpoint_number(path):
        try:
            return int(path.name.rsplit("_", 1)[-1])
        except (IndexError, ValueError):
            return -1

    folders = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith("checkpoint_")),
        key=checkpoint_number,
        reverse=True,
    )
    for folder in folders:
        try:
            data = load_evolution_data(str(folder))
        except (OSError, ValueError):
            continue
        match = next((node for node in data.get("nodes", []) if node.get("id") == program_id), None)
        if match is None:
            continue
        parent = next((node for node in data.get("nodes", []) if node.get("id") == match.get("parent_id")), None)
        return jsonify({
            "run_id": run_id,
            "checkpoint": folder.name,
            "node": match,
            "parent": parent,
            "archive": data.get("archive", []),
            "checkpoints": _list_checkpoints(run_dir),
        })

    return jsonify({"error": f"在所有检查点里都没有找到程序：{program_id}"}), 404


GUI_SETTINGS_PATH = GUI_RUNS_DIR / "gui_settings.json"
# 密钥单独存放（600 权限、已在 .gitignore 里），不写进任何配置/提交文件
GUI_SECRETS_PATH = GUI_RUNS_DIR / "llm_secrets.json"


def _load_secrets() -> dict[str, Any]:
    if not GUI_SECRETS_PATH.is_file():
        return {}
    try:
        data = json.loads(GUI_SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_secrets(data: dict[str, Any]) -> None:
    GUI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    GUI_SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(GUI_SECRETS_PATH, 0o600)
    except OSError:
        pass


def _current_api_key() -> str:
    """优先级：环境变量 OPENEVOLVE_API_KEY > 本地 secrets 文件。"""
    env_key = (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
    if env_key:
        return env_key
    return str(_load_secrets().get("api_key") or "")


def _migrate_legacy_secrets() -> None:
    """把旧版明文 api_key / 多模型配置收敛成「单模型 + 独立 secrets 文件」。"""
    secrets = _load_secrets()
    changed = False

    # 旧版多模型密钥 {模型名: key} -> 单个 api_key
    legacy_keys = secrets.pop("models", None)
    if isinstance(legacy_keys, dict) and legacy_keys:
        first_key = next((str(v) for v in legacy_keys.values() if isinstance(v, str) and v), "")
        if first_key:
            secrets["api_key"] = first_key
        changed = True

    settings = _load_gui_settings()
    llm = settings.get("llm")
    if isinstance(llm, dict):
        if llm.get("api_key"):
            secrets.setdefault("api_key", llm["api_key"])
            llm.pop("api_key", None)
            changed = True
        # 旧版多模型列表 -> 单模型字段（取第一个可用模型）
        raw_models = llm.pop("models", None)
        if isinstance(raw_models, list) and raw_models:
            changed = True
            first = next(
                (
                    item
                    for item in raw_models
                    if isinstance(item, dict) and (item.get("name") or item.get("api_base"))
                ),
                None,
            )
            if isinstance(first, dict):
                for source, target in (
                    ("provider", "provider"),
                    ("api_base", "api_base"),
                    ("name", "primary_model"),
                ):
                    value = str(first.get(source) or "").strip()
                    if value and not llm.get(target):
                        llm[target] = value
        if changed:
            try:
                _save_gui_settings(settings)
            except OSError:
                pass

    try:
        import yaml

        config = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 迁移失败不应阻止启动
        config = None
    if isinstance(config, dict):
        llm_config = config.get("llm")
        if isinstance(llm_config, dict) and llm_config.get("api_key"):
            secrets.setdefault("api_key", llm_config["api_key"])
            llm_config["api_key"] = ""
            try:
                import yaml

                GLOBAL_CONFIG_PATH.write_text(
                    yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
                )
                changed = True
            except OSError:
                pass

    if changed and secrets.get("api_key"):
        _save_secrets(secrets)


def _load_gui_settings() -> dict[str, Any]:
    if not GUI_SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(GUI_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_gui_settings(data: dict[str, Any]) -> None:
    GUI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    GUI_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _mask_secret(value: str) -> str:
    value = str(value or "")
    return f"{value[:4]}…{value[-4:]}" if len(value) > 8 else "*" * len(value)


def _public_settings() -> dict[str, Any]:
    """单模型设置。key 只回掩码；env（.env.local / export）优先于本地 secrets。"""
    data = _load_gui_settings()
    llm = dict(data.get("llm") or {})
    llm.pop("api_key", None)
    llm.pop("models", None)

    env_base = (os.environ.get("OPENEVOLVE_API_BASE") or "").strip()
    env_model = (os.environ.get("OPENEVOLVE_PRIMARY_MODEL") or "").strip()
    if env_base:
        llm["api_base"] = env_base
    if env_model:
        llm["primary_model"] = env_model

    key = _current_api_key()
    llm["has_api_key"] = bool(key)
    llm["api_key_masked"] = _mask_secret(key)
    llm["api_key_source"] = (
        "env" if (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
        else ("secrets" if key else "")
    )
    llm["env_file"] = (PROJECT_ROOT / ".env.local").is_file()
    return {"theme": data.get("theme", "light"), "llm": llm}


@app.route("/api/settings", methods=["GET", "POST"])
def gui_settings_api():
    """Read or update GUI preferences: theme + the single LLM model used by new runs."""
    if not _is_local_request():
        abort(403)
    if request.method == "GET":
        return jsonify(_public_settings())

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求内容必须是 JSON 对象。"}), 400

    current = _load_gui_settings()
    llm = dict(current.get("llm") or {})
    llm.pop("api_key", None)
    # 单模型模式：历史多模型列表不再保留
    llm.pop("models", None)
    incoming = payload.get("llm")
    if isinstance(incoming, dict):
        for key in ("provider", "api_base", "primary_model"):
            if isinstance(incoming.get(key), str):
                llm[key] = incoming[key].strip()
        # Key 只写独立 secrets 文件，不进入任何配置
        api_key = incoming.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            secrets = _load_secrets()
            secrets["api_key"] = api_key.strip()
            secrets.pop("models", None)
            _save_secrets(secrets)
        if incoming.get("clear_api_key"):
            secrets = _load_secrets()
            secrets["api_key"] = ""
            _save_secrets(secrets)
        for key, cast in (("temperature", float), ("max_tokens", int)):
            if key in incoming:
                value = incoming.get(key)
                if value in ("", None):
                    llm.pop(key, None)
                    continue
                try:
                    llm[key] = cast(value)
                except (TypeError, ValueError):
                    return jsonify({"error": f"{key} 不是合法数值。"}), 400

    theme = payload.get("theme")
    if theme in ("light", "dark"):
        current["theme"] = theme
    current["llm"] = llm
    try:
        _save_gui_settings(current)
    except OSError as error:
        logger.exception("Could not save GUI settings")
        return jsonify({"error": f"保存失败：{error}"}), 500
    return jsonify(_public_settings())


def _apply_gui_llm_settings(config: dict[str, Any]) -> None:
    """把设置页里的单模型配置写进本次任务的 llm 段。

    真实 key 不回写到 config 文件：写盘前会被换成 ${OPENEVOLVE_API_KEY} 占位符，
    由子进程环境变量注入。
    """
    llm = config.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        config["llm"] = llm

    # 单模型模式：清掉历史多模型写法，否则 llm.models 会盖过 primary_model
    llm.pop("models", None)
    llm.pop("evaluator_models", None)
    llm.pop("secondary_model", None)

    settings = _load_gui_settings().get("llm") or {}
    for key in ("provider", "api_base", "primary_model"):
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            llm[key] = value.strip()

    api_key = _current_api_key()
    if api_key:
        llm["api_key"] = api_key

    for key in ("temperature", "max_tokens"):
        value = settings.get(key)
        if value not in ("", None):
            llm[key] = value


@app.route("/api/runs/<run_id>/log")
def run_log_api(run_id):
    """Tail a run's console log. Pass ?offset=N to get only bytes after N."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    log_path = run_dir / "output" / "experiment.log"
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return jsonify({"run_id": run_id, "offset": 0, "size": 0, "log": "", "eof": True})

    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "offset 必须是整数。"}), 400
    offset = max(0, min(offset, size))
    max_bytes = 256 * 1024
    with open(log_path, "rb") as log_file:
        log_file.seek(offset)
        chunk = log_file.read(max_bytes)
    return jsonify(
        {
            "run_id": run_id,
            "offset": offset + len(chunk),
            "size": size,
            "log": chunk.decode("utf-8", errors="replace"),
            "eof": offset + len(chunk) >= size,
        }
    )


@app.route("/api/runs/<run_id>/progress")
def run_progress_api(run_id):
    """Lightweight polling endpoint for the live progress of one run."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    return jsonify({"run_id": run_id, **_run_progress(run_dir)})


@app.route("/api/runs/<run_id>/checkpoints")
def run_checkpoints_api(run_id):
    """List the checkpoints of one run so the UI can switch between them."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    return jsonify({"run_id": run_id, "checkpoints": _list_checkpoints(run_dir)})


@app.route("/api/runs/<run_id>/cancel", methods=["POST"])
def run_cancel_api(run_id):
    """Terminate a running task started from this service."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    _reap_finished_runs()
    run = active_runs.get(run_id)
    if run is None and current_run is not None and current_run.get("id") == run_id:
        run = current_run
    if run is None:
        return jsonify({"error": "该任务不是由本服务启动，或已经结束。"}), 409

    process = run["process"]
    if process.poll() is not None:
        return jsonify({"error": "该任务已经结束。"}), 409

    try:
        # The child was started with start_new_session=True, so kill the group.
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as error:
        logger.warning("Could not terminate run %s: %s", run_id, error)
        return jsonify({"error": f"无法终止任务：{error}"}), 500

    run["status"] = "interrupted"
    logger.info("Cancelled evolution run %s", run_id)
    return jsonify({"run": _current_run_status()})


@app.route("/api/runs/<run_id>/export", methods=["POST"])
def run_export_api(run_id):
    """Write a self-contained static copy of the visualization for one run."""
    import argparse

    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    source = str(run_dir / "output")
    checkpoint_name = ""
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and isinstance(payload.get("checkpoint"), str):
            checkpoint_name = payload["checkpoint"].strip()
    if not checkpoint_name:
        checkpoint_name = (request.args.get("checkpoint") or "").strip()
    if checkpoint_name:
        resolved = _resolve_checkpoint(run_dir, checkpoint_name)
        if resolved is None:
            return jsonify({"error": f"找不到检查点：{checkpoint_name}"}), 404
        source = str(resolved)

    static_dir = run_dir / "static"
    try:
        run_static_export(argparse.Namespace(static_output=str(static_dir), path=source))
    except (OSError, RuntimeError, ValueError) as error:
        logger.exception("Could not export static visualization")
        return jsonify({"error": f"静态导出失败：{error}"}), 500

    return jsonify(
        {
            "run_id": run_id,
            "static_dir": str(static_dir),
            "index": str(static_dir / "index.html"),
            "checkpoint": Path(source).name if checkpoint_name else "",
        }
    )


def _resolve_run_output(run_id: str) -> Optional[str]:
    """Map a run id from /api/runs to its output dir, rejecting path traversal."""
    run_dir = _resolve_run_dir(run_id)
    return str(run_dir / "output") if run_dir is not None else None


@app.route("/api/data")
def data():
    global checkpoint_dir
    run_id = request.args.get("run_id", "").strip()
    checkpoint_name = request.args.get("checkpoint", "").strip()
    run_dir = None
    if run_id:
        run_dir = _resolve_run_dir(run_id)
        if run_dir is None:
            return jsonify({"error": f"找不到任务：{run_id}"}), 404
        base_folder = str(run_dir / "output")
    else:
        base_folder = (
            current_run["output_dir"]
            if current_run is not None
            else os.environ.get("EVOLVE_OUTPUT", "examples/")
        )

    selected = None
    if run_dir is not None and checkpoint_name:
        selected = _resolve_checkpoint(run_dir, checkpoint_name)
        if selected is None:
            return jsonify({"error": f"找不到检查点：{checkpoint_name}"}), 404

    checkpoint_dir = str(selected) if selected is not None else find_latest_checkpoint(base_folder)
    if not checkpoint_dir:
        logger.info(f"No checkpoints found in {base_folder}")
        payload = {"archive": [], "nodes": [], "edges": [], "checkpoint_dir": ""}
        if run_dir is not None:
            payload["checkpoints"] = _list_checkpoints(run_dir)
        return jsonify(payload)

    logger.info(f"Loading data from checkpoint: {checkpoint_dir}")
    data = load_evolution_data(checkpoint_dir)
    logger.debug(f"Data: {data}")
    if run_dir is not None:
        data["checkpoints"] = _list_checkpoints(run_dir)
        resolved_root = str(_checkpoints_root(run_dir).resolve())
        resolved_dir = str(Path(checkpoint_dir).resolve()) if checkpoint_dir else ""
        data["selected_checkpoint"] = (
            Path(checkpoint_dir).name if resolved_dir.startswith(resolved_root) else ""
        )
    return jsonify(data)


@app.route("/program/<program_id>")
def program_page(program_id):
    global checkpoint_dir
    run_id = request.args.get("run_id", "").strip()
    if run_id:
        base_folder = _resolve_run_output(run_id)
        if base_folder is None:
            return f"找不到任务：{run_id}", 404
        resolved = find_latest_checkpoint(base_folder)
        if resolved is None:
            return f"任务 {run_id} 还没有 checkpoint", 404
        checkpoint_dir = resolved
    if checkpoint_dir is None:
        return "No checkpoint loaded", 500

    data = load_evolution_data(checkpoint_dir)
    program_data = next((p for p in data["nodes"] if p["id"] == program_id), None)
    program_data = {"code": "", "prompts": {}, **(program_data or {})}
    artifacts_json = program_data.get("artifacts_json", None)

    return render_template(
        "program_page.html",
        program_data=program_data,
        checkpoint_dir=checkpoint_dir,
        artifacts_json=artifacts_json,
    )


def run_static_export(args):
    output_dir = args.static_output
    os.makedirs(output_dir, exist_ok=True)

    # Load data and prepare JSON string
    checkpoint_dir = find_latest_checkpoint(args.path)
    if not checkpoint_dir:
        raise RuntimeError(f"No checkpoint found in {args.path}")
    data = load_evolution_data(checkpoint_dir)
    logger.info(f"Exporting visualization for checkpoint: {checkpoint_dir}")

    with app.app_context():
        data_json = jsonify(data).get_data(as_text=True)
    inlined = f"<script>window.STATIC_DATA = {data_json};</script>"

    # Load index.html template
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    template_path = os.path.join(templates_dir, "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Insert static json data into the HTML
    html = _re.sub(r"\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}", r"static/\1", html)
    script_tag_idx = html.find('<script type="module"')

    if script_tag_idx != -1:
        html = html[:script_tag_idx] + inlined + "\n" + html[script_tag_idx:]
    else:
        html = html.replace("</body>", inlined + "\n</body>")

    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # Copy over static files
    static_src = os.path.join(os.path.dirname(__file__), "static")
    static_dst = os.path.join(output_dir, "static")
    if os.path.exists(static_dst):
        shutil.rmtree(static_dst)
    shutil.copytree(static_src, static_dst)

    logger.info(
        f"Static export written to {output_dir}/\n"
        f"Note: use a web server, not file://. "
        f"Try: python3 -m http.server --directory {output_dir} 8080"
    )


# Manual mode blueprint mounted at /manual
app.register_blueprint(
    create_manual_blueprint(
        lambda: (
            current_run["output_dir"]
            if current_run is not None
            else os.environ.get("EVOLVE_OUTPUT", "examples/")
        )
    )
)


if __name__ == "__main__":
    import argparse

    # 旧版把 api_key 明文写在 gui_settings/global_config 里，启动时搬到独立 secrets 文件
    _migrate_legacy_secrets()

    parser = argparse.ArgumentParser(description="OpenEvolve Evolution Visualizer")
    parser.add_argument(
        "--path",
        type=str,
        default="examples/",
        help="Path to OpenEvolve run output directory (e.g. openevolve_output) or its checkpoints/checkpoint_*.",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode (enables the interactive debugger; local use only)",
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    parser.add_argument(
        "--static-output",
        type=str,
        default=None,
        help="Produce a static HTML export in this directory and exit.",
    )
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    logger.info(f"Current working directory: {os.getcwd()}")

    if args.static_output:
        run_static_export(args)

    os.environ["EVOLVE_OUTPUT"] = args.path
    logger.info(
        f"Starting server at http://{args.host}:{args.port} with log level {args.log_level.upper()}"
    )
    logger.info(f"Manual UI: http://{args.host}:{args.port}/manual")
    app.run(host=args.host, port=args.port, debug=args.debug)

"""运行时的可变全局状态（纯模块，不 import visualizer / backend.core / backend.app）。

任务注册表只能单 worker：内存里的 Popen 句柄无法跨进程共享。为了在服务重启后
仍能认出「哪些任务是自己起的、是否还活着」，注册表同时落盘到
``evolve_tasks/active_runs.json``——内存里存进程句柄，落盘只存 pid 与目录。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from typing import Any, Optional

from . import paths

logger = logging.getLogger(__name__)

current_run: Optional[dict[str, Any]] = None
run_lock = threading.Lock()
# Every task started from this service, keyed by run id.
active_runs: dict[str, dict[str, Any]] = {}
# active_runs 与落盘文件的读写都走这把锁（可重入，方便内部嵌套 flush）
_registry_lock = threading.RLock()


def _serialize(run: dict[str, Any]) -> dict[str, Any]:
    process = run.get("process")
    pid = run.get("pid")
    if not isinstance(pid, int):
        pid = getattr(process, "pid", None) if process is not None else None
    if not isinstance(pid, int):
        pid = None
    return {
        "pid": pid,
        "run_dir": run.get("run_dir"),
        "output_dir": run.get("output_dir"),
        "log_path": run.get("log_path"),
        "status": run.get("status", "running"),
        "started_at": run.get("started_at"),
    }


def flush_registry() -> None:
    """把注册表写到磁盘；失败只记日志，不影响任务本身。"""
    with _registry_lock:
        payload = {run_id: _serialize(run) for run_id, run in active_runs.items()}
    try:
        paths.GUI_ACTIVE_RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        paths.GUI_ACTIVE_RUNS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        # 注册表是辅助信息，写不进去也不能影响任务本身
        logger.warning("Could not persist the run registry", exc_info=True)


def load_persisted_runs() -> dict[str, Any]:
    if not paths.GUI_ACTIVE_RUNS_PATH.is_file():
        return {}
    try:
        data = json.loads(paths.GUI_ACTIVE_RUNS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def register_run(run_id: str, run: dict[str, Any]) -> None:
    with _registry_lock:
        active_runs[run_id] = run
    flush_registry()


def forget_run(run_id: str) -> Optional[dict[str, Any]]:
    with _registry_lock:
        run = active_runs.pop(run_id, None)
    if run is not None:
        flush_registry()
    return run


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    with _registry_lock:
        return active_runs.get(run_id)


def snapshot() -> list[tuple[str, dict[str, Any]]]:
    """线程安全地复制一份 (run_id, run) 列表，供遍历时增删。"""
    with _registry_lock:
        return list(active_runs.items())


def _pid_stat(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def pid_alive(pid: Any) -> bool:
    """进程是否还活着；僵尸进程（已退出待回收）一律算作已退出。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _pid_stat(pid).upper().startswith("Z")


def pid_runs_task(pid: Any, run_id: str) -> bool:
    """确认 pid 仍是该任务的演化进程，避免 PID 复用后误杀无关进程。"""
    if not pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        # 拿不到命令行时退化成「存活即认定」
        return True
    command = (result.stdout or "").strip()
    return bool(command) and str(run_id) in command


def restore_registry() -> None:
    """服务启动时把落盘注册表装回内存：活着的进程可继续被终止，死掉的条目清掉。"""
    persisted = load_persisted_runs()
    restored: dict[str, dict[str, Any]] = {}
    for run_id, entry in persisted.items():
        if not isinstance(entry, dict):
            continue
        if not pid_runs_task(entry.get("pid"), str(run_id)):
            continue
        restored[str(run_id)] = {**entry, "process": None}
    with _registry_lock:
        active_runs.clear()
        active_runs.update(restored)
    if restored or persisted:
        flush_registry()
        logger.info(
            "Run registry restored: %d alive / %d persisted", len(restored), len(persisted)
        )

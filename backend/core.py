"""把 ``scripts/visualizer.py`` 的 Flask 应用与任务工具接进来（不修改该文件）。

visualizer 里已经有完整的路由实现（/api/run、/api/runs/<id>/progress、文件预览等），
后端服务直接复用它们，只额外提供任务视角的接口。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import visualizer  # noqa: E402  （需要先把 scripts 目录放进 sys.path）

viz = visualizer


def _live_updates() -> dict[str, dict[str, Any]]:
    """内存里正在跑的任务：PID 与退出码比日志更即时。"""
    return {
        run_id: {
            "status": run.get("status"),
            "return_code": run.get("return_code"),
            "pid": getattr(run.get("process"), "pid", None),
        }
        for run_id, run in viz.active_runs.items()
    }


def job_rows() -> list[dict[str, Any]]:
    viz._reap_finished_runs()
    live = _live_updates()
    rows: list[dict[str, Any]] = []
    for summary in viz._list_runs():
        row = dict(summary)
        row.update(live.get(row.get("id"), {}))
        rows.append(row)
    return rows


def job_row(job_id: str) -> Optional[dict[str, Any]]:
    for row in job_rows():
        if row.get("id") == job_id:
            return row
    return None

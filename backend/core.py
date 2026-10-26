"""把 ``scripts/visualizer.py`` 的 Flask 应用与任务工具接进来。

visualizer 里保留上游的路由实现（/、/api/data、/program/<id>、/api/runs/<id>/export
以及 manual blueprint），后端服务直接复用它们，团队新增的接口挂在 backend 下的
blueprint 上。这里额外提供任务视角的查询工具。
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

from . import state  # noqa: E402
from .runs import _list_runs, _reap_finished_runs  # noqa: E402

viz = visualizer


def _live_updates() -> dict[str, dict[str, Any]]:
    """内存里正在跑的任务：PID 与退出码比日志更即时。"""
    return {
        run_id: {
            "status": run.get("status"),
            "return_code": run.get("return_code"),
            "pid": getattr(run.get("process"), "pid", None),
        }
        for run_id, run in state.active_runs.items()
    }


def job_rows() -> list[dict[str, Any]]:
    _reap_finished_runs()
    live = _live_updates()
    rows: list[dict[str, Any]] = []
    for summary in _list_runs():
        row = dict(summary)
        row.update(live.get(row.get("id"), {}))
        rows.append(row)
    return rows


def job_row(job_id: str) -> Optional[dict[str, Any]]:
    for row in job_rows():
        if row.get("id") == job_id:
            return row
    return None

"""WSGI 入口：``backend.server:app``（给 gunicorn 用）。

必须单 worker：任务注册表（``current_run`` / ``active_runs``）在进程内存里，
多 worker 会各记各的，前端查进度就会串台。并发靠线程。
"""

from __future__ import annotations

import logging
import os

from . import state
from .app import create_app
from .secrets import _migrate_legacy_secrets

logging.basicConfig(
    level=os.environ.get("OPENEVOLVE_LOG_LEVEL", "INFO"),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)

# 与 visualizer 的启动流程保持一致：把历史明文密钥收敛到独立 secrets 文件
_migrate_legacy_secrets()

# 重启后重新接管上一次遗留的运行中任务（能查进度、能取消）
state.restore_registry()

app = create_app()

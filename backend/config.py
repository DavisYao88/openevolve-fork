"""后端服务的运行参数（端口、线程、日志、PID 文件）。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HOST = os.environ.get("OPENEVOLVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENEVOLVE_PORT", "8080"))

# 演化任务跑在独立子进程里（start_new_session），服务重启不影响正在跑的任务；
# 但任务注册表在进程内存里，所以必须单 worker，靠线程扛并发。
WORKERS = 1
THREADS = int(os.environ.get("OPENEVOLVE_THREADS", "8"))

LOG_FILE = os.environ.get("OPENEVOLVE_LOG", "/tmp/openevolve-backend.log")
PID_FILE = os.environ.get("OPENEVOLVE_PID", "/tmp/openevolve-backend.pid")

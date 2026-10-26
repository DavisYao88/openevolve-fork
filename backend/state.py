"""运行时的可变全局状态（纯模块，不 import visualizer / backend.core / backend.app）。

任务注册表在进程内存里，所以后端必须单 worker；并发靠线程。
"""

from __future__ import annotations

import threading
from typing import Any, Optional

current_run: Optional[dict[str, Any]] = None
run_lock = threading.Lock()
# Every task started from this service, keyed by run id. Tasks may run
# concurrently; the registry is only used to terminate them on request.
active_runs: dict[str, dict[str, Any]] = {}

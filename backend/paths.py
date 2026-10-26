"""路径与常量（纯模块，不 import visualizer / backend.core / backend.app）。

这些值原先写在 ``scripts/visualizer.py`` 顶部，搬到这里后由其它 backend 模块
以及瘦身后的 visualizer.py 共享。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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
# 评测子进程用的解释器：任务的生产依赖常常装在别的 venv（例如生产模型工程自带 .venv），
# 用 OPENEVOLVE_RUNNER_PYTHON 指向它；默认与可视化器本身一致。
RUNNER_PYTHON = (os.environ.get("OPENEVOLVE_RUNNER_PYTHON") or "").strip() or sys.executable
# 公共演化求解器：通用评估器 + 任务模板库（模板只描述目标与约束，不含评估器）。
SOLVER_DIR = PROJECT_ROOT / "solver"
SOLVER_EVALUATOR_PATH = SOLVER_DIR / "evaluator.py"
TEMPLATE_DIR = SOLVER_DIR / "templates"
DEFAULT_TEMPLATE_ID = "famou-调参-v1"
GLOBAL_CONFIG_PATH = GUI_RUNS_DIR / "global_config.yml"
GUI_SETTINGS_PATH = GUI_RUNS_DIR / "gui_settings.json"
# 运行中任务的注册表快照（pid + 目录）：服务重启后靠它对账，找回失控的子进程
GUI_ACTIVE_RUNS_PATH = GUI_RUNS_DIR / "active_runs.json"
# 密钥单独存放（600 权限、已在 .gitignore 里），不写进任何配置/提交文件
GUI_SECRETS_PATH = GUI_RUNS_DIR / "llm_secrets.json"
# A run whose log has not grown for this long is treated as interrupted rather
# than running, so a killed process does not look alive forever.
STALE_RUN_SECONDS = 15 * 60

# 任务输入文件白名单：新建任务时写入这些文件，详情页据此展示。
_INPUT_FILE_NAMES = (
    "init.py",
    "prompt.md",
    "runner_adapter.py",
    "evaluator_callback.py",
    "config.yaml",
    "task.json",
)

# 输出文件白名单：显示名 -> 相对于任务目录的实际路径
_OUTPUT_FILES = {
    "best_program.py": ("output", "best", "best_program.py"),
    "best_program_info.json": ("output", "best", "best_program_info.json"),
}

_MAX_TREE_ITEMS = 200

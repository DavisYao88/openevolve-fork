"""通用程序演化评测脚本：导入候选程序，调用 solve() 并把返回的指标打印成 JSON。

用法（求解器按此调用，日志写 stderr，stdout 只输出一个 JSON 对象）：

    python runner_adapter.py /absolute/path/to/candidate_program.py
    {"score": -0.42}

接入真实任务时把 `solve()` 换成自己的入口即可：候选程序是完整可运行的程序，
评测脚本负责"跑它、测量它、把指标打印出来"。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

ENTRYPOINT = "solve"


def load_candidate(candidate_path: Path):
    spec = importlib.util.spec_from_file_location("candidate_program", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure(module) -> Dict[str, Any]:
    entrypoint = getattr(module, ENTRYPOINT, None)
    if not callable(entrypoint):
        raise SystemExit(f"候选程序必须定义 {ENTRYPOINT}()：{module.__file__}")
    result = entrypoint()
    if not isinstance(result, dict):
        raise SystemExit(f"{ENTRYPOINT}() 必须返回指标字典，实际为 {type(result).__name__}")
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：python runner_adapter.py <候选程序路径>")
    candidate_path = Path(sys.argv[1]).expanduser().resolve()
    if not candidate_path.is_file():
        raise SystemExit(f"候选文件不存在：{candidate_path}")

    module = load_candidate(candidate_path)
    print(f"[adapter] candidate={candidate_path}", file=sys.stderr)
    print(json.dumps(measure(module), ensure_ascii=False))


if __name__ == "__main__":
    main()

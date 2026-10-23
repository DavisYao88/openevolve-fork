"""通用参数调优评测脚本：读取候选 PARAMS，跑固定的优化过程并输出指标 JSON。

用法（求解器按此调用，日志写 stderr，stdout 只输出一个 JSON 对象）：

    python runner_adapter.py /absolute/path/to/candidate_init.py
    {"loss": 0.0031}

接入真实任务时只需要替换 `run_task()`：把"固定算法 + 候选参数 → 指标"换成
自己的训练/仿真/求解链路，指标名与 template.yml 的 objective.metrics 对齐。
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict

# 固定的评测问题：最小化 sum(w_i * (x_i - t_i)^2)，条件数很大，对步长敏感。
TARGET = [1.0, -2.0, 3.0]
WEIGHTS = [1.0, 10.0, 100.0]
STEPS = 300


def load_parameters(candidate_path: Path) -> Dict[str, Any]:
    """Import the candidate file and return its PARAMS dict."""
    spec = importlib.util.spec_from_file_location("_candidate_params", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = getattr(module, "PARAMS", None)
    if not isinstance(params, dict):
        raise SystemExit(f"候选文件没有 PARAMS 字典：{candidate_path}")
    return params


def run_task(params: Dict[str, Any]) -> Dict[str, float]:
    """Run the fixed optimization chain with the candidate parameters."""
    step_size = float(params["step_size"])
    momentum = float(params["momentum"])
    restarts = int(params["restarts"])

    rng = random.Random(20261023)
    best = math.inf
    for restart in range(restarts):
        position = [rng.uniform(-5.0, 5.0) for _ in TARGET]
        velocity = [0.0 for _ in TARGET]
        for _ in range(STEPS):
            gradient = [2.0 * w * (x - t) for x, t, w in zip(position, TARGET, WEIGHTS)]
            velocity = [momentum * v - step_size * g for v, g in zip(velocity, gradient)]
            position = [x + v for x, v in zip(position, velocity)]
            if not all(math.isfinite(value) for value in position):
                break
        loss = sum(w * (x - t) ** 2 for x, t, w in zip(position, TARGET, WEIGHTS))
        print(f"[adapter] restart={restart} loss={loss!r}", file=sys.stderr)
        best = min(best, loss)

    if not math.isfinite(best):
        raise SystemExit(f"参数导致发散：{params}")
    return {"loss": float(best)}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：python runner_adapter.py <候选 init.py 路径>")
    candidate_path = Path(sys.argv[1]).expanduser().resolve()
    if not candidate_path.is_file():
        raise SystemExit(f"候选文件不存在：{candidate_path}")

    params = load_parameters(candidate_path)
    print(f"[adapter] params={json.dumps(params, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    print(json.dumps(run_task(params), ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Command-line adapter between OpenEvolve and the production training pipeline.

OpenEvolve invokes this file once per candidate:

    python runner_adapter.py /absolute/path/to/candidate_init.py

Your job: load ``PARAMS`` from that candidate file, run the real fixed training
pipeline with those parameters, and print exactly one JSON object on stdout:

    {"wmape": 0.12, "bias_ratio": -0.02}

Rules this adapter deliberately keeps:
  * It never scores anything itself -- solver/evaluator.py turns the returned
    metrics into combined_score according to the task's objective config.
  * It never invents metrics. If training cannot run, exit non-zero with the
    reason on stderr; the evaluator turns that into an explicit 0 with artifacts.
  * Training logs go to stderr. Only the final JSON goes to stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Integration point: point this at the production project.
# ---------------------------------------------------------------------------

# Directory containing the production package `app.pipelines.train_pipeline`.
# Set FAMOU_PRODUCTION_ROOT, or hard-code the real path here.
PRODUCTION_ROOT = Path(
    os.environ.get("FAMOU_PRODUCTION_ROOT", "/absolute/path/to/production/project")
).expanduser()

# Add it to sys.path so `from app.pipelines.train_pipeline import run_train` works.
if PRODUCTION_ROOT.is_dir() and str(PRODUCTION_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCTION_ROOT))


def load_parameters(candidate_path: Path) -> Dict[str, Any]:
    """Import the candidate file and return its PARAMS dict.

    The candidate is a standalone module with a single top-level PARAMS literal,
    so a normal import is enough. ``describe()`` is intentionally ignored: it is
    for humans, never for scoring.
    """
    spec = importlib.util.spec_from_file_location("_candidate_params", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = getattr(module, "PARAMS", None)
    if not isinstance(params, dict):
        raise SystemExit(f"候选文件没有 PARAMS 字典：{candidate_path}")
    return params


def run_training(params: Dict[str, Any]) -> Dict[str, Any]:
    """Run the production training chain and return its summary.

    Replace the two lines below with the real call. The expected shape is
    whatever the production pipeline returns; only ``wmape`` and ``bias_ratio``
    are read by summarize_metrics(), so extra keys are fine.

    Example (shape only -- this repository does not ship the implementation):

        from app.pipelines.train_pipeline import run_train
        return run_train(params=params, market="DOMESTIC_MARKET",
                         target="future_1d_box_qty")
    """
    raise SystemExit(
        "runner_adapter.py 还没有接入生产训练链路："
        "请在 run_training() 中调用 app.pipelines.train_pipeline.run_train，"
        "或设置 FAMOU_PRODUCTION_ROOT 指向生产项目目录。"
    )


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def summarize_metrics(result: Dict[str, Any]) -> Dict[str, float]:
    """Pick wmape / bias_ratio out of whatever the training chain returned.

    Accepts both top-level keys and a nested ``metrics`` dict, so the adapter
    still works if the production pipeline wraps its output.
    """
    if not isinstance(result, dict):
        raise SystemExit(f"训练链路返回了非字典结果：{type(result).__name__}")

    source = result
    if not {"wmape", "bias_ratio"} <= set(result) and isinstance(result.get("metrics"), dict):
        source = result["metrics"]

    wmape = source.get("wmape")
    bias_ratio = source.get("bias_ratio")
    if not _finite(wmape) or wmape <= 0:
        raise SystemExit(f"训练结果缺少有效的 wmape：{wmape!r}")
    if not _finite(bias_ratio):
        raise SystemExit(f"训练结果缺少有效的 bias_ratio：{bias_ratio!r}")
    return {"wmape": float(wmape), "bias_ratio": float(bias_ratio)}


def main() -> None:
    parser = argparse.ArgumentParser(description="famou production training adapter")
    parser.add_argument("candidate", help="候选 init.py 的绝对路径")
    args = parser.parse_args()

    candidate_path = Path(args.candidate).expanduser().resolve()
    if not candidate_path.is_file():
        raise SystemExit(f"候选文件不存在：{candidate_path}")

    params = load_parameters(candidate_path)
    print(f"[adapter] candidate={candidate_path}", file=sys.stderr)
    print(
        f"[adapter] params={json.dumps(params, ensure_ascii=False, sort_keys=True)}",
        file=sys.stderr,
    )

    result = run_training(params)
    metrics = summarize_metrics(result)

    # The adapter prints exactly one JSON object; everything else went to stderr.
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()

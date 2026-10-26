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


def _template_dir() -> Path:
    """Locate the template directory that holds the helper modules.

    The adapter is copied into ``evolve_tasks/<task>/input/`` before it runs, so
    its own directory is not necessarily the template directory.
    """
    override = os.environ.get("FAMOU_TEMPLATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    here = Path(__file__).resolve().parent
    if (here / "sample_backend.py").is_file():
        return here
    for base in (here, *here.parents):
        guess = base / "solver" / "templates" / "famou-调参"
        if (guess / "sample_backend.py").is_file():
            return guess
    return here


def _load_entry(spec: str):
    """Load a training entry point given as ``module:function``."""
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise SystemExit(f"FAMOU_TRAIN_ENTRY 格式应为 module:function，收到：{spec!r}")
    template_dir = str(_template_dir())
    if template_dir not in sys.path:
        sys.path.insert(0, template_dir)
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise SystemExit(f"无法导入训练后端模块 {module_name!r}：{error}")
    func = getattr(module, func_name, None)
    if not callable(func):
        raise SystemExit(f"训练后端 {spec!r} 不是可调用对象")
    return func


def resolve_backend():
    """Pick the training backend, most explicit source first.

    1. ``FAMOU_TRAIN_ENTRY=module:function`` -- any callable you point at.
    2. ``FAMOU_TRAIN_BACKEND=sample`` -- the built-in synthetic surrogate.
       Only useful for wiring checks; its numbers are NOT business metrics.
    3. ``FAMOU_PRODUCTION_ROOT`` -- real chain: app.pipelines.train_pipeline.run_train.
    """
    entry = os.environ.get("FAMOU_TRAIN_ENTRY", "").strip()
    if entry:
        return _load_entry(entry), f"entry={entry}"

    backend = os.environ.get("FAMOU_TRAIN_BACKEND", "").strip().lower()
    if backend in {"sample", "demo", "surrogate"}:
        func = _load_entry("sample_backend:run_train")
        print(
            "[adapter] 警告：正在使用内置示例后端，指标为合成代理值，不代表业务表现。",
            file=sys.stderr,
        )
        return func, "backend=sample (synthetic surrogate, NOT production)"

    if PRODUCTION_ROOT.is_dir():
        try:
            from app.pipelines.train_pipeline import run_train  # type: ignore
        except Exception as error:
            raise SystemExit(
                f"无法从生产目录 {PRODUCTION_ROOT} 导入 run_train：{error}"
            )
        return run_train, f"production={PRODUCTION_ROOT}"

    raise SystemExit(
        "未配置训练后端，评估无法进行（不会伪造指标）。请任选一种："
        "FAMOU_TRAIN_ENTRY=module:function / "
        "FAMOU_TRAIN_BACKEND=sample（仅联通验证）/ "
        "FAMOU_PRODUCTION_ROOT=/生产项目目录"
    )


def run_training(params: Dict[str, Any]) -> Dict[str, Any]:
    """Run the configured training chain and return its summary.

    The backend is resolved from the environment; only ``wmape`` and
    ``bias_ratio`` are read from whatever it returns.
    """
    backend, label = resolve_backend()
    print(f"[adapter] training backend: {label}", file=sys.stderr)
    return backend(
        params=params,
        market="DOMESTIC_MARKET",
        target="future_1d_box_qty",
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

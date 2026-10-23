"""OpenEvolve evaluator for the famou parameter-only tuning task.

The production training code is not part of this repository. A configured
runner command must execute the real training/evaluation chain and print one
JSON object containing ``wmape`` and ``bias_ratio`` to stdout.
"""

import ast
import copy
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from openevolve.evaluation_result import EvaluationResult

_NUMERIC_RANGES = {
    "n_estimators": (100, 2000, int),
    "learning_rate": (0.005, 0.3, float),
    "num_leaves": (4, 255, int),
    "max_depth": (2, 16, int),
    "min_child_samples": (5, 200, int),
    "subsample": (0.5, 1.0, float),
    "colsample_bytree": (0.3, 1.0, float),
    "reg_alpha": (0.0, 10.0, float),
    "reg_lambda": (0.0, 10.0, float),
    "combo_score_threshold": (0.0, 100.0, float),
    "min_daily_volume": (50.0, 2000.0, float),
    "adaptive_volume_floor": (50.0, 1000.0, float),
    "adaptive_volume_ratio": (0.05, 1.0, float),
    "variance_calibration_min_rows": (50, 500, int),
    "bias_correction_clamp_min": (0.30, 1.00, float),
    "bias_correction_clamp_max": (1.00, 2.00, float),
    "combo_bias_min_rows_floor": (2, 100, int),
    "combo_bias_min_rows_ratio": (0.0, 0.20, float),
    "dirty_date_lag_spread_days": (0, 30, int),
    "min_history_days": (14, 90, int),
}
_BOOLEAN_KEYS = {
    "enable_combo_scoring",
    "adaptive_volume_threshold",
    "combo_absorb_growth",
    "enable_variance_calibration",
    "exclude_dirty_dates",
    "disable_pipeline_features",
    "disable_fwd_pipeline_features",
}
_OBJECTIVES = {"regression", "regression_l1", "huber"}
_PARAMETER_KEYS = set(_NUMERIC_RANGES) | _BOOLEAN_KEYS | {"objective"}


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _failure(message: str, details: str = "") -> EvaluationResult:
    print(f"famou evaluator: {message}")
    artifacts = {"evaluation_error": message}
    if details:
        artifacts["runner_output"] = details[-12000:]
    return EvaluationResult(metrics={"combined_score": 0.0}, artifacts=artifacts)


def _decode_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _parameter_assignment(tree: ast.Module) -> ast.Assign:
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PARAMS" for target in node.targets)
    ]
    if len(assignments) != 1 or len(assignments[0].targets) != 1:
        raise ValueError("必须且只能有一个顶层 PARAMS 字典赋值")
    if not isinstance(assignments[0].value, ast.Dict):
        raise ValueError("PARAMS 必须直接赋值为字典字面量")
    return assignments[0]


def _structure_without_parameter_values(source: str) -> tuple[str, dict[str, Any]]:
    tree = ast.parse(source)
    assignment = _parameter_assignment(tree)

    keys = []
    for key_node in assignment.value.keys:
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            raise ValueError("PARAMS 的所有键必须是字符串字面量")
        keys.append(key_node.value)
    if len(keys) != len(set(keys)):
        raise ValueError("PARAMS 不允许重复键")

    params = ast.literal_eval(assignment.value)
    if not isinstance(params, dict):
        raise ValueError("PARAMS 必须是字典")

    normalized = copy.deepcopy(tree)
    normalized_assignment = _parameter_assignment(normalized)
    normalized_assignment.value = ast.Constant(value=None)
    return ast.dump(normalized, include_attributes=False), params


def _validate_parameters(program_path: str, reference_program_path: Optional[str]) -> None:
    candidate_source = Path(program_path).read_text(encoding="utf-8")
    reference_path = (
        Path(reference_program_path)
        if reference_program_path
        else Path(__file__).with_name("init.py")
    )
    reference_source = reference_path.read_text(encoding="utf-8")

    candidate_structure, params = _structure_without_parameter_values(candidate_source)
    reference_structure, reference_params = _structure_without_parameter_values(reference_source)
    if candidate_structure != reference_structure:
        raise ValueError("只能修改 PARAMS 的值；其他代码结构必须与初始程序一致")
    if list(params) != list(reference_params) or set(params) != _PARAMETER_KEYS:
        raise ValueError("PARAMS 键集合必须与 init.py 完全一致")

    for key, (minimum, maximum, expected_type) in _NUMERIC_RANGES.items():
        value = params[key]
        if expected_type is int:
            valid_type = type(value) is int
        else:
            valid_type = type(value) in (int, float)
        if not valid_type or value < minimum or value > maximum or not math.isfinite(value):
            raise ValueError(f"{key} 必须在 [{minimum}, {maximum}] 范围内")

    for key in _BOOLEAN_KEYS:
        if type(params[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔字面量")
    if not isinstance(params["objective"], str) or params["objective"] not in _OBJECTIVES:
        raise ValueError(f"objective 必须是以下值之一：{', '.join(sorted(_OBJECTIVES))}")
    if params["bias_correction_clamp_min"] >= params["bias_correction_clamp_max"]:
        raise ValueError("bias_correction_clamp_min 必须严格小于 bias_correction_clamp_max")


def evaluate(
    program_path: str,
    runner_command: Optional[list[str]] = None,
    runner_cwd: Optional[str] = None,
    timeout_seconds: int = 3500,
    reference_program_path: Optional[str] = None,
) -> EvaluationResult:
    """Run the production adapter and score its WMAPE/bias JSON output.

    The adapter command is an argv list, not a shell command. Put
    ``{program_path}`` in the argument where the candidate init.py path belongs.
    The adapter must print exactly one JSON object to stdout, for example:
    ``{"wmape": 0.12, "bias_ratio": -0.02}``.
    """
    if not runner_command:
        raise ValueError(
            "未配置 evaluator.evaluator_args.runner_command；"
            "请填写生产训练适配器命令，并在参数中包含 {program_path}"
        )
    if not isinstance(runner_command, list) or not all(
        isinstance(argument, str) for argument in runner_command
    ):
        raise ValueError("runner_command 必须是字符串 argv 列表")
    if not any("{program_path}" in argument for argument in runner_command):
        raise ValueError("runner_command 必须包含 {program_path} 占位符")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ValueError("timeout_seconds 必须是正整数")
    if runner_cwd is not None and not isinstance(runner_cwd, str):
        raise ValueError("runner_cwd 必须是路径字符串或 null")

    try:
        _validate_parameters(program_path, reference_program_path)
    except (OSError, SyntaxError, ValueError) as error:
        return _failure(f"候选参数无效：{error}")

    command = [
        argument.replace("{program_path}", os.path.abspath(program_path))
        for argument in runner_command
    ]
    working_directory = runner_cwd or str(Path(__file__).resolve().parent)
    if not os.path.isdir(working_directory):
        return _failure(f"runner_cwd 目录不存在：{working_directory}")

    try:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        details = _decode_output(error.stdout) + "\n" + _decode_output(error.stderr)
        return _failure(f"生产训练评测超时（{timeout_seconds} 秒）", details)
    except OSError as error:
        return _failure(f"无法启动生产训练适配器：{error}")

    details = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    if completed.returncode != 0:
        return _failure(f"生产训练适配器退出码为 {completed.returncode}", details)

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return _failure(f"适配器 stdout 必须是单个 JSON 对象：{error}", details)
    if not isinstance(result, dict):
        return _failure("适配器 JSON 顶层必须是对象", details)

    wmape = result.get("wmape")
    bias_ratio = result.get("bias_ratio")
    if not _is_finite_number(wmape) or wmape <= 0:
        return _failure("适配器返回的 wmape 必须是有限正数", details)
    if not _is_finite_number(bias_ratio):
        return _failure("适配器返回的 bias_ratio 必须是有限数值", details)

    bias_penalty = max(0.0, abs(bias_ratio) - 0.05) * 100.0
    combined_score = -math.log(wmape) * 100.0 - bias_penalty
    return EvaluationResult(
        metrics={
            "combined_score": float(combined_score),
            "wmape": float(wmape),
            "bias_ratio": float(bias_ratio),
        },
        artifacts={
            "runner_output": details[-12000:],
            "runner_result": json.dumps(
                {"wmape": wmape, "bias_ratio": bias_ratio}, ensure_ascii=False
            ),
        },
    )

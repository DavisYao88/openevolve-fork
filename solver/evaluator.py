"""公共演化求解器评估器（OpenEvolve evaluator）。

这份评估器不绑定任何具体业务，任何"候选程序 → 外部评测命令 → 指标 JSON"的
优化任务都可以直接使用。任务差异全部由 ``evaluator.evaluator_args`` 中的
``objective``（怎么算分）与 ``validation``（候选程序允许怎么改）描述。

求解契约
--------
1. OpenEvolve 把候选程序写到临时文件，并把它的绝对路径交给 ``evaluate()``。
2. 评估器按 ``runner_command`` 调用外部评测命令（argv 列表，不使用 shell），
   其中的 ``{program_path}`` 会替换成候选程序路径。
3. 评测命令必须只向 stdout 打印**一个 JSON 对象**，内容是任意指标，例如
   ``{"wmape": 0.12, "bias_ratio": -0.02}``；日志一律写 stderr。
4. 评估器按 ``objective`` 把指标折算成 ``combined_score``（**越大越好**），
   所有原始指标同时透传给 OpenEvolve，可直接用作 MAP-Elites 的特征维度。

objective 配置（YAML）
---------------------
    objective:
      metrics:
        - name: wmape            # 指标名，取自评测命令输出的 JSON
          direction: minimize    # minimize（默认）| maximize
          transform: log         # none（默认）| log | abs
          threshold: null        # 设置后先取 max(0, |v| - threshold)
          scale: 100             # 乘数，默认 1
          weight: 1.0            # 权重，默认 1

单个指标的贡献为 ``sign * weight * scale * f(value)``，其中 ``f`` 由
``threshold`` / ``transform`` 决定，``sign`` 在 ``direction: minimize`` 时为 -1。
上例等价于 famou 调参任务的 ``-log(wmape) * 100``。

若 ``objective`` 未配置，且评测命令已经返回 ``combined_score``（或 ``score``），
则直接使用该值；否则报错，避免"静默按 0 分演化"。

validation 配置（YAML）
----------------------
    validation:
      mode: params_dict     # none（默认，不校验结构）| params_dict
      variable: PARAMS      # params_dict 模式下校验的顶层字典变量名
      ranges:               # 数值范围，简写 [min, max] 或 {min, max, type}
        learning_rate: [0.005, 0.3]
        num_leaves: {min: 4, max: 255, type: int}
      booleans: [enable_combo_scoring]
      enums: {objective: [regression, regression_l1, huber]}
      constraints: {lt: [[clamp_min, clamp_max]]}

``params_dict`` 模式会强制候选程序与初始程序（``reference_program_path``）结构
完全一致、键集合一致，只允许改值；适用于"只调参数、不改算法"的任务。
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

# 评测命令没有输出 combined_score 时，兜底使用的键名。
_SCORE_KEYS = ("combined_score", "score")


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _failure(message: str, details: str = "") -> EvaluationResult:
    """Return an explicit zero score plus the reason as an artifact."""
    print(f"solver evaluator: {message}")
    artifacts = {"evaluation_error": message}
    if details:
        artifacts["runner_output"] = details[-12000:]
    return EvaluationResult(metrics={"combined_score": 0.0}, artifacts=artifacts)


def _decode_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    """从评测命令的 stdout 里取出结果 JSON。

    约定是 stdout 只打印一个 JSON 对象，但真实的生产工程常把训练/查询日志直接
    print 到 stdout（例如 ``[train] 查询宽表 …``）。这里先整体解析，失败后从后
    往前找最后一个合法 JSON 行，避免日志噪声让整轮评测变成 0 分。
    """
    text = (stdout or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    last_error: Optional[json.JSONDecodeError] = None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError as error:
            last_error = error
            continue
        if isinstance(candidate, dict):
            return candidate

    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("stdout 里没有找到 JSON 对象", text[:200], 0)


# ---------------------------------------------------------------------------
# 目标函数：把任意指标折算成越大越好的 combined_score
# ---------------------------------------------------------------------------


def _transform_value(value: float, transform: str, threshold: Optional[float]) -> float:
    if threshold is not None:
        return max(0.0, abs(value) - float(threshold))
    if transform == "log":
        if value <= 0:
            raise ValueError(f"transform: log 要求指标为正数，实际为 {value}")
        return math.log(value)
    if transform == "abs":
        return abs(value)
    if transform in (None, "", "none"):
        return float(value)
    raise ValueError(f"未知的 transform：{transform}")


def _metric_contribution(name: str, metrics: dict[str, Any], spec: dict[str, Any]) -> float:
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
        raise ValueError(f"objective.metrics 的每一项都需要字符串 name，实际为：{spec!r}")
    metric_name = spec["name"]
    if metric_name not in metrics:
        raise ValueError(f"评测结果缺少目标指标 {metric_name}；实际指标：{sorted(metrics)}")
    value = metrics[metric_name]
    if not _is_finite_number(value):
        raise ValueError(f"目标指标 {metric_name} 必须是有限数值，实际为 {value!r}")

    direction = spec.get("direction", "minimize")
    if direction not in ("minimize", "maximize"):
        raise ValueError(
            f"{metric_name} 的 direction 只能是 minimize 或 maximize，实际为 {direction}"
        )

    threshold = spec.get("threshold")
    if threshold is not None and not _is_finite_number(threshold):
        raise ValueError(f"{metric_name} 的 threshold 必须是有限数值")

    try:
        scale = float(spec.get("scale", 1.0))
        weight = float(spec.get("weight", 1.0))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{metric_name} 的 scale/weight 必须是数值：{error}") from error

    transformed = _transform_value(float(value), spec.get("transform", "none"), threshold)
    sign = -1.0 if direction == "minimize" else 1.0
    return sign * weight * scale * transformed


def _combined_score(metrics: dict[str, Any], objective: Optional[dict[str, Any]]) -> float:
    if not objective:
        for key in _SCORE_KEYS:
            value = metrics.get(key)
            if _is_finite_number(value):
                return float(value)
        raise ValueError(
            "未配置 objective，且评测结果没有 combined_score/score；"
            "请在 evaluator.evaluator_args.objective 中声明目标指标与方向"
        )

    if not isinstance(objective, dict):
        raise ValueError("objective 必须是键值映射")

    spec_metrics = objective.get("metrics")
    if spec_metrics is None:
        # 单指标简写：objective: {name: wmape, direction: minimize}
        if isinstance(objective.get("name"), str):
            spec_metrics = [objective]
        else:
            for key in _SCORE_KEYS:
                value = metrics.get(key)
                if _is_finite_number(value):
                    return float(value)
            raise ValueError("objective 缺少 metrics 列表，且评测结果没有 combined_score/score")
    if not isinstance(spec_metrics, list) or not spec_metrics:
        raise ValueError("objective.metrics 必须是非空列表")

    total = 0.0
    for spec in spec_metrics:
        total += _metric_contribution("objective", metrics, spec)
    return float(total)


# ---------------------------------------------------------------------------
# 候选程序校验：把"允许改什么"变成配置
# ---------------------------------------------------------------------------


def _assignment_of(tree: ast.Module, variable: str) -> ast.Assign:
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets)
    ]
    if len(assignments) != 1 or len(assignments[0].targets) != 1:
        raise ValueError(f"必须且只能有一个顶层 {variable} 字典赋值")
    if not isinstance(assignments[0].value, ast.Dict):
        raise ValueError(f"{variable} 必须直接赋值为字典字面量")
    return assignments[0]


def _structure_without_values(source: str, variable: str) -> tuple[str, dict[str, Any]]:
    tree = ast.parse(source)
    assignment = _assignment_of(tree, variable)

    keys = []
    for key_node in assignment.value.keys:
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            raise ValueError(f"{variable} 的所有键必须是字符串字面量")
        keys.append(key_node.value)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{variable} 不允许重复键")

    values = ast.literal_eval(assignment.value)
    if not isinstance(values, dict):
        raise ValueError(f"{variable} 必须是字典")

    normalized = copy.deepcopy(tree)
    _assignment_of(normalized, variable).value = ast.Constant(value=None)
    return ast.dump(normalized, include_attributes=False), values


def _normalize_range(key: str, spec: Any) -> tuple[float, float, Optional[type]]:
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        minimum, maximum = spec
        expected_type = None
    elif isinstance(spec, dict):
        minimum = spec.get("min")
        maximum = spec.get("max")
        expected_type = {"int": int, "float": float}.get(spec.get("type"))
        if spec.get("type") not in (None, "int", "float"):
            raise ValueError(f"{key} 的 type 只能是 int 或 float")
    else:
        raise ValueError(f"{key} 的范围必须是 [min, max] 或 {{min, max, type}}")

    if not _is_finite_number(minimum) or not _is_finite_number(maximum):
        raise ValueError(f"{key} 的 min/max 必须是有限数值")
    return float(minimum), float(maximum), expected_type


def _check_constraints(values: dict[str, Any], constraints: Any) -> None:
    if not constraints:
        return
    if not isinstance(constraints, dict):
        raise ValueError("validation.constraints 必须是键值映射")
    for operator, pairs in constraints.items():
        if operator not in ("lt", "lte"):
            raise ValueError(f"未知的约束运算符：{operator}")
        if not isinstance(pairs, list):
            raise ValueError(f"constraints.{operator} 必须是 [[左键, 右键], ...]")
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"constraints.{operator} 的每一项必须是 [左键, 右键]")
            left_key, right_key = pair
            if left_key not in values or right_key not in values:
                raise ValueError(f"约束引用了不存在的键：{left_key} / {right_key}")
            left, right = values[left_key], values[right_key]
            if not _is_finite_number(left) or not _is_finite_number(right):
                raise ValueError(f"约束 {left_key} {operator} {right_key} 的两个值都必须是数值")
            ok = left < right if operator == "lt" else left <= right
            if not ok:
                raise ValueError(f"必须满足 {left_key} {operator} {right_key}")


def validate_objective(objective: Optional[dict[str, Any]]) -> None:
    """Fail fast on a malformed objective config (called before a run starts)."""
    if objective is None:
        return
    if not isinstance(objective, dict):
        raise ValueError("objective 必须是键值映射")

    specs = objective.get("metrics")
    if specs is None:
        if isinstance(objective.get("name"), str):
            specs = [objective]
        else:
            return
    if not isinstance(specs, list) or not specs:
        raise ValueError("objective.metrics 必须是非空列表")

    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
            raise ValueError(f"objective.metrics 的每一项都需要字符串 name，实际为：{spec!r}")
        direction = spec.get("direction", "minimize")
        if direction not in ("minimize", "maximize"):
            raise ValueError(f"{spec['name']} 的 direction 只能是 minimize 或 maximize")
        if spec.get("transform", "none") not in ("none", "", None, "log", "abs"):
            raise ValueError(f"{spec['name']} 的 transform 只能是 none / log / abs")
        for key in ("scale", "weight", "threshold"):
            if spec.get(key) is not None and not _is_finite_number(spec[key]):
                raise ValueError(f"{spec['name']} 的 {key} 必须是数值")


def validate_validation(validation: Optional[dict[str, Any]]) -> None:
    """Fail fast on a malformed validation config (called before a run starts)."""
    if validation is None:
        return
    if not isinstance(validation, dict):
        raise ValueError("validation 必须是键值映射")

    mode = validation.get("mode", "none")
    if mode in (None, "none"):
        return
    if mode != "params_dict":
        raise ValueError(f"未知的 validation.mode：{mode}（可用 none / params_dict）")

    variable = validation.get("variable") or "PARAMS"
    if not isinstance(variable, str) or not variable.isidentifier():
        raise ValueError(f"validation.variable 必须是合法变量名，实际为 {variable!r}")

    ranges = validation.get("ranges") or {}
    if not isinstance(ranges, dict):
        raise ValueError("validation.ranges 必须是键值映射")
    for key, spec in ranges.items():
        _normalize_range(str(key), spec)

    enums = validation.get("enums") or {}
    if not isinstance(enums, dict):
        raise ValueError("validation.enums 必须是键值映射")
    for key, allowed in enums.items():
        if not isinstance(allowed, list) or not allowed:
            raise ValueError(f"validation.enums.{key} 必须是非空列表")

    booleans = validation.get("booleans") or []
    if not isinstance(booleans, list):
        raise ValueError("validation.booleans 必须是列表")

    constraints = validation.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            raise ValueError("validation.constraints 必须是键值映射")
        for operator, pairs in constraints.items():
            if operator not in ("lt", "lte"):
                raise ValueError(f"未知的约束运算符：{operator}")
            if not isinstance(pairs, list):
                raise ValueError(f"constraints.{operator} 必须是 [[左键, 右键], ...]")


def _validate_candidate(
    program_path: str,
    validation: Optional[dict[str, Any]],
    reference_program_path: Optional[str],
) -> None:
    """Validate the candidate program against the task's validation config."""
    if not validation:
        return
    if not isinstance(validation, dict):
        raise ValueError("validation 必须是键值映射")

    mode = validation.get("mode", "none")
    if mode in (None, "none"):
        return
    if mode != "params_dict":
        raise ValueError(f"未知的 validation.mode：{mode}（可用 none / params_dict）")

    variable = validation.get("variable") or "PARAMS"
    if not isinstance(variable, str) or not variable.isidentifier():
        raise ValueError(f"validation.variable 必须是合法变量名，实际为 {variable!r}")

    candidate_source = Path(program_path).read_text(encoding="utf-8")
    reference_path = Path(reference_program_path) if reference_program_path else None
    if reference_path is None or not reference_path.is_file():
        raise ValueError("params_dict 校验需要 reference_program_path 指向初始程序")

    candidate_structure, values = _structure_without_values(candidate_source, variable)
    reference_structure, reference_values = _structure_without_values(
        reference_path.read_text(encoding="utf-8"), variable
    )
    if candidate_structure != reference_structure:
        raise ValueError(f"只允许修改 {variable} 的值；其他代码结构必须与初始程序一致")
    if list(values) != list(reference_values):
        raise ValueError(f"{variable} 的键集合与键顺序必须与初始程序完全一致")

    ranges = validation.get("ranges") or {}
    if not isinstance(ranges, dict):
        raise ValueError("validation.ranges 必须是键值映射")
    for key, spec in ranges.items():
        if key not in values:
            raise ValueError(f"范围配置引用了不存在的键：{key}")
        minimum, maximum, expected_type = _normalize_range(key, spec)
        value = values[key]
        if expected_type is int:
            valid_type = type(value) is int
        elif expected_type is float:
            valid_type = type(value) in (int, float)
        else:
            valid_type = type(value) in (int, float)
        if not valid_type or not _is_finite_number(value) or value < minimum or value > maximum:
            raise ValueError(f"{key} 必须在 [{minimum}, {maximum}] 范围内")

    for key in validation.get("booleans") or []:
        if key not in values:
            raise ValueError(f"布尔配置引用了不存在的键：{key}")
        if type(values[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔字面量")

    enums = validation.get("enums") or {}
    if not isinstance(enums, dict):
        raise ValueError("validation.enums 必须是键值映射")
    for key, allowed in enums.items():
        if key not in values:
            raise ValueError(f"枚举配置引用了不存在的键：{key}")
        if not isinstance(allowed, list) or values[key] not in allowed:
            raise ValueError(f"{key} 必须是以下值之一：{allowed}")

    _check_constraints(values, validation.get("constraints"))


# ---------------------------------------------------------------------------
# 评测命令
# ---------------------------------------------------------------------------


def _prepare_command(runner_command: Any, program_path: str) -> list[str]:
    if not runner_command:
        raise ValueError(
            "未配置 evaluator.evaluator_args.runner_command；"
            "请填写评测命令，并在参数中包含 {program_path}"
        )
    if not isinstance(runner_command, list) or not all(
        isinstance(argument, str) for argument in runner_command
    ):
        raise ValueError("runner_command 必须是字符串 argv 列表")
    if not any("{program_path}" in argument for argument in runner_command):
        raise ValueError("runner_command 必须包含 {program_path} 占位符")
    return [
        argument.replace("{program_path}", os.path.abspath(program_path))
        for argument in runner_command
    ]


def evaluate(
    program_path: str,
    runner_command: Optional[list[str]] = None,
    runner_cwd: Optional[str] = None,
    timeout_seconds: int = 3500,
    objective: Optional[dict[str, Any]] = None,
    validation: Optional[dict[str, Any]] = None,
    reference_program_path: Optional[str] = None,
) -> EvaluationResult:
    """Score one candidate program by running the task's evaluation command.

    The command is an argv list, never a shell string. Put ``{program_path}``
    where the candidate program path belongs; the command must print exactly one
    JSON object of metrics to stdout.
    """
    command = _prepare_command(runner_command, program_path)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ValueError("timeout_seconds 必须是正整数")
    if runner_cwd is not None and not isinstance(runner_cwd, str):
        raise ValueError("runner_cwd 必须是路径字符串或 null")

    try:
        _validate_candidate(program_path, validation, reference_program_path)
    except (OSError, SyntaxError, ValueError) as error:
        return _failure(f"候选程序无效：{error}")

    # 默认工作目录是本评估器所在的任务 input 目录（页面运行时就是本轮输入目录）。
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
        return _failure(f"评测命令超时（{timeout_seconds} 秒）", details)
    except OSError as error:
        return _failure(f"无法启动评测命令：{error}")

    details = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    if completed.returncode != 0:
        return _failure(f"评测命令退出码为 {completed.returncode}", details)

    try:
        result = _parse_stdout_json(completed.stdout)
    except json.JSONDecodeError as error:
        return _failure(f"评测命令 stdout 里没有可解析的 JSON 对象：{error}", details)
    if not isinstance(result, dict):
        return _failure("评测命令 JSON 顶层必须是对象", details)

    metrics = {key: float(value) for key, value in result.items() if _is_finite_number(value)}
    try:
        combined_score = _combined_score(result, objective)
    except ValueError as error:
        return _failure(f"目标函数无法计算：{error}", details)

    metrics["combined_score"] = float(combined_score)
    return EvaluationResult(
        metrics=metrics,
        artifacts={
            "runner_output": details[-12000:],
            "runner_result": json.dumps(result, ensure_ascii=False)[-12000:],
        },
    )

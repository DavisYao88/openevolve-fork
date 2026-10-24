"""内置示例训练后端（synthetic surrogate）——只用于联通验证，不是业务指标。

!! 重要 !!
本文件不连接任何真实数据或生产训练链路。它按一组"隐藏最优配方"构造出
确定性的 wmape / bias_ratio 响应面，用途只有一个：验证
「候选参数 -> 训练后端 -> 指标 -> combined_score -> 进化选择」这条链路是通的，
并且分数确实会对参数变化产生反应。

**示例后端产生的数值不代表任何业务表现，不能用于调参结论。**
真实场景请用 FAMOU_PRODUCTION_ROOT 或 FAMOU_TRAIN_ENTRY 指向生产链路。

响应面（wmape）的构成：
    0.085（理论下界）
    + Σ 权重 × 与最优值之间的距离（对数距离 or 线性距离）
    + 若干结构性开关的加成/减免（组合分流、脏日剔除、特征开关等）
"""

from __future__ import annotations

import math
from typing import Any, Dict

# 隐藏的"最优配方"：示例后端围绕它构造一个单峰响应面。
_OPTIMAL = {
    "n_estimators": (900, "log"),
    "learning_rate": (0.03, "log"),
    "num_leaves": (96, "log"),
    "max_depth": (10, "log"),
    "min_child_samples": (20, "log"),
    "subsample": (0.85, "linear"),
    "colsample_bytree": (0.65, "linear"),
    "reg_alpha": (1.5, "log"),
    "reg_lambda": (3.0, "log"),
}

_WEIGHTS = {
    "n_estimators": 0.020,
    "learning_rate": 0.045,
    "num_leaves": 0.030,
    "max_depth": 0.015,
    "min_child_samples": 0.020,
    "subsample": 0.025,
    "colsample_bytree": 0.025,
    "reg_alpha": 0.020,
    "reg_lambda": 0.018,
}

# 结构性开关的偏好（负数=更好）
_OBJECTIVE_BONUS = {"regression": 0.0, "regression_l1": -0.004, "huber": -0.006}

_WMAPE_FLOOR = 0.085


def _distance(value: Any, target: float, kind: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 3.0
    if not math.isfinite(number):
        return 3.0
    if kind == "log":
        return abs(math.log(max(number, 1e-6) / float(target)))
    return abs(number - float(target))


def _flag(params: Dict[str, Any], key: str, default: bool = False) -> bool:
    return bool(params.get(key, default))


def wmape_of(params: Dict[str, Any]) -> float:
    """按参数构造一个确定性的 wmape 代理值（越小越好）。"""
    gap = sum(
        _WEIGHTS[key] * _distance(params.get(key), target, kind)
        for key, (target, kind) in _OPTIMAL.items()
    )

    structure = 0.0
    structure += -0.012 if _flag(params, "enable_combo_scoring") else 0.004
    structure += 0.010 * abs(float(params.get("combo_score_threshold", 50.0)) - 35.0) / 100.0
    structure += -0.006 if _flag(params, "exclude_dirty_dates") else 0.008
    structure += 0.025 if _flag(params, "disable_pipeline_features") else 0.0
    structure += 0.015 if _flag(params, "disable_fwd_pipeline_features") else 0.0
    structure += -0.003 if _flag(params, "adaptive_volume_threshold") else 0.0
    structure += 0.008 * abs(float(params.get("variance_calibration_min_rows", 200)) - 120) / 500.0
    structure += 0.006 * abs(float(params.get("min_history_days", 28)) - 45) / 90.0
    structure += _OBJECTIVE_BONUS.get(str(params.get("objective", "regression")), 0.0)

    return max(_WMAPE_FLOOR, _WMAPE_FLOOR + gap + structure)


def bias_of(params: Dict[str, Any]) -> float:
    """按两级偏置校正参数构造 bias_ratio（0 附近最佳，超过 ±0.05 会罚分）。"""
    clamp_min = float(params.get("bias_correction_clamp_min", 0.7))
    clamp_max = float(params.get("bias_correction_clamp_max", 1.3))
    floor = float(params.get("combo_bias_min_rows_floor", 14))
    ratio = float(params.get("combo_bias_min_rows_ratio", 0.02))

    center = (clamp_min + clamp_max) / 2.0
    width = clamp_max - clamp_min
    bias = 0.0
    bias += 0.05 * (1.0 - center)          # 校正区间中心偏离 1.0 → 系统性偏差
    bias += 0.03 * (width - 0.6)           # 区间过宽 → 校正不足
    bias += 0.8 * (ratio - 0.02)           # 组合级标定比例
    bias += 0.001 * (floor - 14)
    return max(-0.5, min(0.5, bias))


def run_train(params: Dict[str, Any], market: str = "", target: str = "") -> Dict[str, Any]:
    """示例后端入口：签名与生产 run_train 保持一致。

    返回结构里只有 wmape / bias_ratio 会被读取；额外字段仅用于日志排查。
    """
    wmape = wmape_of(params)
    bias_ratio = bias_of(params)
    return {
        "wmape": round(wmape, 6),
        "bias_ratio": round(bias_ratio, 6),
        "backend": "sample-synthetic-surrogate",
        "market": market,
        "target": target,
    }


if __name__ == "__main__":
    # 手动自检：打印默认配方与最优配方的代理指标。
    import json

    from init import PARAMS  # type: ignore

    best = dict(PARAMS)
    for key, (value, _kind) in _OPTIMAL.items():
        best[key] = value
    best["exclude_dirty_dates"] = True
    best["disable_pipeline_features"] = False
    best["variance_calibration_min_rows"] = 120
    best["min_history_days"] = 45
    print(json.dumps({
        "default": run_train(dict(PARAMS)),
        "near_optimal": run_train(best),
    }, ensure_ascii=False, indent=2))

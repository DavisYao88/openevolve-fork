"""伐谋演化单元：训练链路参数（A 方案，只搜参数，不改算法逻辑）。

规则（评估器会强制校验，违反者直接判 0 分）：
    1. 只允许修改 PARAMS 中的数值/布尔值，不允许新增键、删键、改键名。
    2. 不允许 import 任何模块，不允许出现函数定义、类定义、I/O 操作。
    3. 每个参数的合法范围见下方注释；评估器有同样的硬约束。

真正执行的是生产训练流程 `app.pipelines.train_pipeline.run_train`，
本文件不包含任何训练代码——改这里只等于改一组配置。
"""

# 当前解：默认值 = 生产配方（production_recipe.json 2026-08-29-v3，内销 mv_4.7 口径）
# EVOLVE-BLOCK-START
PARAMS = {
    # ── LightGBM 超参（生产默认 L2，内销不套 L1）────────────────────────
    "n_estimators": 500,          # [100, 2000]   带 early_stopping(50)，上限不敏感
    "learning_rate": 0.05,        # [0.005, 0.3]
    "num_leaves": 31,             # [4, 255]
    "max_depth": 6,               # [2, 16]
    "min_child_samples": 50,      # [5, 200]      时段×温层样本量小，过低会过拟合
    "subsample": 0.8,             # [0.5, 1.0]
    "colsample_bytree": 0.8,      # [0.3, 1.0]
    "reg_alpha": 0.1,             # [0.0, 10.0]
    "reg_lambda": 0.1,            # [0.0, 10.0]
    "objective": "regression",    # ["regression", "regression_l1", "huber"]

    # ── 组合质态分流（v1.3.0）─────────────────────────────────────────
    # 注：仅在连真实数仓时生效；mock/快照数据下生产代码会跳过这一整段
    "enable_combo_scoring": True,
    "combo_score_threshold": 50.0,        # [0, 100]   低于该分走 RULE 兜底
    "min_daily_volume": 500.0,            # [50, 2000] 日均箱量门槛
    "adaptive_volume_threshold": False,   # 自适应量级门槛开关
    "adaptive_volume_floor": 200.0,       # [50, 1000]
    "adaptive_volume_ratio": 0.25,        # [0.05, 1.0]
    "combo_absorb_growth": True,          # 断裂检测方向不对称（上行爬坡不计断裂）

    # ── 方差拉伸校准（E-6/P1-11）──────────────────────────────────────
    "enable_variance_calibration": True,
    "variance_calibration_min_rows": 200, # [50, 500]  内销 val 约 116 行，可放宽到 100

    # ── 两级偏置校正（v1.3.2，2026-10-19 由硬编码常量参数化）─────────────
    # 系数 = val 实际总量 / 预测总量，再用上下界截断，防止极端样本外推。
    # 下界必须严格小于上界，否则判无效。
    "bias_correction_clamp_min": 0.7,     # [0.30, 1.00] 原硬编码 0.7
    "bias_correction_clamp_max": 1.3,     # [1.00, 2.00] 原硬编码 1.3
    # 组合级系数标定的最小样本：max(floor, 组合外总行数 × ratio)，不足则回退全局系数
    "combo_bias_min_rows_floor": 14,      # [2, 100]     原硬编码 14
    "combo_bias_min_rows_ratio": 0.02,    # [0.0, 0.20]  原硬编码 0.02

    # ── 数据与特征开关 ────────────────────────────────────────────────
    "exclude_dirty_dates": True,          # 剔除源侧缺损标签（内销常温塌方）
    "dirty_date_lag_spread_days": 0,      # [0, 30]    脏日后向 lag 传播剔除天数
    "min_history_days": 28,               # [14, 90]   进模型流的最小历史天数
    "disable_pipeline_features": False,   # 关闭全部在途特征
    "disable_fwd_pipeline_features": False,  # 关闭窗口累计在途特征
}
# EVOLVE-BLOCK-END


def describe() -> dict:
    """给人工查看用；评估器不依赖本函数。"""
    return {
        "param_count": len(PARAMS),
        "lgb_params": [k for k in PARAMS if k in {
            "n_estimators", "learning_rate", "num_leaves", "max_depth",
            "min_child_samples", "subsample", "colsample_bytree",
            "reg_alpha", "reg_lambda", "objective",
        }],
    }

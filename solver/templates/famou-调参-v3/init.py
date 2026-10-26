"""D+2 货量预测 LightGBM 参数演化种子。

只允许 OpenEvolve 修改 PARAMS 中的值；特征、样本、切分和评估逻辑均由
runner_adapter.py 固定。
"""

# EVOLVE-BLOCK-START
PARAMS = {
    "n_estimators": 820,
    "learning_rate": 0.029,
    "num_leaves": 12,
    "max_depth": 4,
    "min_child_samples": 70,
    "subsample": 0.83,
    "colsample_bytree": 0.58,
    "reg_alpha": 3.1,
    "reg_lambda": 5.4,
    "min_split_gain": 0.011,
    "objective": "regression",
}
# EVOLVE-BLOCK-END


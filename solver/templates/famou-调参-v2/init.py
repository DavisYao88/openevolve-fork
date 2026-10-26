"""D+2 货量预测 LightGBM 参数演化种子。

只允许 OpenEvolve 修改 PARAMS 中的值；特征、样本、切分和评估逻辑均由
runner_adapter.py 固定。
"""

# EVOLVE-BLOCK-START
PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.0377125,
    "num_leaves": 16,
    "max_depth": 4,
    "min_child_samples": 66,
    "subsample": 0.8397,
    "colsample_bytree": 0.62265,
    "reg_alpha": 2.849,
    "reg_lambda": 3.58,
    "min_split_gain": 0.0,
    "objective": "regression",
}
# EVOLVE-BLOCK-END


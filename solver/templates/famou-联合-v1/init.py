
"""D+2 货量预测：参数 + 代码 联合演化种子。

演化范围只有下面 EVOLVE-BLOCK 里的三样东西：
  1. PARAMS             —— LightGBM 参数
  2. augment_features() —— 追加特征列
  3. postprocess()      —— 预测结果后处理

块外的代码是本模板的运行骨架，不要修改。基线版本不做任何额外处理，
即等价于「纯调参」；你可以先只调参数，再逐步引入特征和后处理。
"""

# EVOLVE-BLOCK-START
PARAMS = {
    "n_estimators": 1000,
    "learning_rate": 0.025,
    "num_leaves": 8,
    "max_depth": 4,
    "min_child_samples": 80,
    "subsample": 0.85,
    "colsample_bytree": 0.62,
    "reg_alpha": 3.0,
    "reg_lambda": 5.0,
    "min_split_gain": 0.012,
    "objective": "regression_l1",
}


def augment_features(df):
    """追加特征列。

    入参 df 是开发集（训练段 + 验证段），**已经剔除了标签列**，
    因此无法用真值构造特征。返回 (新的 df, 新增列名列表)。

    约束（违反会直接判 0 分）：
      - 必须返回二元组 (DataFrame, list[str])
      - 行数与索引顺序必须与入参完全一致，不能 filter / sort
      - 新增列必须是数值列，列名不能与生产特征重名
      - 最多新增 20 列，空值会被填充为 0
    """
    return df, []


def postprocess(pred, ctx):
    """预测后处理。

    入参 pred 是 LightGBM 在验证折上的预测数组（float），
    ctx 含折号、验证月份、可用列名、参数快照等元信息，**不含任何真值**。

    返回同长度的数值数组。用于压极端值、按温层/班次缩放、阈值截断等。
    """
    return pred
# EVOLVE-BLOCK-END

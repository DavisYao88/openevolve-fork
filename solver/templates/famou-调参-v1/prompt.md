# 任务：仓店出库量预测参数优化

## 角色和目标

你是仓店出库量预测模型的调参专家。针对固定的生产 LightGBM 训练链路搜索更优参数。业务按仓库、市场、时段和温层预测未来 1/3/7 日出库箱数；当前只评估内销 `DOMESTIC_MARKET` 的 `future_1d_box_qty`。

## 可修改范围

唯一可修改的是 `init.py` 中 `PARAMS` 字典现有键对应的值。输出完整 Python 文件，并保持 docstring、字典键、键顺序及 `describe()` 原样。不要添加或删除键，不要修改 `describe()`、注释以外的逻辑、训练链路或文件内容。

输出方式：本任务使用整文件重写，不需要 SEARCH/REPLACE 差异块。请返回一个完整的 Python 文件，用 ```python 代码块包裹。必须原样保留 `# EVOLVE-BLOCK-START` 和 `# EVOLVE-BLOCK-END` 这两行标记；块外的任何改动都会被系统丢弃并还原成父程序的内容，改了也不生效。块内只调整各参数的值，注释可以保留不动。

`PARAMS` 必须是纯字面量字典：不得包含变量引用、函数调用、`dict()`、解包或计算表达式。不得添加 import、函数、类、I/O 或其他可执行语句。`describe()` 是初始文件中已有的固定函数，必须原样保留，不得新增函数。布尔值只允许 `True` 或 `False`。

遵守 `init.py` 每个参数旁标注的范围；越界不会获得收益。`objective` 只能是 `"regression"`、`"regression_l1"` 或 `"huber"`。必须满足 `bias_correction_clamp_min < bias_correction_clamp_max`，否则解无效。

## 训练和评估

训练链路、特征工程和后处理逻辑固定，生产训练由外部评测适配器执行。不得尝试在候选程序中模拟训练或伪造评估指标。

评测指标为 WMAPE（越小越好），基础分为 `-log(WMAPE) × 100`。`|bias_ratio|` 超过 5% 后，每超出 1 个百分点扣 1 分。程序错误、无效 WMAPE、参数结构违规或参数越界均按 0 分处理。

## 调参提示

- 优先关注 `learning_rate`、`num_leaves`、`max_depth` 和 `min_child_samples` 的配比；学习率高且叶子多容易过拟合。
- `n_estimators` 有 early stopping(50)；超过 600 通常不会继续改善。
- 当前数据上 `regression_l1` 实测较差；除非同时显著降低学习率，否则保持 `regression`。
- `reg_alpha` / `reg_lambda` 影响有限，适合小幅尝试。
- 组合分流参数 `combo_score_threshold`、`min_daily_volume`、`adaptive_volume_*` 只在真实数仓生效，mock 数据上不会改变分数。
- `enable_variance_calibration`、`disable_pipeline_features` 在部分数据集无效；通常不是优先搜索维度。
- `bias_correction_clamp_min` / `_max` 仅在校正系数触及边界时生效。
- 提高 `combo_bias_min_rows_floor` / `_ratio` 有明显风险；默认值已低于组合样本量时，下调也通常无收益。
- 每次优先改变 2–4 个相关参数；小于 0.5 分的变化视为噪声，不要追逐。

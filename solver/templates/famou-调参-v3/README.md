# famou-调参-v3

用OpenEvolve搜索`slot_temp` D+2（`feature_version=v2.0.0`）LightGBM参数。

## 与v2的区别

- 仍然直接导入当前生产项目代码，不复制特征公式。
- 复用生产`_compute_combo_scores`，因此外销低量组合与正式训练一致走RULE。
- 复用生产`_time_series_split`封存最终Test，演化过程不可见Test。
- 在Development上复用生产`_run_rolling_backtest`：4个月训练、1个月验证、步进4个月、最多7折。
- 生产配方决定训练起点、脏日规则、组合阈值和量级门槛。
- 评估只读：不调用`run_train`，不写job log、registry或模型文件。

注意：生产偏置校正和方差校准发生在最终模型评估阶段，不参与当前生产滚动回测的参数排名，因此v3也不把它们混进演化目标。冠军参数仍需用正式`AB`任务在封存Test上验证一次。

## 默认配置

- 市场：`DOMESTIC_MARKET`
- 仓库：`WBJ`
- 训练窗：生产配方起点～`2026-09-15`
- 标签：`future_1d_box_qty`（v2.0.0语义为D+2）
- 特征：25列，不使用订单在途
- 滚动回测：生产口径，最多7折

环境变量：

```bash
export FAMOU_PRODUCTION_ROOT="/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/班次+库存+出库箱数/model"
export FAMOU_D2_MARKET="DOMESTIC_MARKET"  # 或EXPORT_MARKET，必须分开演化
export FAMOU_D2_WAREHOUSE="WBJ"
export FAMOU_D2_TRAIN_START="2025-04-01" # 可选；默认取对应市场生产配方
export FAMOU_D2_TRAIN_END="2026-09-15"
export FAMOU_D2_FOLDS="7"
```

## 联通检查

无需数据库：

```bash
FAMOU_D2_USE_MOCK=1 python runner_adapter.py init.py
```

真实数据只读检查：

```bash
python runner_adapter.py init.py
```

输出为单行JSON指标，日志写stderr，适合OpenEvolve adapter调用。

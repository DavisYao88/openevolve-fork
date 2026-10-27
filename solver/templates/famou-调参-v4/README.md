# famou-调参-v4

用 OpenEvolve 搜索 `slot_temp` D+2 LightGBM 参数。特征版本与市场由环境变量切换，其余评估逻辑与 v3 一致。

## 与 v3 的区别

- `feature_version` 由 `FAMOU_D2_FEATURE_VERSION` 决定，支持 `v2.0.0`(25 特征) / `v2.1.0`(29) / `v2.1.1`(31)，不再硬编码 v2.0.0。
- 特征数校验改为按版本动态断言，版本不符直接失败，不做静默降级。
- 启动时把 market / feature_version / 训练窗 / 折数打到 stderr，任务日志可直接核对演化口径。

## 与 v2 的共同点

- 仍然直接导入当前生产项目代码，不复制特征公式。
- 复用生产 `_compute_combo_scores`，因此外销低量组合与正式训练一致走 RULE。
- 复用生产 `_time_series_split` 封存最终 Test，演化过程不可见 Test。
- 在 Development 上复用生产 `_run_rolling_backtest`：4 个月训练、1 个月验证、步进 4 个月。
- 生产配方决定训练起点、脏日规则、组合阈值和量级门槛。
- 评估只读：不调用 `run_train`，不写 job log、registry 或模型文件。

注意：生产偏置校正和方差校准发生在最终模型评估阶段，不参与滚动回测的参数排名，因此也不混进演化目标。冠军参数仍需用正式 `AB` 任务在封存 Test 上验证一次。

## 默认配置

- 市场：`DOMESTIC_MARKET`
- 特征版本：`v2.0.0`
- 仓库：`WBJ`
- 训练窗：生产配方起点～`2026-09-15`
- 标签：`future_1d_box_qty`（D+2 语义）

环境变量：

```bash
export FAMOU_PRODUCTION_ROOT="/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/班次+库存+出库箱数/model"
export FAMOU_D2_MARKET="EXPORT_MARKET"          # DOMESTIC_MARKET / EXPORT_MARKET，必须分开演化
export FAMOU_D2_FEATURE_VERSION="v2.1.0"        # v2.0.0 / v2.1.0 / v2.1.1
export FAMOU_D2_WAREHOUSE="WBJ"
export FAMOU_D2_TRAIN_START="2024-07-01"        # 可选；默认取对应市场生产配方
export FAMOU_D2_TRAIN_END="2026-09-15"
export FAMOU_D2_FOLDS="7"
```

## 版本前提（务必先确认）

`v2.1.0` / `v2.1.1` 依赖快照表 `dws_slot_temp_d2_order_visibility_di`，生产 loader 对该表是**硬失败**语义：缺列或 `data_ready<>1` 会直接抛错。

- `v2.1.0` 需要 4 列：`visible_order_cnt` / `visible_shop_cnt` / `order_cnt_ratio_28d` / `shop_cnt_ratio_28d` —— 当前表齐全。
- `v2.1.1` 额外需要 `visible_order_qty` 与 `order_qty_ratio_28d`。表里有 `visible_order_qty`，但**缺 `order_qty_ratio_28d`**（也无 `order_qty_avg_28d`），因此 v2.1.1 目前无法走生产 loader，需先在数仓补列并回刷存储过程。

## 联通检查

真实数据只读检查（约 40~90s，会连库）：

```bash
python runner_adapter.py init.py
```

输出为单行 JSON 指标，日志写 stderr，适合 OpenEvolve adapter 调用。

# famou-调参-v2

用 OpenEvolve 搜索 `slot_temp` D+2（`feature_version=v2.0.0`）的 LightGBM 参数。

与 v1 的关键差异：

- 评估器直接调用当前生产代码的数据加载、D+2 特征和 LightGBM 训练函数，不再把字典错传给 `run_train`。
- 只搜索实际生效的 LightGBM 参数，不搜索在途、组合分流、校准等业务开关。
- 整个评估链路只读：不调用 `run_train`，不写 registry、job log 或模型文件。
- 先按生产时序逻辑留出最终 Test，再仅在 Test 之前的数据上进行最多 5 折月度扩展窗口回测。

## 默认配置

- 市场：`DOMESTIC_MARKET`
- 仓库：`WBJ`
- 训练窗：`2025-04-01 ~ 2026-09-15`
- 标签：`future_1d_box_qty`（在 v2.0.0 中表示 D+2）
- 特征：v2.0.0 白名单 25 列
- 滚动折：最多 5 折，每折 1 个自然月，至少 4 个月历史

可通过环境变量覆盖：

```bash
export FAMOU_PRODUCTION_ROOT="/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/班次+库存+出库箱数/model"
export FAMOU_D2_MARKET="DOMESTIC_MARKET"  # 或 EXPORT_MARKET
export FAMOU_D2_WAREHOUSE="WBJ"
export FAMOU_D2_TRAIN_START="2025-04-01"
export FAMOU_D2_TRAIN_END="2026-09-15"
export FAMOU_D2_FOLDS="5"
```

跑外销时建议把开始日期改为 `2024-07-01`，并单独创建一个演化任务，不要把内外销混在同一轮搜索中。

## 联通检查

不读数据库的合成数据检查：

```bash
FAMOU_D2_USE_MOCK=1 python runner_adapter.py init.py
```

真实演化直接在 OpenEvolve 前端选择该模板。建议使用只读数据库账号；评估器本身仅执行查询。


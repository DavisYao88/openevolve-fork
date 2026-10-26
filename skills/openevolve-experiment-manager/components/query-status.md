# 组件 — 查询状态

**输入**：`run_id`（可选；缺省查全部任务）
**输出**：状态摘要 / 进度 / 日志 / 最佳程序

---

## 1. 列出全部任务

```bash
curl -s "http://127.0.0.1:8080/api/runs"
```

返回 `{"runs": [...]}`，按更新时间倒序。每条的关键字段：

- `id`、`name`、`template_name`、`model`
- `status`（pending/running/completed/failed/interrupted）
- `planned_iterations`（计划轮数）、`finished_iterations`、`current_iteration`
- `percent`、`best_metrics.combined_score`（最佳分）、`programs`（程序数）
- `last_error`、`updated_at`、`has_checkpoint`

## 2. 单任务进度（最权威）

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/progress"
```

返回：`status`、`total_iterations`、`current_iteration`、`finished_iterations`、`percent`、`tokens`、`checkpoints`、`islands`（岛屿）、`best_events`、`log_tail`、`has_checkpoint`。

## 3. 增量读日志

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/log?offset=0"
```

返回 `{offset, size, log, eof}`。轮询时把 `offset` 设为上次返回的 `offset`，只取新增字节；`eof: true` 表示读到文件末尾。

## 4. 检查点

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/checkpoints"
```

返回 `{checkpoints: [{name, iteration, programs, updated_at}, ...]}`。

## 5. 最佳程序 / 进化数据

```bash
# 完整进化数据（含最佳程序、节点、边、feature_maps、checkpoints）
curl -s "http://127.0.0.1:8080/api/data?run_id=<id>"

# 指定检查点
curl -s "http://127.0.0.1:8080/api/data?run_id=<id>&checkpoint=<checkpoint名>"
```

返回字段：`nodes`（程序列表，含 `metrics.combined_score`）、`edges`（父子关系）、`feature_maps`（MAP-Elites 热力图）、`archive`、`selected_checkpoint`。

找最佳程序：在 `nodes` 里取 `metrics.combined_score` 最大的节点。

## 6. 单程序详情

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/program/<program_id>"
```

## 7. 输出文件（最佳程序源码 / 信息）

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/output/best_program.py"
curl -s "http://127.0.0.1:8080/api/runs/<id>/output/best_program_info.json"
```

## 8. 轮询模板

任务在后台子进程跑，建议每 15–20 秒查一次 `/progress`，直到 `status` 进入终态（`completed` / `failed` / `interrupted`）。

```text
⏳ [HH:MM] <id> — status: running | iterations: 12/50 | best: 0.1234
```

终态后读 `/log?offset=0` 看结尾是否有报错，再决定是否报告完成。

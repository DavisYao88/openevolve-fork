# 组件 — 诊断（只读）

**输入**：`run_id`
**输出**：一份只读诊断，说明任务当前卡在哪、是否健康、下一步建议

诊断**不修改、不中断**运行中的任务，只读接口。

---

## 1. 当前进度

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/progress"
```

看：`status`、`current_iteration` / `total_iterations`、`failed_iterations`、`last_error`、`percent`。

## 2. 日志尾

```bash
curl -s "http://127.0.0.1:8080/api/runs/<id>/log?offset=0"
```

看日志最后若干行，判断：

- **卡在 LLM 生成**：出现 `LLM generation failed` / `tokens:` 后无下文 → 模型/网络问题。
- **卡在 diff 应用**：`SEARCH block(s) matched` / 反复 `Iteration N error: ... SEARCH` → 提示词与 diff 模式不匹配。
- **卡在评测**：`evaluat` 相关错误 / 超时 → 评测脚本慢或候选越界。
- **正常推进**：出现 `Iteration N: Program ... completed in ...s`。

## 3. 每轮失败分布

`progress.iterations` 是每轮明细数组。统计：

- 连续失败的轮次区间
- 失败 `stage`（`llm` / `diff` / `evaluation`）
- 失败 `error` 里的共性原因

## 4. 最佳程序与岛屿

```bash
curl -s "http://127.0.0.1:8080/api/data?run_id=<id>"
```

- `nodes` 里 `metrics.combined_score` 最高的节点即当前最佳。
- `feature_maps` 看 MAP-Elites 是否在填格（多样性）。
- `checkpoints` 看检查点是否正常产出（若长期无 checkpoint，可能是 `checkpoint_interval` 或任务没跑到保存点）。

## 5. 输出诊断结论

给用户三行：

```text
🔎 诊断 <id>
- 状态：running | 12/50 轮 | 失败 3 轮（均在 evaluation）
- 最佳：0.1234（迭代 9 发现）
- 判断：<健康 / 卡在 XX>，建议 <继续等待 / 查 XX / 调 YY>
```

- 若 `status` 已终态（completed/failed/interrupted），直接说明结果，无需「继续等待」。
- 诊断是只读的；若要改参数或终止，分别走 create-run（续跑）或 cancel-run。

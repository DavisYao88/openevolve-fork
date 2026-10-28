# 组件 — 续跑（resume）

**输入**：`run_id` +（可选）新轮数 / 指定检查点
**输出**：新 run 的 `run_id`

续跑 = 从该任务已有 checkpoint 恢复种群继续进化，任务定义沿用原任务。

---

## 1. 确认可续跑

```bash
curl -s "http://127.0.0.1:8080/api/runs/<run_id>" | python3 -c "
import json,sys
d=json.load(sys.stdin)['run']
print('状态:', d['status'], '| 已完成轮:', d['completed_iterations'], '| 计划轮:', d['planned_iterations'])
print('检查点:', [c['name'] for c in d.get('checkpoints', [])])
"
```

- `completed` / `interrupted` / `failed` 才值得续跑；`running` 的任务先别动（要停用 `POST /api/runs/<id>/cancel`）。
- `checkpoints` 为空说明没存过检查点（运行轮数小于 `checkpoint_interval` 时会发生），只能改用 fork 重跑。

## 2. 生成 payload

```bash
python3 <skill-path>/scripts/fork_build.py <run_id> --iterations <总轮数> --resume
# 指定某个检查点：
python3 <skill-path>/scripts/fork_build.py <run_id> --iterations <总轮数> --checkpoint checkpoint_10
```

脚本会自动补 `resume_from`，其余三份产物照常从原任务取回。

## 3. 提交

```bash
curl -s -X POST "http://127.0.0.1:8080/api/run" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d @/tmp/fork_payload.json
```

## 4. 轮数的语义

`iterations` 是**本次运行的总轮数上限**，不是「再追加多少轮」。底层命令固定为 `--iterations <N> --checkpoint <ckpt>`。

| 原任务 | 传入 iterations | 效果 |
|--------|----------------|------|
| 跑了 10 轮、计划 30 | 30 | 从第 10 轮继续，再跑 20 轮 |
| 跑了 10 轮、计划 30 | 50 | 从第 10 轮继续，再跑 40 轮 |
| 跑了 30 轮、已完成 | 40 | 从第 30 轮继续，再跑 10 轮 |

## 5. 什么情况不能用续跑

| 改动 | 原因 | 改用 |
|------|------|------|
| objective | checkpoint 里的程序分数按旧口径算，续跑会让新旧分数混排 | `fork-run.md` |
| evaluator（数据/切分） | 旧分数与新口径不可比 | `fork-run.md` |
| prompt | 技术上能续跑，但种群已是旧提示词的产物，收益接近零 | `fork-run.md` + `--use-best` |

## 6. 提交后

- 新任务目录是**新**的 `evolve_task_<时间戳>`，`task.json.resumed_from` 会记录来源。
- 首轮日志应出现 `Loaded checkpoint from ...`；没有这行说明检查点没找到，核对 `resume_from` 里的 checkpoint 名是否存在。

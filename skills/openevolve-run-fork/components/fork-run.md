# 组件 — 派生新任务（fork）

**输入**：`run_id` + 要覆盖的字段（可以只改一项）
**输出**：新 run 的 `run_id`（后台启动，返回 202）

派生 = 把原任务 `input/` 里的产物原样取回，组装成新 payload 再提交。种群不继承，新任务从种子解重新进化。

---

## 1. 一条命令生成 payload

脚本在本 skill 的 `scripts/fork_build.py`（只读，不改动任何已有任务）：

```bash
python3 <skill-path>/scripts/fork_build.py <run_id> \
  [--iterations N] [--name 新名字] [--use-best] \
  [--resume | --checkpoint checkpoint_10] \
  [--objective obj.json] [--prompt p.md] [--evaluator e.py] [--init i.py] \
  [--out /tmp/fork_payload.json] [--dry-run]
```

`run_id` 可以直接贴页面 URL（`http://127.0.0.1:4800/runs/evolve_task_xxx`），脚本会自己截取。

先用 `--dry-run` 看摘要，确认无误再去掉它生成文件：

```bash
python3 <skill-path>/scripts/fork_build.py evolve_task_20261028134036 \
  --iterations 40 --name "外销D+2-40轮" --use-best --dry-run
```

输出形如：

```json
{"source_run": "evolve_task_20261028134036", "source_status": "interrupted",
 "template": "generic-python", "task_name": "外销D+2-40轮", "iterations": 40,
 "resume_from": "", "objective_metrics": ["wmape", "bias_ratio", "worst_fold_wmape"],
 "sizes": {"prompt": 3016, "initial_program": 8845, "evaluator": 6609}}
```

## 2. 提交

```bash
curl -s -X POST "http://127.0.0.1:8080/api/run" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d @/tmp/fork_payload.json
```

成功返回 202，从 `run.id` 取新 run_id。

## 3. 脚本从哪取哪份文件

| payload 字段 | 来源 | 备注 |
|---|---|---|
| `prompt` | `input/prompt.md` | 整体替换，不是追加 |
| `initial_program` | `input/init.py` | 加 `--use-best` 则换成 `output/best/best_program.py` |
| `evaluator` | `input/runner_adapter.py` | **不是** `evaluator_callback.py`（那是后端公共评估器副本，回传会重复包装） |
| `objective` / `validation` / `template` | `input/task.json` | 已解析好的结构，直接用 |
| `iterations` | `GET /api/runs/<id>` 的 `planned_iterations` | 被 `--iterations` 覆盖 |

## 4. 可覆盖字段一览

| 字段 | 怎么改 | 注意 |
|------|--------|------|
| `iterations` | `--iterations N` | 必须 ≥ 1；后端会把 `checkpoint_interval` 收缩到不超过它 |
| `task_name` | `--name` | 建议加后缀区分，避免列表里同名 |
| `objective` | `--objective obj.json` | 改了就**不能**续跑，必须走 fork |
| `validation` | 手工改 payload | `mode: params_dict` 要求初始程序结构对齐；`none` 表示自由改写 |
| `prompt` | `--prompt p.md` | 换提示词的典型动机：补参考解、补已知坑位 |
| `initial_program` | `--init i.py` 或 `--use-best` | 见下节 |
| `evaluator` | `--evaluator e.py` | 换数据集、换验证切分都改这里 |

## 5. 用最优解当种子

`--use-best` 会读 `output/best/best_program.py`（`GET /api/runs/<id>` 的 `best_metrics` 非空即有）替换初始程序。

典型用途：

- 原任务被中断，想在新任务里从已有最好水平继续；
- 换了 objective，但仍想保留原来的解作为起点（注意它是按**旧口径**挑出来的，可能不再最优，通常仍优于原始种子）。

## 6. 常用场景

| 场景 | 命令要点 |
|------|----------|
| 只把 30 轮改成 50 轮、从零重跑 | `--iterations 50` |
| 换打分口径（加指标/改 scale/加 threshold） | `--objective` + `--use-best` |
| 换数据集或验证切分 | `--evaluator` |
| 换提示词（补参考解、补坑位） | `--prompt` |
| 什么都不改、再跑一遍看稳定性 | 直接 fork，`--name` 加 `-rerun` |
| 跑满剩余轮数、要保留种群 | 不要用 fork，见 `resume-run.md` |

## 7. 提交后

1. `GET /api/run` 确认 `status=running`。
2. 等基线程序评估完成，读新 run 的 `output/experiment.log` 里 `Evaluated program` 那行，把基线 `combined_score` 报给用户。
3. 基线就报错时，对照 `references/api-contract.md` 的「排错」表定位。

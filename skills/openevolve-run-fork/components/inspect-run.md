# 组件 — 查看原任务配置（inspect）

**输入**：`run_id`
**输出**：该任务的提示词、评测口径、轮数、最优解摘要（只读）

派生之前先看清楚原任务是什么，能避免改错字段。

---

## 1. 元信息

```bash
curl -s "http://127.0.0.1:8080/api/runs/<run_id>" | python3 -c "
import json,sys
d=json.load(sys.stdin)['run']
keep = ('id','name','template','status','planned_iterations','completed_iterations',
        'model','resumed_from','started_at','finished_at','best_metrics','checkpoints')
print(json.dumps({k: d.get(k) for k in keep}, ensure_ascii=False, indent=2))
"
```

`best_metrics` 非空说明已产出 `output/best/best_program.py`，可以拿来当新任务的种子。

## 2. 任务定义（objective / validation / 名字 / 模板）

```bash
curl -s "http://127.0.0.1:8080/api/runs/<run_id>/input/task.json" | python3 -c "
import json,sys
print(json.loads(json.load(sys.stdin)['content']))" 
```

输出包含 `name` / `template` / `objective` / `validation` / `resumed_from` / `created_at`。

## 3. 三份产物摘要（不打印全文）

```bash
for f in prompt.md init.py runner_adapter.py; do
  curl -s "http://127.0.0.1:8080/api/runs/<run_id>/input/$f" | python3 -c "
import json,sys
d=json.load(sys.stdin)
c=d['content']
print(d.get('name'), '|', len(c), 'chars |', c.count(chr(10))+1, 'lines')"
done
```

需要看具体内容时再单独取，例如只取前 40 行：

```bash
curl -s "http://127.0.0.1:8080/api/runs/<run_id>/input/prompt.md" \
  | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)['content'].splitlines()[:40]))"
```

## 4. 最优解

```bash
curl -s "http://127.0.0.1:8080/api/runs/<run_id>/output/best_program.py" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['content'][:2000])"
```

## 5. 汇报格式

给用户一个表，不要倾倒 JSON：

| 项 | 值 |
|---|---|
| 任务名 / 模板 | ... |
| 状态 / 轮数 | running 或 completed，已完成 x / 计划 y |
| objective | 指标名 + 方向 + scale + transform/threshold |
| 提示词 / 种子 / 评测脚本 | 各多少行 |
| 最优分 | 有则列出，无则说明还没产出 |

然后问一句要改哪项，或直接按用户已说明的改动执行 `fork-run.md`。

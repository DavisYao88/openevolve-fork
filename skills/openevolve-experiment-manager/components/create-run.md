# 组件 — 创建任务

**输入**：模板 + 提示词 + 初始程序 + 评测脚本 + 轮数（可选：任务名、objective/validation 覆盖、续跑来源）
**输出**：`run_id`（任务已后台启动，返回 202）

---

## 1. 先看模板默认内容

```bash
curl -s "http://127.0.0.1:8080/api/launch?template=<模板id>"
```

返回里的 `initial_program`、`evaluator`（模板的 runner_adapter）、`prompt`、`objective`、`validation` 是模板默认值，`iterations` 是默认轮数。

模板 id（以实际返回为准）：
- `famou-调参-v1` / `famou-调参-v2`：真实业务 LightGBM 参数调优
- `generic-python`：自由改写 `solve()`，无结构约束
- `param-tuning`：只改 `PARAMS` 字典取值

## 2. 组装 payload

`POST /api/run` 的 JSON 字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `template` | 是 | 模板 id |
| `prompt` | 是 | 任务提示词（Markdown 文本） |
| `initial_program` | 是 | 初始程序代码（种子） |
| `evaluator` | 是 | 评测脚本代码（receives 候选路径，stdout 打印一个指标 JSON） |
| `iterations` | 是 | 正整数 |
| `task_name` | 否 | 展示用，留空用模板名 |
| `objective` | 否 | YAML/JSON 文本或对象；留空沿用模板 |
| `validation` | 否 | 同上；留空沿用模板 |
| `resume_from` | 否 | `<run_id>` 或 `<run_id>/<checkpoint名>`；空串=不续跑 |

## 3. 提交

```bash
curl -s -X POST "http://127.0.0.1:8080/api/run" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d @payload.json
```

`payload.json` 示例：

```json
{
  "template": "param-tuning",
  "task_name": "示例调参",
  "prompt": "只调 PARAMS 取值，让 loss 最小。",
  "initial_program": "PARAMS = {'step_size': 0.01, 'momentum': 0.9}\n",
  "evaluator": "import json, sys\nprint(json.dumps({'loss': 0.1}))\n",
  "iterations": 30
}
```

成功返回 `202`，body：

```json
{ "run": { "id": "evolve_task_...", "status": "running", ... } }
```

记住 `run["id"]` 用于后续查询。

## 4. 错误处理

| 返回码 | 含义 | 处理 |
|--------|------|------|
| 400 | 参数非法（缺字段/轮数非正整数/未知模板/objective 无效） | 读 `error` 字段修 payload 重试 |
| 403 | 非本机请求 | 确认后端在本机、无跨域 Origin |
| 409 | 正在启动另一个任务 / 任务已结束 | 稍后重试 |
| 500 | 全局配置/模板读取失败 | 把 `error` 转述用户 |

## 5. 续跑（resume_from）

- `resume_from: "<run_id>"` → 从该任务最新检查点续跑。
- `resume_from: "<run_id>/<checkpoint名>"` → 指定检查点（checkpoint 名形如 `checkpoint_10`，来自 `GET /api/runs/<id>/checkpoints`）。
- 空串 / 不传 → 全新任务。

创建后按 `components/query-status.md` 轮询进度，不要阻塞等待。

# 参考 — 派生/续跑用到的接口

Base：`http://127.0.0.1:8080`。所有接口只接受本机回环请求。

## 读接口

| 接口 | 返回 | 用途 |
|------|------|------|
| `GET /api/runs/<run_id>` | `{run: {...}}` | 状态、轮数、模型、检查点、best_metrics |
| `GET /api/runs/<run_id>/input/task.json` | `{name,size,content}` | 任务名 / template / objective / validation |
| `GET /api/runs/<run_id>/input/prompt.md` | 同上 | 提示词全文 |
| `GET /api/runs/<run_id>/input/init.py` | 同上 | 初始程序全文 |
| `GET /api/runs/<run_id>/input/runner_adapter.py` | 同上 | **用户提交的评测脚本全文** |
| `GET /api/runs/<run_id>/input/evaluator_callback.py` | 同上 | 后端自带的公共评估器副本（**不要**用作 payload 的 evaluator） |
| `GET /api/runs/<run_id>/input/config.yaml` | 同上 | 完整运行配置（含 llm 段，key 已脱敏） |
| `GET /api/runs/<run_id>/output/best_program.py` | 同上 | 最优解源码 |
| `GET /api/runs/<run_id>/output/best_program_info.json` | 同上 | 最优解的指标快照 |
| `GET /api/run` | `{run: {...} \| null}` | 当前正在跑的任务；提交前先查这个 |
| `GET /api/launch?template=<id>` | 模板默认值 | 换模板时取默认 prompt/evaluator/objective |

`content` 是**字符串**，`task.json` 和 `best_program_info.json` 需要再 `json.loads` 一次。

`input/` 里到底有哪几份文件，以 `GET /api/runs/<id>` 返回的 `input_files` 为准：

```json
{"init.py": true, "prompt.md": true, "runner_adapter.py": true,
 "evaluator_callback.py": true, "config.yaml": true, "task.json": true}
```

## 写接口

| 接口 | 用途 |
|------|------|
| `POST /api/run` | 创建任务（派生和续跑都走它） |
| `POST /api/runs/<run_id>/cancel` | 终止运行中的任务 |

`POST /api/run` 必须带 `Content-Type: application/json` 和 `X-Requested-With: XMLHttpRequest`。

### payload 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `template` | 是 | 模板 id，沿用原任务的 `task.json.template` |
| `prompt` | 是 | 提示词全文 |
| `initial_program` | 是 | 初始程序全文 |
| `evaluator` | 是 | 评测脚本全文（取 `runner_adapter.py`） |
| `iterations` | 是 | **本次运行的总轮数上限**，≥ 1 |
| `task_name` | 否 | 展示名，建议加后缀区分 |
| `objective` | 否 | 缺省用模板自带；派生时应显式传原任务的值 |
| `validation` | 否 | 同上；`generic-python` 默认 `mode: none` |
| `resume_from` | 否 | `<run_id>` 或 `<run_id>/checkpoint_10`；空串 = 不续跑 |

## 返回码

| 码 | 含义 | 处理 |
|----|------|------|
| 202 | 已启动 | 取 `run.id` |
| 400 | 字段非法（缺必填 / 轮数非正整数 / 未知模板 / objective 无效） | 读 `error` 修正 payload 重试 |
| 403 | 非本机请求 | 确认后端在本机、无跨域 Origin |
| 409 | 已在启动另一个任务，或目标任务已结束（取消时） | 刷新 `GET /api/run` 后重试 |
| 500 | 全局配置或模板读取失败 | 把 `error` 原样转述 |

## 排错

| 现象 | 原因 | 处理 |
|------|------|------|
| 新任务每轮都 0 分、日志有 `stdout 里没有可解析的 JSON` | evaluator 传成了 `evaluator_callback.py`，或脚本本身不打印 JSON | 改成 `runner_adapter.py`；确认脚本以 `print(json.dumps(...))` 结尾 |
| `目标函数无法计算：评测结果缺少目标指标 xxx` | objective 里写的指标名，评测脚本没有输出 | 对齐指标名，或用 `transform`/`threshold` 调整而不是改名 |
| `未配置 objective，且评测结果没有 combined_score/score` | 没传 objective，脚本也没自己算总分 | 显式传 `objective`，或让脚本输出 `combined_score` |
| 首轮就 `候选程序缺少 fit_predict/predict` | 初始程序与评测脚本的接口不匹配 | 两者必须来自同一个任务，不要混用不同任务的产物 |
| `找不到可用于续跑的检查点` | `resume_from` 写错，或该任务没存过 checkpoint | 用 `GET /api/runs/<id>` 看 `checkpoints` 列表 |

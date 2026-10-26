# 本地启动说明（前后端）

> 本文件只讲怎么把本地环境跑起来，不改 README。

## 目录职责

| 目录 | 职责 |
|---|---|
| `solver/` | 核心功能：通用评估器 `evaluator.py`、任务模板 `templates/*` |
| `scripts/visualizer.py` | 原始单文件可视化器，提供全部 Flask 路由实现（HTML 页面、`/api/*`） |
| `backend/` | 对外服务层：常驻进程、异步任务、任务查询接口 |
| `frontend/` | Vue 3 + Vite + Ant Design Vue 前端 |
| `evolve_tasks/` | 运行产物：每个任务一个目录（`input/` 配置 + `output/` 日志/检查点） |

## 0. 一次性准备

```bash
# Python 依赖（项目根目录）
pip install -e .
pip install gunicorn            # 后端常驻用，不在默认依赖里

# 环境变量（密钥不写进任何配置文件）
cp .env.example .env.local
#   必填：OPENEVOLVE_API_KEY=sk-xxxx
#   选填：OPENEVOLVE_RUNNER_PYTHON=/abs/path/to/other/venv/bin/python
#        当评测脚本依赖装在别的 venv（例如生产模型工程自带 .venv）时指向它

# 前端依赖
cd frontend && npm install && cd ..
```

`.env.local` 的优先级高于设置页保存的 `evolve_tasks/llm_secrets.json`；它已在 `.gitignore` 里。

## 1. 启动后端（默认 8080）

```bash
./backend/serve.sh start      # 启动
./backend/serve.sh status     # 查看状态
./backend/serve.sh logs       # 跟踪日志（默认 /tmp/openevolve-backend.log）
./backend/serve.sh restart
./backend/serve.sh stop
```

- 用 gunicorn 常驻：`--workers 1 --worker-class gthread --threads 8 --timeout 0 --daemon`。
- **必须单 worker**：任务注册表（`current_run` / `active_runs`）在进程内存里，多 worker 会各记各的。
- 停止服务不影响已经在跑的演化任务：每个任务在独立子进程里（`start_new_session`），只是父进程丢失了 PID 和取消能力。

验证：

```bash
curl -s http://127.0.0.1:8080/api/settings
curl -s http://127.0.0.1:8080/api/jobs | head -c 300
```

## 2. 启动前端（默认 4800）

```bash
cd frontend
npm run dev
```

打开 http://127.0.0.1:4800 。

- vite 配置在 `frontend/vite.config.js`：`host 127.0.0.1`、`port 4800`。
- `/api` 和 `/manual/api` 由 vite 代理到后端 `http://127.0.0.1:8080`，并会移除 `Origin` 头（后端只接受同源本机请求）。
- 后端不在 8080 时，用环境变量指定：

```bash
VITE_BACKEND_ORIGIN=http://127.0.0.1:9000 npm run dev
```

生产构建：

```bash
cd frontend && npm run build     # 产物在 frontend/dist
```

## 3. 创建任务

页面上点「＋ 新建演化」最省事。要走接口的话注意两点：

- 只接受本机请求（`127.0.0.1` / `localhost`）。
- `POST /api/run` 必须带 `X-Requested-With: XMLHttpRequest`（防 CSRF），否则返回 400「请从本机进化配置页面提交任务。」
- `initial_program`、`evaluator`、`prompt` 是必填的，不会自动取模板默认值；先 `GET /api/launch` 拿默认值再提交最稳。
- 不续跑时 `resume_from` 传 `null` 或不传，别传空字符串。

```python
import json, urllib.request

BASE = "http://127.0.0.1:8080"

with urllib.request.urlopen(BASE + "/api/launch", timeout=30) as r:
    d = json.load(r)                       # 默认模板的 prompt / initial_program / evaluator

body = {
    "template": d["template"]["id"],
    "prompt": d["prompt"],
    "initial_program": d["initial_program"],
    "evaluator": d["evaluator"],
    "objective": d["objective_yaml"],
    "validation": d["validation_yaml"],
    "iterations": 50,
    "resume_from": None,
}
req = urllib.request.Request(
    BASE + "/api/run",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as r:
    print(r.status, json.load(r)["run"]["id"])   # 202 evolve_task_xxx
```

立即返回 `202` + `run_id`，演化在后台子进程异步执行，不会占住请求。

查询进度：

```bash
curl -s http://127.0.0.1:8080/api/runs/<run_id>            # 任务详情
curl -s http://127.0.0.1:8080/api/runs/<run_id>/progress   # 进度
curl -s http://127.0.0.1:8080/api/jobs                     # 全部任务
curl -s -X POST http://127.0.0.1:8080/api/runs/<run_id>/cancel
```

## 4. 任务状态是怎么判定的

没有数据库，状态来自三处：

1. 进程内存 `active_runs`：PID、退出码（服务重启即丢）。
2. 磁盘 `evolve_tasks/<run_id>/`：`input/config.yaml`（总轮数）、`output/experiment.log`、`output/checkpoints/`。
3. 日志推断 `_infer_run_status()`：完成标记 / 中断标记 / 轮次进度 / 日志 mtime。

规则要点：

- 日志有 `✅ Evolution completed` → `completed`
- 有 `Shutdown requested` 或 `KeyboardInterrupt` → `interrupted`
- 还没有任何轮次：出现 `Traceback` → `failed`；否则算 `running`（正在评估初始程序）
- 日志 15 分钟没有增长（`STALE_RUN_SECONDS`）→ `interrupted`

## 5. 常见问题

**页面显示 500 / 请求全部失败**
后端没起来。`./backend/serve.sh status`，再 `tail -50 /tmp/openevolve-backend.log`。

**4800 端口被别的项目占用**
vite 会自动换端口（例如 4801），看启动日志里的 `Local: http://127.0.0.1:xxxx`。代理配置不变，换个端口访问即可。

**页面提示“失败”，但任务明明在跑**
先看 `output/experiment.log` 有没有 `Iteration N` 和 `failed_iterations`。若日志在持续增长，刷新页面即可；若确实没有新日志且超过 15 分钟，就是 `interrupted`。

**每轮评测都是 0 分**
多半是评测子进程报错。查 `evolve_tasks/<run_id>/output/experiment.log` 里的 `评测命令` 相关行，常见两类：

- `ModuleNotFoundError`：评测脚本依赖的解释器不对，用 `OPENEVOLVE_RUNNER_PYTHON` 指向正确的 venv。
- `stdout 里没有可解析的 JSON 对象`：评测脚本把训练日志 print 到了 stdout，结果 JSON 被淹没。

**设置页改了模型不生效**
`/api/settings` 只影响**之后新建**的任务；已经在跑的不会变。另外环境变量（`.env.local`）优先于设置页。

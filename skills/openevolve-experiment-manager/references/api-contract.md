# API 契约参考

OpenEvolve 后端的 HTTP 契约。所有示例假设后端在 `http://127.0.0.1:8080`（`BASE` 变量）。

## 启动后端

```bash
./backend/serve.sh start      # 启动（daemon，单 worker + 多线程）
./backend/serve.sh status     # 看是否在跑
./backend/serve.sh logs       # 跟踪日志
./backend/serve.sh restart    # 重启
```

在仓库根目录执行；脚本会自动用 `OPENEVOLVE_HOST` / `OPENEVOLVE_PORT`（默认 `127.0.0.1:8080`）。

## 关键约定

- **本机 only**：写接口校验 `remote_addr` 是 loopback、host 是 localhost、且无跨域 Origin。curl 直接打本机即可。
- **创建任务头**：`POST /api/run` 必须带 `X-Requested-With: XMLHttpRequest`，否则 400。
- **run_id**：形如 `evolve_task_YYYYMMDDHHMMSS`（可能带 `_<hex>` 后缀）。任务目录在 `evolve_tasks/<run_id>/`。
- **轮询**：任务在独立子进程跑，接口非阻塞。状态查询建议每 10–20 秒一次。

## 端点速查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/launch[?template=<id>]` | 模板列表 + 默认编辑器内容 |
| POST | `/api/run` | 创建/续跑任务（202 + run） |
| GET | `/api/run` | 当前/最近任务状态 |
| GET | `/api/runs` | 全部任务摘要（状态/轮数/最佳分） |
| GET | `/api/runs/<id>` | 单任务详情 + 进度 + 输入文件清单 |
| GET | `/api/runs/<id>/progress` | 轻量实时进度 |
| GET | `/api/runs/<id>/log?offset=N` | 增量读日志 |
| GET | `/api/runs/<id>/checkpoints` | 检查点列表 |
| GET | `/api/runs/<id>/program/<pid>` | 跨检查点找单个程序 |
| GET | `/api/runs/<id>/files` | 输入/输出/检查点文件树 |
| GET | `/api/runs/<id>/file?path=<p>` | 读 run 内单个文件 |
| GET | `/api/runs/<id>/input/<name>` | 读输入文件（白名单） |
| GET | `/api/runs/<id>/output/<name>` | 读输出文件（白名单） |
| POST | `/api/runs/<id>/cancel` | 终止运行中任务 |
| POST | `/api/runs/<id>/export` | 静态导出可视化 |
| GET | `/api/data?run_id=<id>&checkpoint=<name>` | 进化树/MAP-Elites 数据 |
| GET | `/api/jobs` | 任务视角（含 PID/实时状态） |

## 状态取值

`status` ∈ `pending` | `running` | `completed` | `failed` | `interrupted`。

- `completed`：日志出现 `Evolution complete!` 或 `✅ Evolution completed`。
- `failed`：启动即崩（无任何迭代 + 有 Traceback）。
- `interrupted`：被取消 / 进程消失 / 轮数跑满但缺完成横幅。
- 状态判定以日志为准，`/api/runs/<id>/progress` 是最权威的单任务来源。

## 常用 curl 模板

```bash
BASE=http://127.0.0.1:8080
curl -s "$BASE/api/runs"                       # 列表
curl -s "$BASE/api/runs/<id>/progress"         # 进度
curl -s "$BASE/api/runs/<id>/log?offset=0"     # 日志（offset 递增做 tail）
curl -s "$BASE/api/runs/<id>/checkpoints"      # 检查点
curl -s "$BASE/api/data?run_id=<id>"           # 进化数据（最佳程序/特征地图）
```

返回都是 JSON。报错时 HTTP 非 2xx，body 形如 `{"error": "..."}`，把 `error` 字段转述给用户。

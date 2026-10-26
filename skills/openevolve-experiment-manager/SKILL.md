---
name: openevolve-experiment-manager
description: '通过 OpenEvolve 本机后端（http://127.0.0.1:8080）创建并查询进化实验。当用户想要在本机启动一个 OpenEvolve 进化/调参任务、提交实验、查询某个进化任务的状态/进度/日志/最佳程序、取消任务、续跑任务，或列出历史任务时触发。也适用于「帮我跑一个优化任务」「调参」「启动进化实验」「查一下任务跑到哪了」这类诉求。'
metadata:
  author: openevolve-group
  version: "1.0"
---

# OpenEvolve 实验管理器（路由）

把用户的实验诉求路由到正确的后端操作。本 skill 只负责**路由与顺序**，具体 API 细节、字段、错误处理都在 `references/` 与 `components/` 里。

## 前置条件

- 后端必须已在**本机**运行，默认 `http://127.0.0.1:8080`（`./backend/serve.sh start`）。
- 所有写接口只接受**本机回环请求**，且创建任务需带 `X-Requested-With: XMLHttpRequest` 头。
- 可用模板：`famou-调参-v1`、`famou-调参-v2`、`generic-python`、`param-tuning`（准确列表以 `GET /api/launch` 返回为准）。

## 路由规则

按用户意图，从下面选**恰好一个**操作，再读对应的组件文件执行：

| 用户意图 | 操作 | 组件 |
|----------|------|------|
| 新建/启动一个进化实验 | 创建任务 | `components/create-run.md` |
| 查询状态 / 进度 / 任务列表 | 查询状态 | `components/query-status.md` |
| 查看日志 / 最佳程序 / 检查点 / 文件 | 查询状态 | `components/query-status.md` |
| 诊断一个运行中/已结束的任务（只读分析） | 诊断 | `components/diagnose.md` |
| 取消 / 终止一个运行中的任务 | 取消任务 | `components/cancel-run.md` |
| 从已有任务续跑 | 续跑任务 | `components/create-run.md`（看 resume_from） |
| 后端没起 / 不知道端口 | 启动服务 | `references/api-contract.md` 里的「启动后端」 |

## 执行顺序

1. **路由**：从用户诉求判断上面属于哪一类。
2. **读组件**：打开对应组件文件，按里面给好的 curl 模板执行。
3. **回读结果**：把 JSON 结果里的关键字段（状态、轮数、最佳分、run_id）转述给用户，不要原样倾倒整个 JSON。

## 安全与边界

- 任务会在 Flask 所在机器**执行本地 Python 代码**。只对可信的初始程序/评测脚本提交；不要替用户凭空生成不可信的评测器代码去跑。
- 不要绕过 `_is_local_request` 限制，不要试图从远程主机触发。
- 密钥只通过 `.env.local` / 环境变量注入，skill 不读取、不打印明文 key。

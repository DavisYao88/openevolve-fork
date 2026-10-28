---
name: openevolve-run-fork
description: '基于已有 OpenEvolve 任务（run_id）派生新任务或续跑：改轮数、改目标函数 objective、换提示词/评测脚本/初始程序、换任务名，一键重新提交，不用重新准备 prompt/evaluator/seed。当用户说「用 xxx 任务再跑一次」「把轮数改成 50 再提交」「换个 objective 重跑」「基于上次的结果继续跑」「复制这个任务改参数」「在原来基础上再提交一个」时触发。'
metadata:
  author: openevolve-group
  version: "1.0"
---

# 基于已有任务派生 / 续跑（路由）

用户手上通常已经有跑过的任务，只是要改一点东西再提交。**不要重新生成 prompt、evaluator、seed**——它们都完整保存在该任务的 `input/` 目录里，通过 API 就能原样取回。

## 前置条件

- 后端在本机运行，默认 `http://127.0.0.1:8080`（`./backend/serve.sh start`）。
- 创建任务必须带 `X-Requested-With: XMLHttpRequest` 头，且只能本机请求。
- 同一时刻只允许一个任务在跑；提交前先 `GET /api/run` 确认没有 running 的任务。

## 路由

| 用户意图 | 组件 |
|----------|------|
| 只是加轮数、想保留已经进化出来的种群 | `components/resume-run.md` |
| 改 objective / prompt / evaluator / 初始程序 / 任务名 | `components/fork-run.md` |
| 先看原任务到底用了什么（提示词、评测口径、轮数） | `components/inspect-run.md` |
| 想把原任务的最优解当成新任务的起点 | `components/fork-run.md` 的「用最优解当种子」 |
| 接口字段、错误码、取哪个文件 | `references/api-contract.md` |

## 四条必须遵守的判定（最容易做错的地方）

1. **三份产物从 `input/` 取，不要重新写。** `init.py` = 初始程序，`prompt.md` = 提示词，`runner_adapter.py` = 用户提交的评测脚本。
2. **评测脚本取 `runner_adapter.py`，不是 `evaluator_callback.py`。** 后者是后端自带的公共评估器副本，取回来再提交会被重复包装，指标全错。
3. **objective 从 `input/task.json` 取。** 它和 `config.yaml` 里的 `evaluator.evaluator_args.objective` 是同一份，但 task.json 已经解析好结构，直接可用。
4. **改了打分口径（objective / evaluator）就只能派生新任务，不能续跑。** checkpoint 里的历史程序是按旧口径打的分，续跑会让旧分数和新分数混在一个种群里排序。

## 执行顺序

1. 规范化 run_id：用户可能贴整条 URL（`http://127.0.0.1:4800/runs/evolve_task_xxx`），从中取出最后一段。
2. 读 `components/inspect-run.md` 把原任务的关键信息列给用户确认（轮数、objective、任务名）。
3. 按上面路由表选一个组件执行。
4. 提交后把新 run_id、改了哪几项、与原任务的差异转述给用户；不要原样倾倒整个 JSON。

## 安全边界

- 任务会在本机执行 Python 代码。派生只复用**该项目自己产生过**的代码与数据，不要替用户凭空塞入新的评测逻辑。
- 密钥不进 payload，由后端通过 `.env.local` / 环境变量注入。

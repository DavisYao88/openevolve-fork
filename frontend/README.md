# OpenEvolve Vue frontend

This is a standalone Vue 3 + Vite frontend. The Flask visualizer remains the API
server and continues to own task execution, task files, checkpoints, and feedback
queue operations.

## Local development

Start the Flask API in one terminal:

```bash
python scripts/visualizer.py --host 127.0.0.1 --port 8080
```

Install dependencies and start Vue in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://127.0.0.1:5173`).
Vite proxies `/api` and `/manual/api` to Flask and removes the browser `Origin`
header on the local proxy connection, matching Flask's local-only API policy.
Set `VITE_BACKEND_ORIGIN` to change the proxy target.

## Pages

| Route | Page | What it does |
| --- | --- | --- |
| `/` | 可视化 | 进化树（滚轮缩放/拖拽平移）、指标趋势、程序列表、MAP-Elites 特征地图；指标下拉、高亮规则、检查点切换、运行进度与岛屿面板 |
| `/runs` | 任务列表 | 轮询任务状态、模型、轮数/程序数、最高分、模板；进入详情或直接可视化 |
| `/runs/<id>` | 任务详情 | 运行状态、岛屿、检查点列表、输入文件、全量日志（增量 tail）；停止运行、静态导出 |
| `/launch` | 新建进化 | 选任务模板 → 填任务名/提示词/初始程序/评测脚本/轮数；可选续跑来源；objective/validation YAML 高级配置 |
| `/program/<id>` | 程序详情 | 代数/岛屿/复杂度/多样性、变更说明、代码、提示词、父程序指标、产物 |
| `/manual` | 人工反馈 | 待回答任务列表与提交 |

Backend endpoints used: `GET /api/data[?run_id&checkpoint]`, `/api/runs`,
`/api/runs/<id>`, `/api/runs/<id>/progress|log|checkpoints`, `POST /api/runs/<id>/cancel|export`,
`GET|POST /api/run`, `GET /api/launch[?template]`, `/manual/api/tasks*`.

## Production

Build with `npm run build`; generated static assets are written to `dist/`.
The current Flask API intentionally accepts local requests only. A deployment
outside local development needs a trusted reverse proxy that forwards API
requests to Flask over loopback and preserves that local-only boundary. Do not
expose the API directly to an untrusted network; add authentication and an
explicit origin policy before doing so.

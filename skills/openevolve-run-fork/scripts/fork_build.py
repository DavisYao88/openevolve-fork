#!/usr/bin/env python3
"""基于已有 OpenEvolve 任务生成派生/续跑用的 payload。

用法：
    python3 fork_build.py <run_id> [选项]

选项：
    --iterations N   本次运行的总轮数上限
    --name NAME      新任务名
    --use-best       用原任务的最优解（output/best/best_program.py）当新种子
    --resume         加上 resume_from（续跑，沿用原任务检查点）
    --checkpoint CK  指定检查点名（形如 checkpoint_10），隐含 --resume
    --objective PATH 用文件内容替换 objective（JSON 或 YAML）
    --prompt PATH    用文件内容替换提示词
    --evaluator PATH 用文件内容替换评测脚本
    --init PATH      用文件内容替换初始程序
    --out PATH       输出 payload 路径，默认 /tmp/fork_payload.json
    --dry-run        只打印摘要，不写文件

默认只读，不改动任何已有任务。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def get(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GET {path} 失败：HTTP {error.code} {body}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"连不上后端 {BASE}：{error.reason}（先 ./backend/serve.sh start）") from error


def text_of(run_id: str, name: str) -> str:
    return get(f"/api/runs/{run_id}/input/{name}")["content"]


def normalize_run_id(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw.startswith("http"):
        raw = raw.rsplit("/", 1)[-1]
    if not raw.startswith("evolve_task_"):
        raise SystemExit(f"看起来不是 run_id：{raw}")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("run_id")
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--name")
    parser.add_argument("--use-best", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--objective")
    parser.add_argument("--prompt")
    parser.add_argument("--evaluator")
    parser.add_argument("--init")
    parser.add_argument("--out", default="/tmp/fork_payload.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_id = normalize_run_id(args.run_id)
    meta = get(f"/api/runs/{run_id}")["run"]
    task = json.loads(text_of(run_id, "task.json"))

    payload = {
        "template": task["template"],
        "task_name": args.name or task["name"],
        "prompt": text_of(run_id, "prompt.md"),
        "initial_program": text_of(run_id, "init.py"),
        "evaluator": text_of(run_id, "runner_adapter.py"),
        "iterations": (
            args.iterations
            if args.iterations is not None
            else (meta.get("planned_iterations") or 30)
        ),
        "objective": task.get("objective"),
        "validation": task.get("validation"),
    }

    if args.prompt:
        payload["prompt"] = pathlib.Path(args.prompt).read_text(encoding="utf-8")
    if args.evaluator:
        payload["evaluator"] = pathlib.Path(args.evaluator).read_text(encoding="utf-8")
    if args.init:
        payload["initial_program"] = pathlib.Path(args.init).read_text(encoding="utf-8")
    if args.objective:
        payload["objective"] = json.loads(pathlib.Path(args.objective).read_text(encoding="utf-8"))
    if args.use_best:
        payload["initial_program"] = get(f"/api/runs/{run_id}/output/best_program.py")["content"]
    if args.checkpoint:
        payload["resume_from"] = f"{run_id}/{args.checkpoint}"
    elif args.resume:
        payload["resume_from"] = run_id

    if not isinstance(payload["iterations"], int) or payload["iterations"] < 1:
        raise SystemExit(f"iterations 必须是正整数：{payload['iterations']}")

    summary = {
        "source_run": run_id,
        "source_status": meta.get("status"),
        "template": payload["template"],
        "task_name": payload["task_name"],
        "iterations": payload["iterations"],
        "resume_from": payload.get("resume_from", ""),
        "objective_metrics": [m.get("name") for m in (payload.get("objective") or {}).get("metrics", [])],
        "sizes": {
            "prompt": len(payload["prompt"]),
            "initial_program": len(payload["initial_program"]),
            "evaluator": len(payload["evaluator"]),
        },
    }

    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["payload"] = str(out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

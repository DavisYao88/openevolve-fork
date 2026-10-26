"""异步演化任务的查询接口。

提交任务仍然走 ``POST /api/run``：它会立刻返回 202 + run_id，演化在独立子进程里
后台执行，不会占住请求线程。这里提供任务视角的查询，方便脚本或不带前端的客户端轮询。
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify

from ..core import job_row, job_rows, viz

jobs_api = Blueprint("jobs", __name__)


@jobs_api.get("/api/jobs")
def list_jobs():
    """所有演化任务，按目录时间倒序；正在跑的任务带上 PID 与实时状态。"""
    if not viz._is_local_request():
        abort(403)
    return jsonify({"jobs": job_rows()})


@jobs_api.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    if not viz._is_local_request():
        abort(403)
    row = job_row(job_id)
    if row is None:
        return jsonify({"error": f"找不到任务：{job_id}"}), 404
    return jsonify({"job": row})

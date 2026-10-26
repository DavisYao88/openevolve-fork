"""演化任务的查询接口（任务列表 / 详情 / 文件 / 日志 / 进度 / 检查点 / 取消）。

注意：``/api/runs/<id>/export`` 留在 visualizer.py——它紧贴上游的 ``run_static_export``。
"""

from __future__ import annotations

import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Optional

from flask import Blueprint, abort, jsonify, render_template, request

from .. import paths, state
from ..evolution_data import load_evolution_data
from ..runs import (
    _build_file_tree,
    _checkpoints_root,
    _current_run_status,
    _list_checkpoints,
    _list_runs,
    _reap_finished_runs,
    _resolve_checkpoint,
    _resolve_run_dir,
    _run_progress,
    _run_summary,
)
from .common import _is_local_request

logger = logging.getLogger(__name__)

runs_api_bp = Blueprint("runs_api", __name__)


@runs_api_bp.route("/runs")
def runs_page():
    if not _is_local_request():
        abort(403)
    return render_template("runs.html")


@runs_api_bp.route("/api/runs")
def runs_api():
    if not _is_local_request():
        abort(403)
    _reap_finished_runs()
    return jsonify({"runs": _list_runs()})


@runs_api_bp.route("/api/runs/<run_id>")
def run_detail_api(run_id):
    """Full detail for one run: summary + live progress + input copies."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    progress = _run_progress(run_dir)
    detail = _run_summary(run_dir)
    detail.update(progress)
    # progress["iterations"] 是每轮明细数组，会覆盖计划轮数；
    # 这里保留两个语义清晰的键供 UI 使用。
    detail["iteration_details"] = progress["iterations"]

    input_dir = run_dir / "input"
    detail["input_files"] = {
        name: (input_dir / name).is_file()
        for name in paths._INPUT_FILE_NAMES
    }
    return jsonify({"run": detail})


@runs_api_bp.route("/api/runs/<run_id>/input/<name>")
def run_input_file_api(run_id, name):
    """Return the text content of one saved input file for preview."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    if name not in paths._INPUT_FILE_NAMES:
        return jsonify({"error": "不支持的文件。"}), 400
    path = run_dir / "input" / name
    if not path.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500
    truncated = len(text) > 200_000
    if truncated:
        text = text[:200_000]
    return jsonify({"name": name, "size": path.stat().st_size, "truncated": truncated, "content": text})


@runs_api_bp.route("/api/runs/<run_id>/output/<name>")
def run_output_file_api(run_id, name):
    """Return the text content of one saved output file (best program etc.)."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    if name not in paths._OUTPUT_FILES:
        return jsonify({"error": "不支持的文件。"}), 400
    path = run_dir.joinpath(*paths._OUTPUT_FILES[name])
    if not path.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500
    truncated = len(text) > 200_000
    if truncated:
        text = text[:200_000]
    return jsonify({"name": name, "size": path.stat().st_size, "truncated": truncated, "content": text})


@runs_api_bp.route("/api/runs/<run_id>/files")
def run_files_api(run_id):
    """List a run's input / output / checkpoint files as a tree for browsing."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    sections = []
    # 显示名 / 实际目录 / 读取文件时使用的相对前缀
    for name, base, prefix in (
        ("input", run_dir / "input", "input"),
        ("output", run_dir / "output", "output"),
        ("checkpoints", run_dir / "output" / "checkpoints", "output/checkpoints"),
    ):
        if not base.is_dir():
            continue
        children = _build_file_tree(base, prefix.split("/"))
        if name == "output":
            # checkpoints 单独列为一段，避免重复
            children = [item for item in children if item["name"] != "checkpoints"]
        sections.append({"type": "dir", "name": name, "path": prefix, "children": children})

    return jsonify({
        "run_id": run_id,
        "run_dir": Path(os.path.relpath(run_dir, paths.PROJECT_ROOT)).as_posix(),
        "tree": sections,
    })


@runs_api_bp.route("/api/runs/<run_id>/file")
def run_file_api(run_id):
    """Read one file inside the run folder. `path` must stay inside run_dir."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    relative = request.args.get("path", "")
    if not relative:
        return jsonify({"error": "缺少 path 参数。"}), 400
    base = run_dir.resolve()
    target = (run_dir / relative).resolve()
    if str(target) != str(base) and not str(target).startswith(str(base) + os.sep):
        return jsonify({"error": "路径不合法。"}), 400
    if not target.is_file():
        return jsonify({"error": "文件不存在。"}), 404
    try:
        raw = target.read_bytes()
    except OSError as error:
        return jsonify({"error": f"读取失败：{error}"}), 500

    truncated = len(raw) > 300_000
    body = raw[:300_000] if truncated else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "该文件不是 UTF-8 文本文件（可能是二进制产物）。", "binary": True}), 415

    return jsonify({
        "name": target.name,
        "path": relative,
        "size": len(raw),
        "truncated": truncated,
        "content": text,
    })


@runs_api_bp.route("/api/runs/<run_id>/program/<program_id>")
def run_program_api(run_id, program_id):
    """Find one program across every checkpoint of a run.

    Programs from early iterations often disappear from later checkpoints, so the
    program page needs a lookup that is not limited to the newest checkpoint.
    """
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    root = _checkpoints_root(run_dir)
    if not root.is_dir():
        return jsonify({"error": "该任务还没有 checkpoint。"}), 404

    def checkpoint_number(path):
        try:
            return int(path.name.rsplit("_", 1)[-1])
        except (IndexError, ValueError):
            return -1

    folders = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith("checkpoint_")),
        key=checkpoint_number,
        reverse=True,
    )
    for folder in folders:
        try:
            data = load_evolution_data(str(folder))
        except (OSError, ValueError):
            continue
        match = next((node for node in data.get("nodes", []) if node.get("id") == program_id), None)
        if match is None:
            continue
        parent = next((node for node in data.get("nodes", []) if node.get("id") == match.get("parent_id")), None)
        return jsonify({
            "run_id": run_id,
            "checkpoint": folder.name,
            "node": match,
            "parent": parent,
            "archive": data.get("archive", []),
            "checkpoints": _list_checkpoints(run_dir),
        })

    return jsonify({"error": f"在所有检查点里都没有找到程序：{program_id}"}), 404


@runs_api_bp.route("/api/runs/<run_id>/log")
def run_log_api(run_id):
    """Tail a run's console log. Pass ?offset=N to get only bytes after N."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    log_path = run_dir / "output" / "experiment.log"
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return jsonify({"run_id": run_id, "offset": 0, "size": 0, "log": "", "eof": True})

    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "offset 必须是整数。"}), 400
    offset = max(0, min(offset, size))
    max_bytes = 256 * 1024
    with open(log_path, "rb") as log_file:
        log_file.seek(offset)
        chunk = log_file.read(max_bytes)
    return jsonify(
        {
            "run_id": run_id,
            "offset": offset + len(chunk),
            "size": size,
            "log": chunk.decode("utf-8", errors="replace"),
            "eof": offset + len(chunk) >= size,
        }
    )


@runs_api_bp.route("/api/runs/<run_id>/progress")
def run_progress_api(run_id):
    """Lightweight polling endpoint for the live progress of one run."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    return jsonify({"run_id": run_id, **_run_progress(run_dir)})


@runs_api_bp.route("/api/runs/<run_id>/checkpoints")
def run_checkpoints_api(run_id):
    """List the checkpoints of one run so the UI can switch between them."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404
    return jsonify({"run_id": run_id, "checkpoints": _list_checkpoints(run_dir)})


@runs_api_bp.route("/api/runs/<run_id>/cancel", methods=["POST"])
def run_cancel_api(run_id):
    """Terminate a running task started from this service."""
    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    _reap_finished_runs()
    run = state.active_runs.get(run_id)
    if run is None and state.current_run is not None and state.current_run.get("id") == run_id:
        run = state.current_run
    if run is None:
        return jsonify({"error": "该任务不是由本服务启动，或已经结束。"}), 409

    process = run["process"]
    if process.poll() is not None:
        return jsonify({"error": "该任务已经结束。"}), 409

    try:
        # The child was started with start_new_session=True, so kill the group.
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as error:
        logger.warning("Could not terminate run %s: %s", run_id, error)
        return jsonify({"error": f"无法终止任务：{error}"}), 500

    run["status"] = "interrupted"
    logger.info("Cancelled evolution run %s", run_id)
    return jsonify({"run": _current_run_status()})

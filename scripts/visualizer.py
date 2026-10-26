"""OpenEvolve 进化可视化器（瘦身版）。

本文件保留上游（origin/main，约 244 行）的路由与函数，团队新增的逻辑已迁到
``backend/`` 下的模块与 blueprint。这样同步上游时，冲突面收窄到本文件里少数几个
被团队增强（feature_maps / run_id 参数）的函数/路由。

保留内容：
- 上游原样：``/``、``/api/data``、``/program/<id>``、manual blueprint、``__main__``；
- 上游函数 ``run_static_export``（依赖 templates/static 与 app context，留在原地）；
- ``/api/runs/<id>/export``（紧贴 ``run_static_export``，留在一起）；
- 数据函数 re-export 自 ``backend.evolution_data`` / ``backend.runs``，以兼容
  上游测试（``from visualizer import check_json_float`` 等）。
"""

from __future__ import annotations

import os
import re as _re
import shutil
import sys
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# 让 `python scripts/visualizer.py` 独立运行时也能 import backend（单文件入口）。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import state  # noqa: E402
from backend.evolution_data import (  # noqa: E402
    _build_feature_maps,
    check_json_float,
    load_evolution_data,
    sanitize_program_for_visualization,
)
from backend.runs import (  # noqa: E402
    _checkpoints_root,
    _list_checkpoints,
    _resolve_checkpoint,
    _resolve_run_dir,
    _resolve_run_output,
    find_latest_checkpoint,
)
from backend.secrets import _migrate_legacy_secrets  # noqa: E402

from manual import create_manual_blueprint  # noqa: E402

logger = logging.getLogger(__name__)
app = Flask(__name__, template_folder="templates")

# Global variable to store the checkpoint directory
checkpoint_dir = None


@app.route("/")
def index():
    return render_template("index.html", checkpoint_dir=checkpoint_dir)


@app.route("/api/data")
def data():
    global checkpoint_dir
    run_id = request.args.get("run_id", "").strip()
    checkpoint_name = request.args.get("checkpoint", "").strip()
    run_dir = None
    if run_id:
        run_dir = _resolve_run_dir(run_id)
        if run_dir is None:
            return jsonify({"error": f"找不到任务：{run_id}"}), 404
        base_folder = str(run_dir / "output")
    else:
        base_folder = (
            state.current_run["output_dir"]
            if state.current_run is not None
            else os.environ.get("EVOLVE_OUTPUT", "examples/")
        )

    selected = None
    if run_dir is not None and checkpoint_name:
        selected = _resolve_checkpoint(run_dir, checkpoint_name)
        if selected is None:
            return jsonify({"error": f"找不到检查点：{checkpoint_name}"}), 404

    checkpoint_dir = str(selected) if selected is not None else find_latest_checkpoint(base_folder)
    if not checkpoint_dir:
        logger.info(f"No checkpoints found in {base_folder}")
        payload = {"archive": [], "nodes": [], "edges": [], "checkpoint_dir": ""}
        if run_dir is not None:
            payload["checkpoints"] = _list_checkpoints(run_dir)
        return jsonify(payload)

    logger.info(f"Loading data from checkpoint: {checkpoint_dir}")
    data = load_evolution_data(checkpoint_dir)
    logger.debug(f"Data: {data}")
    if run_dir is not None:
        data["checkpoints"] = _list_checkpoints(run_dir)
        resolved_root = str(_checkpoints_root(run_dir).resolve())
        resolved_dir = str(Path(checkpoint_dir).resolve()) if checkpoint_dir else ""
        data["selected_checkpoint"] = (
            Path(checkpoint_dir).name if resolved_dir.startswith(resolved_root) else ""
        )
    return jsonify(data)


@app.route("/program/<program_id>")
def program_page(program_id):
    global checkpoint_dir
    run_id = request.args.get("run_id", "").strip()
    if run_id:
        base_folder = _resolve_run_output(run_id)
        if base_folder is None:
            return f"找不到任务：{run_id}", 404
        resolved = find_latest_checkpoint(base_folder)
        if resolved is None:
            return f"任务 {run_id} 还没有 checkpoint", 404
        checkpoint_dir = resolved
    if checkpoint_dir is None:
        return "No checkpoint loaded", 500

    data = load_evolution_data(checkpoint_dir)
    program_data = next((p for p in data["nodes"] if p["id"] == program_id), None)
    program_data = {"code": "", "prompts": {}, **(program_data or {})}
    artifacts_json = program_data.get("artifacts_json", None)

    return render_template(
        "program_page.html",
        program_data=program_data,
        checkpoint_dir=checkpoint_dir,
        artifacts_json=artifacts_json,
    )


@app.route("/api/runs/<run_id>/export", methods=["POST"])
def run_export_api(run_id):
    """Write a self-contained static copy of the visualization for one run."""
    import argparse

    from flask import abort

    from backend.api.common import _is_local_request

    if not _is_local_request():
        abort(403)
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({"error": f"找不到任务：{run_id}"}), 404

    source = str(run_dir / "output")
    checkpoint_name = ""
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and isinstance(payload.get("checkpoint"), str):
            checkpoint_name = payload["checkpoint"].strip()
    if not checkpoint_name:
        checkpoint_name = (request.args.get("checkpoint") or "").strip()
    if checkpoint_name:
        resolved = _resolve_checkpoint(run_dir, checkpoint_name)
        if resolved is None:
            return jsonify({"error": f"找不到检查点：{checkpoint_name}"}), 404
        source = str(resolved)

    static_dir = run_dir / "static"
    try:
        run_static_export(argparse.Namespace(static_output=str(static_dir), path=source))
    except (OSError, RuntimeError, ValueError) as error:
        logger.exception("Could not export static visualization")
        return jsonify({"error": f"静态导出失败：{error}"}), 500

    return jsonify(
        {
            "run_id": run_id,
            "static_dir": str(static_dir),
            "index": str(static_dir / "index.html"),
            "checkpoint": Path(source).name if checkpoint_name else "",
        }
    )


def run_static_export(args):
    output_dir = args.static_output
    os.makedirs(output_dir, exist_ok=True)

    # Load data and prepare JSON string
    checkpoint_dir = find_latest_checkpoint(args.path)
    if not checkpoint_dir:
        raise RuntimeError(f"No checkpoint found in {args.path}")
    data = load_evolution_data(checkpoint_dir)
    logger.info(f"Exporting visualization for checkpoint: {checkpoint_dir}")

    with app.app_context():
        data_json = jsonify(data).get_data(as_text=True)
    inlined = f"<script>window.STATIC_DATA = {data_json};</script>"

    # Load index.html template
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    template_path = os.path.join(templates_dir, "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Insert static json data into the HTML
    html = _re.sub(r"\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}", r"static/\1", html)
    script_tag_idx = html.find('<script type="module"')

    if script_tag_idx != -1:
        html = html[:script_tag_idx] + inlined + "\n" + html[script_tag_idx:]
    else:
        html = html.replace("</body>", inlined + "\n</body>")

    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # Copy over static files
    static_src = os.path.join(os.path.dirname(__file__), "static")
    static_dst = os.path.join(output_dir, "static")
    if os.path.exists(static_dst):
        shutil.rmtree(static_dst)
    shutil.copytree(static_src, static_dst)

    logger.info(
        f"Static export written to {output_dir}/\n"
        f"Note: use a web server, not file://. "
        f"Try: python3 -m http.server --directory {output_dir} 8080"
    )


# Manual mode blueprint mounted at /manual
app.register_blueprint(
    create_manual_blueprint(
        lambda: (
            state.current_run["output_dir"]
            if state.current_run is not None
            else os.environ.get("EVOLVE_OUTPUT", "examples/")
        )
    )
)


if __name__ == "__main__":
    import argparse

    # 旧版把 api_key 明文写在 gui_settings/global_config 里，启动时搬到独立 secrets 文件
    _migrate_legacy_secrets()

    parser = argparse.ArgumentParser(description="OpenEvolve Evolution Visualizer")
    parser.add_argument(
        "--path",
        type=str,
        default="examples/",
        help="Path to OpenEvolve run output directory (e.g. openevolve_output) or its checkpoints/checkpoint_*.",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode (enables the interactive debugger; local use only)",
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    parser.add_argument(
        "--static-output",
        type=str,
        default=None,
        help="Produce a static HTML export in this directory and exit.",
    )
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    logger.info(f"Current working directory: {os.getcwd()}")

    if args.static_output:
        run_static_export(args)

    os.environ["EVOLVE_OUTPUT"] = args.path
    logger.info(
        f"Starting server at http://{args.host}:{args.port} with log level {args.log_level.upper()}"
    )
    logger.info(f"Manual UI: http://{args.host}:{args.port}/manual")
    app.run(host=args.host, port=args.port, debug=args.debug)

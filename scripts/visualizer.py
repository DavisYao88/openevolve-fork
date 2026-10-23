import glob
import ipaddress
import json
import logging
import math
import os
import re as _re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from numbers import Number
from typing import Any, Optional
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request
from manual import create_manual_blueprint

logger = logging.getLogger(__name__)
app = Flask(__name__, template_folder="templates")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_RUNS_DIR = PROJECT_ROOT / "openevolve_gui_runs"
current_run: Optional[dict[str, Any]] = None
run_lock = threading.Lock()


def find_latest_checkpoint(base_folder):
    base_folder = os.path.normpath(base_folder)
    # Check whether the base folder is itself a checkpoint folder
    if os.path.basename(base_folder).startswith("checkpoint_"):
        return base_folder

    checkpoint_folders = glob.glob("**/checkpoint_*", root_dir=base_folder, recursive=True)
    if not checkpoint_folders:
        logger.info(f"No checkpoint folders found in {base_folder}")
        return None
    checkpoint_folders = [os.path.join(base_folder, folder) for folder in checkpoint_folders]
    checkpoint_folders.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    logger.debug(f"Found checkpoint folder: {checkpoint_folders[0]}")
    return checkpoint_folders[0]


def load_evolution_data(checkpoint_folder):
    meta_path = os.path.join(checkpoint_folder, "metadata.json")
    programs_dir = os.path.join(checkpoint_folder, "programs")
    if not os.path.exists(meta_path) or not os.path.exists(programs_dir):
        logger.info(f"Missing metadata.json or programs dir in {checkpoint_folder}")
        return {"archive": [], "nodes": [], "edges": [], "checkpoint_dir": checkpoint_folder}
    with open(meta_path) as f:
        meta = json.load(f)

    nodes = []
    id_to_program = {}
    pids = set()
    for island_idx, id_list in enumerate(meta.get("islands", [])):
        for pid in id_list:
            prog_path = os.path.join(programs_dir, f"{pid}.json")

            # Keep track of PIDs and if one is double, append "-copyN" to the PID
            if pid in pids:
                base_pid = pid

                # If base_pid already has a "-copyN" suffix, strip it
                if "-copy" in base_pid:
                    base_pid = base_pid.rsplit("-copy", 1)[0]

                # Find the next available copy number
                copy_num = 1
                while f"{base_pid}-copy{copy_num}" in pids:
                    copy_num += 1
                pid = f"{base_pid}-copy{copy_num}"
            pids.add(pid)

            if os.path.exists(prog_path):
                with open(prog_path) as pf:
                    prog = json.load(pf)
                    sanitize_program_for_visualization(prog)
                prog["id"] = pid
                prog["island"] = island_idx
                nodes.append(prog)
                id_to_program[pid] = prog
            else:
                logger.debug(f"Program file not found: {prog_path}")

    edges = []
    for prog in nodes:
        parent_id = prog.get("parent_id")
        if parent_id and parent_id in id_to_program:
            edges.append({"source": parent_id, "target": prog["id"]})

    logger.info(f"Loaded {len(nodes)} nodes and {len(edges)} edges from {checkpoint_folder}")
    return {
        "archive": meta.get("archive", []),
        "nodes": nodes,
        "edges": edges,
        "checkpoint_dir": checkpoint_folder,
    }


def sanitize_program_for_visualization(program: dict[str, Any]) -> None:
    for k, v in program["metrics"].items():
        if not check_json_float(v):
            program["metrics"][k] = None
        if "parent_metrics" in program["metadata"]:
            for k, v in program["metadata"]["parent_metrics"].items():
                if not check_json_float(v):
                    program["metadata"]["parent_metrics"][k] = None


def check_json_float(v: Optional[float]) -> bool:
    return isinstance(v, Number) and not (math.isinf(v) or math.isnan(v))


@app.route("/")
def index():
    return render_template("index.html", checkpoint_dir=checkpoint_dir)


checkpoint_dir = None  # Global variable to store the checkpoint directory


def _is_local_request() -> bool:
    remote_addr = request.remote_addr
    try:
        if remote_addr is None or not ipaddress.ip_address(remote_addr).is_loopback:
            return False
    except ValueError:
        return False

    host = urlsplit(request.host_url).hostname
    try:
        if host != "localhost" and (host is None or not ipaddress.ip_address(host).is_loopback):
            return False
    except ValueError:
        return False

    origin = request.headers.get("Origin")
    return not origin or origin.rstrip("/") == request.host_url.rstrip("/")


def _current_run_status() -> Optional[dict[str, Any]]:
    if current_run is None:
        return None

    process = current_run["process"]
    return_code = process.poll()
    if return_code is not None and current_run["status"] == "running":
        current_run["status"] = "completed" if return_code == 0 else "failed"
        current_run["return_code"] = return_code

    log_path = current_run["log_path"]
    try:
        with open(log_path, "rb") as log_file:
            log_file.seek(max(0, os.path.getsize(log_path) - 16000))
            log = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        log = ""

    return {
        "id": current_run["id"],
        "status": current_run["status"],
        "output_dir": current_run["output_dir"],
        "return_code": current_run.get("return_code"),
        "log": log,
    }


@app.route("/launch")
def launch_page():
    if not _is_local_request():
        abort(403)

    example_dir = PROJECT_ROOT / "examples" / "function_minimization"
    return render_template(
        "launch.html",
        initial_program=(example_dir / "initial_program.py").read_text(encoding="utf-8"),
        evaluator=(example_dir / "evaluator.py").read_text(encoding="utf-8"),
        config=(example_dir / "config.yaml").read_text(encoding="utf-8"),
    )


@app.route("/api/import-experiment", methods=["POST"])
def import_experiment_api():
    if not _is_local_request():
        abort(403)

    experiment_file = request.files.get("experiment")
    program_file = request.files.get("program")
    if experiment_file is None or program_file is None:
        return jsonify({"error": "请选择实验 YAML 文件和初始程序文件。"}), 400
    if not experiment_file.filename or not program_file.filename:
        return jsonify({"error": "实验 YAML 文件和初始程序文件都不能为空。"}), 400
    if Path(experiment_file.filename).suffix.lower() not in {".yaml", ".yml"}:
        return jsonify({"error": "实验配置文件必须是 .yaml 或 .yml 格式。"}), 400
    if Path(program_file.filename).suffix.lower() != ".py":
        return jsonify({"error": "初始程序文件必须是 .py 格式。"}), 400

    try:
        import yaml

        experiment_data = yaml.safe_load(experiment_file.stream)
    except yaml.YAMLError as error:
        return jsonify({"error": f"实验文件不是有效的 YAML：{error}"}), 400
    if not isinstance(experiment_data, dict):
        return jsonify({"error": "实验 YAML 顶层必须是对象。"}), 400

    experiment = experiment_data.get("experiment")
    if not isinstance(experiment, dict):
        return jsonify({"error": "未找到 experiment 对象。"}), 400

    task_description = experiment.get("task_description")
    if not isinstance(task_description, str) or not task_description.strip():
        return jsonify({"error": "experiment.task_description 必须是非空文本。"}), 400

    iterations = experiment.get("max_iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        return jsonify({"error": "experiment.max_iterations 必须是正整数。"}), 400

    try:
        initial_program = program_file.stream.read().decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "初始程序必须是 UTF-8 编码的文本文件。"}), 400
    if not initial_program.strip():
        return jsonify({"error": "初始程序文件不能为空。"}), 400

    config = {
        "max_iterations": iterations,
        "prompt": {"system_message": task_description},
    }
    return jsonify(
        {
            "initial_program": initial_program,
            "iterations": iterations,
            "config": yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            "experiment_name": str(experiment.get("name") or ""),
            "evaluator_reference": str(experiment.get("evaluator") or ""),
        }
    )


@app.route("/api/run", methods=["GET", "POST"])
def run_api():
    global current_run

    if not _is_local_request():
        abort(403)

    if request.method == "GET":
        return jsonify({"run": _current_run_status()})

    if not request.is_json or request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"error": "请从本机进化配置页面提交任务。"}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求内容必须是 JSON 对象。"}), 400

    initial_program = payload.get("initial_program")
    evaluator = payload.get("evaluator")
    config_text = payload.get("config", "")
    iterations = payload.get("iterations")
    evaluator_args_text = payload.get("evaluator_args", "")

    if not isinstance(initial_program, str) or not initial_program.strip():
        return jsonify({"error": "请填写初始程序代码。"}), 400
    if not isinstance(evaluator, str) or not evaluator.strip():
        return jsonify({"error": "请填写评测器代码。"}), 400
    if not isinstance(config_text, str):
        return jsonify({"error": "配置内容必须是 YAML 文本。"}), 400
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        return jsonify({"error": "进化轮数必须是正整数。"}), 400
    if not isinstance(evaluator_args_text, str):
        return jsonify({"error": "评测器参数必须是 JSON 文本。"}), 400

    try:
        evaluator_args = json.loads(evaluator_args_text) if evaluator_args_text.strip() else {}
    except json.JSONDecodeError as error:
        return jsonify({"error": f"评测器参数不是有效的 JSON：{error.msg}。"}), 400
    if not isinstance(evaluator_args, dict):
        return jsonify({"error": "评测器参数必须是 JSON 对象。"}), 400

    try:
        import yaml

        parsed_config = yaml.safe_load(config_text) if config_text.strip() else {}
    except yaml.YAMLError as error:
        return jsonify({"error": f"配置不是有效的 YAML：{error}"}), 400
    if parsed_config is not None and not isinstance(parsed_config, dict):
        return jsonify({"error": "YAML 配置的顶层内容必须是键值映射。"}), 400

    if not run_lock.acquire(blocking=False):
        return jsonify({"error": "正在启动另一个任务，请稍后再试。"}), 409
    try:
        if current_run is not None and current_run["process"].poll() is None:
            return jsonify({"error": "已有进化任务正在运行。"}), 409

        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        run_dir = GUI_RUNS_DIR / run_id
        input_dir = run_dir / "inputs"
        initial_path = input_dir / "initial_program.py"
        evaluator_path = input_dir / "evaluator.py"
        config_path = input_dir / "config.yaml"
        try:
            input_dir.mkdir(parents=True, exist_ok=False)
            initial_path.write_text(initial_program, encoding="utf-8")
            evaluator_path.write_text(evaluator, encoding="utf-8")
            if config_text.strip():
                config_path.write_text(config_text, encoding="utf-8")
        except OSError as error:
            logger.exception("Could not write GUI evolution input files")
            return jsonify({"error": f"无法保存任务输入文件：{error}"}), 500

        command = [
            sys.executable,
            str(PROJECT_ROOT / "openevolve-run.py"),
            str(initial_path),
            str(evaluator_path),
            "--iterations",
            str(iterations),
            "--output",
            str(run_dir),
        ]
        if config_text.strip():
            command.extend(["--config", str(config_path)])
        if evaluator_args:
            command.extend(["--evaluator-args", json.dumps(evaluator_args)])

        log_path = run_dir / "run.log"
        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as error:
            logger.exception("Could not start GUI evolution run")
            return jsonify({"error": f"无法启动进化任务：{error}"}), 500

        current_run = {
            "id": run_id,
            "status": "running",
            "process": process,
            "output_dir": str(run_dir),
            "log_path": str(log_path),
        }
        return jsonify({"run": _current_run_status()}), 202
    finally:
        run_lock.release()


@app.route("/api/data")
def data():
    global checkpoint_dir
    base_folder = (
        current_run["output_dir"]
        if current_run is not None
        else os.environ.get("EVOLVE_OUTPUT", "examples/")
    )
    checkpoint_dir = find_latest_checkpoint(base_folder)
    if not checkpoint_dir:
        logger.info(f"No checkpoints found in {base_folder}")
        return jsonify({"archive": [], "nodes": [], "edges": [], "checkpoint_dir": ""})

    logger.info(f"Loading data from checkpoint: {checkpoint_dir}")
    data = load_evolution_data(checkpoint_dir)
    logger.debug(f"Data: {data}")
    return jsonify(data)


@app.route("/program/<program_id>")
def program_page(program_id):
    global checkpoint_dir
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
            current_run["output_dir"]
            if current_run is not None
            else os.environ.get("EVOLVE_OUTPUT", "examples/")
        )
    )
)


if __name__ == "__main__":
    import argparse

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

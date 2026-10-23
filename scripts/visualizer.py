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
GUI_RUNS_DIR = PROJECT_ROOT / "evolve_tasks"
TASK_FILES_DIR = PROJECT_ROOT / "famou" / "调参"
GLOBAL_CONFIG_PATH = GUI_RUNS_DIR / "global_config.yml"
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


def _latest_checkpoint_for(checkpoint_root):
    """Return the newest checkpoint folder under a run output dir, or None."""
    if not os.path.isdir(checkpoint_root):
        return None
    return find_latest_checkpoint(checkpoint_root)


def _read_run_generation(run_dir):
    """Best-effort count of generated programs in a run output dir."""
    checkpoint = run_dir / "output" / "checkpoints"
    if not checkpoint.is_dir():
        return 0
    newest = _latest_checkpoint_for(str(checkpoint))
    if not newest:
        return 0
    programs_dir = os.path.join(newest, "programs")
    try:
        return len([name for name in os.listdir(programs_dir) if name.endswith(".json")])
    except OSError:
        return 0


def _run_summary(run_dir):
    """Describe one evolve_tasks/evolve_task_* run folder for the task list."""
    output_dir = run_dir / "output"
    input_dir = run_dir / "input"
    checkpoint = _latest_checkpoint_for(str(output_dir)) if output_dir.is_dir() else None

    status = "unknown"
    return_code = None
    log_text = ""
    log_path = output_dir / "experiment.log"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
            log_text = log_file.read()
    except OSError:
        pass
    if "Evolution complete!" in log_text or "✅ Evolution completed" in log_text:
        status = "completed"
        return_code = 0
    elif log_text:
        status = "failed"
    elif checkpoint:
        status = "interrupted"

    iterations = 0
    config_path = input_dir / "config.yaml"
    if config_path.is_file():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(config, dict) and isinstance(config.get("max_iterations"), int):
                iterations = config["max_iterations"]
        except (OSError, yaml.YAMLError):
            pass

    best_metrics = {}
    best_info = output_dir / "best" / "best_program_info.json"
    if best_info.is_file():
        try:
            with open(best_info, "r", encoding="utf-8") as info_file:
                info = json.load(info_file)
            if isinstance(info, dict) and isinstance(info.get("metrics"), dict):
                best_metrics = info["metrics"]
        except (OSError, json.JSONDecodeError):
            pass

    model = ""
    if config_path.is_file():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            llm = config.get("llm") if isinstance(config, dict) else None
            if isinstance(llm, dict):
                if isinstance(llm.get("primary_model"), str):
                    model = llm["primary_model"]
                elif isinstance(llm.get("models"), list) and llm["models"]:
                    first = llm["models"][0]
                    if isinstance(first, dict) and isinstance(first.get("name"), str):
                        model = first["name"]
        except (OSError, yaml.YAMLError):
            pass

    try:
        updated_at = datetime.fromtimestamp(os.path.getmtime(run_dir)).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        updated_at = ""

    try:
        run_dir_display = run_dir.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        # Tests and ad-hoc runs point GUI_RUNS_DIR outside the repo.
        run_dir_display = run_dir.as_posix()

    return {
        "id": run_dir.name,
        "run_dir": run_dir_display,
        "output_dir": Path(os.path.relpath(output_dir, PROJECT_ROOT)).as_posix(),
        "status": status,
        "return_code": return_code,
        "iterations": iterations,
        "programs": _read_run_generation(run_dir),
        "best_metrics": best_metrics,
        "model": model,
        "has_checkpoint": checkpoint is not None,
        "checkpoint_dir": (
            Path(os.path.relpath(checkpoint, PROJECT_ROOT)).as_posix() if checkpoint else ""
        ),
        "updated_at": updated_at,
    }


def _list_runs():
    """List every GUI-launched evolution run, newest first."""
    if not GUI_RUNS_DIR.is_dir():
        return []
    runs = [
        _run_summary(path)
        for path in GUI_RUNS_DIR.iterdir()
        if path.is_dir() and path.name.startswith("evolve_task_")
    ]
    runs.sort(key=lambda run: run["updated_at"], reverse=True)
    return runs


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

    project_root = str(PROJECT_ROOT)
    log = log.replace(f"{project_root}{os.sep}", "")

    return {
        "id": current_run["id"],
        "status": current_run["status"],
        "run_dir": Path(os.path.relpath(current_run["run_dir"], PROJECT_ROOT)).as_posix(),
        "output_dir": Path(os.path.relpath(current_run["output_dir"], PROJECT_ROOT)).as_posix(),
        "return_code": current_run.get("return_code"),
        "log": log,
    }


@app.route("/launch")
def launch_page():
    if not _is_local_request():
        abort(403)

    import yaml

    config = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    return render_template(
        "launch.html",
        initial_program=(TASK_FILES_DIR / "init.py").read_text(encoding="utf-8"),
        evaluator=(TASK_FILES_DIR / "runner_adapter.py").read_text(encoding="utf-8"),
        prompt=(TASK_FILES_DIR / "prompt.md").read_text(encoding="utf-8"),
        iterations=config["max_iterations"],
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
    prompt_text = payload.get("prompt")
    evaluator = payload.get("evaluator")
    iterations = payload.get("iterations")

    if not isinstance(initial_program, str) or not initial_program.strip():
        return jsonify({"error": "请填写初始程序代码。"}), 400
    if not isinstance(prompt_text, str):
        return jsonify({"error": "任务提示词必须是文本。"}), 400
    if not isinstance(evaluator, str) or not evaluator.strip():
        return jsonify({"error": "请填写真实训练评估脚本代码。"}), 400
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        return jsonify({"error": "进化轮数必须是正整数。"}), 400
    if not prompt_text.strip():
        return jsonify({"error": "任务提示词不能为空。"}), 400
    import yaml

    try:
        config = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        logger.exception("Could not load global OpenEvolve configuration")
        return jsonify({"error": f"无法读取全局运行配置：{error}"}), 500
    if not isinstance(config, dict):
        return jsonify({"error": "全局运行配置必须是 YAML 键值映射。"}), 500
    evaluator_config = config.get("evaluator")
    if not isinstance(evaluator_config, dict):
        return jsonify({"error": "全局配置缺少 evaluator 配置段。"}), 500
    evaluator_args = evaluator_config.setdefault("evaluator_args", {})
    if not isinstance(evaluator_args, dict):
        return jsonify({"error": "全局配置 evaluator.evaluator_args 必须是映射。"}), 500
    config["max_iterations"] = iterations
    config["prompt"] = {"template_dir": ".", "system_message": "prompt"}

    if not run_lock.acquire(blocking=False):
        return jsonify({"error": "正在启动另一个任务，请稍后再试。"}), 409
    try:
        if current_run is not None and current_run["process"].poll() is None:
            return jsonify({"error": "已有进化任务正在运行。"}), 409

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        run_id = f"evolve_task_{timestamp}"
        run_dir = GUI_RUNS_DIR / run_id
        if run_dir.exists():
            run_id = f"{run_id}_{uuid.uuid4().hex[:8]}"
            run_dir = GUI_RUNS_DIR / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        initial_path = input_dir / "init.py"
        evaluator_path = input_dir / "evaluator_callback.py"
        adapter_path = input_dir / "runner_adapter.py"
        config_path = input_dir / "config.yaml"
        prompt_path = input_dir / "prompt.md"
        try:
            input_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir()
            initial_path.write_text(initial_program, encoding="utf-8")
            evaluator_path.write_text(
                (TASK_FILES_DIR / "evaluator.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            adapter_path.write_text(evaluator, encoding="utf-8")
            prompt_path.write_text(prompt_text, encoding="utf-8")
            evaluator_args["runner_command"] = [
                sys.executable,
                str(adapter_path),
                "{program_path}",
            ]
            config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
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
            str(output_dir),
        ]
        command.extend(["--config", str(config_path)])

        log_path = output_dir / "experiment.log"
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
            "run_dir": str(run_dir),
            "output_dir": str(output_dir),
            "log_path": str(log_path),
        }
        return jsonify({"run": _current_run_status()}), 202
    finally:
        run_lock.release()


@app.route("/runs")
def runs_page():
    if not _is_local_request():
        abort(403)
    return render_template("runs.html")


@app.route("/api/runs")
def runs_api():
    if not _is_local_request():
        abort(403)
    return jsonify({"runs": _list_runs()})


def _resolve_run_output(run_id: str) -> Optional[str]:
    """Map a run id from /api/runs to its output dir, rejecting path traversal."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return None
    run_dir = GUI_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return None
    return str(run_dir / "output")


@app.route("/api/data")
def data():
    global checkpoint_dir
    run_id = request.args.get("run_id", "").strip()
    if run_id:
        base_folder = _resolve_run_output(run_id)
        if base_folder is None:
            return jsonify({"error": f"找不到任务：{run_id}"}), 404
    else:
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

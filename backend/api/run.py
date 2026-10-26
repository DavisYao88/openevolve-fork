"""任务创建与当前任务状态（``/api/run``）。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime

import yaml
from flask import Blueprint, abort, jsonify, request

from .. import paths, state
from ..runs import _current_run_status, _reap_finished_runs, _resolve_resume_checkpoint
from ..secrets import _apply_gui_llm_settings
from ..templates import (
    _load_solver_evaluator,
    _load_template,
    _parse_mapping_field,
    _template_dirs,
)
from .common import _is_local_request

logger = logging.getLogger(__name__)

run_api_bp = Blueprint("run_api", __name__)


@run_api_bp.route("/api/run", methods=["GET", "POST"])
def run_api():
    if not _is_local_request():
        abort(403)

    if request.method == "GET":
        _reap_finished_runs()
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
    template_id = payload.get("template")
    task_name = payload.get("task_name")

    if not isinstance(initial_program, str) or not initial_program.strip():
        return jsonify({"error": "请填写初始程序代码。"}), 400
    if not isinstance(prompt_text, str):
        return jsonify({"error": "任务提示词必须是文本。"}), 400
    if not isinstance(evaluator, str) or not evaluator.strip():
        return jsonify({"error": "请填写真实评测脚本代码。"}), 400
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        return jsonify({"error": "进化轮数必须是正整数。"}), 400
    if not prompt_text.strip():
        return jsonify({"error": "任务提示词不能为空。"}), 400
    if template_id is not None and (not isinstance(template_id, str) or template_id not in _template_dirs()):
        return jsonify({"error": f"未知的任务模板：{template_id}"}), 400
    if task_name is not None and not isinstance(task_name, str):
        return jsonify({"error": "任务名称必须是文本。"}), 400
    resume_checkpoint = None
    resume_from = payload.get("resume_from")
    if isinstance(resume_from, str):
        resume_from = resume_from.strip()
    # 空字符串/None 一律表示「不续跑」：页面新建任务时总会带上空串，不能当非法值
    if resume_from:
        if not isinstance(resume_from, str):
            return jsonify({"error": "续跑来源必须是 <任务ID> 或 <任务ID>/<检查点名>。"}), 400
        resume_checkpoint = _resolve_resume_checkpoint(resume_from)
        if resume_checkpoint is None:
            return jsonify({"error": f"找不到可用于续跑的检查点：{resume_from}"}), 400

    # 目标函数与候选校验：优先用页面提交的值，否则用任务模板里的配置。
    template, template_error = _load_template(template_id)
    if template_error:
        return jsonify({"error": template_error}), 500
    objective, error = _parse_mapping_field(payload.get("objective"), "评分配置 objective")
    if error:
        return jsonify({"error": error}), 400
    validation, error = _parse_mapping_field(payload.get("validation"), "校验配置 validation")
    if error:
        return jsonify({"error": error}), 400
    if objective is None:
        objective = template["objective"]
    if validation is None:
        validation = template["validation"]
    try:
        solver_evaluator = _load_solver_evaluator()
        solver_evaluator.validate_objective(objective)
        solver_evaluator.validate_validation(validation)
    except (ImportError, OSError, ValueError) as error:
        return jsonify({"error": f"目标/校验配置无效：{error}"}), 400

    try:
        config = yaml.safe_load(paths.GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
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
    # 轮数少于检查点间隔时永远存不出 checkpoint，可视化拿不到任何数据；
    # 所以把间隔收缩到不超过总轮数，保证每个任务至少有一份检查点。
    interval = config.get("checkpoint_interval")
    if not isinstance(interval, int) or interval <= 0:
        interval = 5
    config["checkpoint_interval"] = max(1, min(interval, iterations))
    config["prompt"] = {"template_dir": ".", "system_message": "prompt"}
    # 设置页里配置的模型供应商覆盖全局配置，本次任务立即生效
    _apply_gui_llm_settings(config)
    # 真实 key 只在内存里：写盘时换成 ${OPENEVOLVE_API_KEY} 占位符，由子进程环境变量注入
    llm_section = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    run_api_key = str(llm_section.get("api_key") or "")

    if not os.path.isfile(paths.RUNNER_PYTHON):
        return (
            jsonify(
                {
                    "error": f"评测解释器不存在：{paths.RUNNER_PYTHON}。"
                    "请检查 OPENEVOLVE_RUNNER_PYTHON 或 .env.local 里的配置。"
                }
            ),
            400,
        )

    if not state.run_lock.acquire(blocking=False):
        return jsonify({"error": "正在启动另一个任务，请稍后再试。"}), 409
    try:
        _reap_finished_runs()

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        run_id = f"evolve_task_{timestamp}"
        run_dir = paths.GUI_RUNS_DIR / run_id
        if run_dir.exists():
            run_id = f"{run_id}_{uuid.uuid4().hex[:8]}"
            run_dir = paths.GUI_RUNS_DIR / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        initial_path = input_dir / "init.py"
        evaluator_path = input_dir / "evaluator_callback.py"
        adapter_path = input_dir / "runner_adapter.py"
        config_path = input_dir / "config.yaml"
        prompt_path = input_dir / "prompt.md"
        task_path = input_dir / "task.json"
        task_manifest = {
            "name": (task_name or "").strip() or template["name"],
            "template": template["id"],
            "template_name": template["name"],
            "objective": objective,
            "validation": validation,
            "resumed_from": resume_checkpoint,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            input_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir()
            initial_path.write_text(initial_program, encoding="utf-8")
            evaluator_path.write_text(
                paths.SOLVER_EVALUATOR_PATH.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            adapter_path.write_text(evaluator, encoding="utf-8")
            prompt_path.write_text(prompt_text, encoding="utf-8")
            evaluator_args["runner_command"] = [
                paths.RUNNER_PYTHON,
                str(adapter_path),
                "{program_path}",
            ]
            if objective is not None:
                evaluator_args["objective"] = objective
            if validation is not None:
                evaluator_args["validation"] = validation
            evaluator_args["reference_program_path"] = str(initial_path)
            # 任务目录里的 config.yaml 不含明文 key，只留占位符
            llm_dump = config.get("llm")
            if isinstance(llm_dump, dict) and run_api_key:
                llm_dump["api_key"] = "${OPENEVOLVE_API_KEY}"
            config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
            config_path.write_text(config_text, encoding="utf-8")
            task_path.write_text(
                json.dumps(task_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            logger.exception("Could not write GUI evolution input files")
            return jsonify({"error": f"无法保存任务输入文件：{error}"}), 500

        command = [
            sys.executable,
            str(paths.PROJECT_ROOT / "openevolve-run.py"),
            str(initial_path),
            str(evaluator_path),
            "--iterations",
            str(iterations),
            "--output",
            str(output_dir),
        ]
        command.extend(["--config", str(config_path)])
        if resume_checkpoint:
            command.extend(["--checkpoint", resume_checkpoint])

        log_path = output_dir / "experiment.log"
        child_env = dict(os.environ)
        if run_api_key:
            child_env["OPENEVOLVE_API_KEY"] = run_api_key
        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=paths.PROJECT_ROOT,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=child_env,
                    start_new_session=True,
                )
        except OSError as error:
            logger.exception("Could not start GUI evolution run")
            return jsonify({"error": f"无法启动进化任务：{error}"}), 500

        current_run = {
            "id": run_id,
            "status": "running",
            "process": process,
            "pid": process.pid,
            "run_dir": str(run_dir),
            "output_dir": str(output_dir),
            "log_path": str(log_path),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        state.current_run = current_run
        # 注册表落盘：服务重启后靠 pid + 任务目录找回这个子进程
        state.register_run(run_id, current_run)
        return jsonify({"run": _current_run_status()}), 202
    finally:
        state.run_lock.release()

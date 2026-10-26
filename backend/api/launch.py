"""新建任务的模板与默认内容接口（``/launch`` 页面 + ``/api/launch``）。"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request

from ..templates import _launch_defaults
from .common import _is_local_request

launch_api_bp = Blueprint("launch_api", __name__)


@launch_api_bp.route("/launch")
def launch_page():
    if not _is_local_request():
        abort(403)

    defaults, error = _launch_defaults()
    if error:
        return jsonify({"error": error}), 500
    return render_template(
        "launch.html",
        templates=defaults["templates"],
        default_template=defaults["default_template"],
        template_name=defaults["template"]["name"],
        initial_program=defaults["initial_program"],
        evaluator=defaults["evaluator"],
        prompt=defaults["prompt"],
        iterations=defaults["iterations"],
    )


@launch_api_bp.route("/api/launch")
def launch_defaults_api():
    if not _is_local_request():
        abort(403)

    defaults, error = _launch_defaults(request.args.get("template"))
    if error:
        return jsonify({"error": error}), 500
    return jsonify(defaults)

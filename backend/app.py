"""Flask 应用装配。

复用 ``scripts/visualizer.py`` 里保留的上游路由（前端不需要任何改动），
再挂上 backend 新增的 blueprint（任务创建 / 查询 / 设置 / 模板）。
"""

from __future__ import annotations

from flask import Flask

from .api.jobs import jobs_api
from .api.launch import launch_api_bp
from .api.run import run_api_bp
from .api.runs import runs_api_bp
from .api.settings import settings_api_bp
from .core import viz


def create_app() -> Flask:
    app = viz.app
    for blueprint in (run_api_bp, runs_api_bp, settings_api_bp, launch_api_bp, jobs_api):
        if blueprint.name not in app.blueprints:
            app.register_blueprint(blueprint)
    return app

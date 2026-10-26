"""Flask 应用装配。

直接复用 ``scripts/visualizer.py`` 里已经注册好的全部路由（前端不需要任何改动），
再挂上后端新增的异步任务接口。
"""

from __future__ import annotations

from flask import Flask

from .api.jobs import jobs_api
from .core import viz


def create_app() -> Flask:
    app = viz.app
    if "jobs" not in app.blueprints:
        app.register_blueprint(jobs_api)
    return app

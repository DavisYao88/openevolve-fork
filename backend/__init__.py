"""OpenEvolve 后端服务。

职责边界：

- ``solver/``：核心功能（评测器、任务模板），不含任何服务代码。
- ``scripts/visualizer.py``：原始单文件可视化器，保留原样，只提供 Flask 路由实现。
- ``backend/``：对外服务层——常驻进程、异步任务管理、任务查询接口。

启动：``./backend/serve.sh start``，默认 http://127.0.0.1:8080
"""

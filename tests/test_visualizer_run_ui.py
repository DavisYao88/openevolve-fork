"""Tests for the local run-configuration UI."""

import importlib.util
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import flask
except ImportError:  # pragma: no cover - visualizer extras not installed
    flask = None

VISUALIZER = Path(__file__).resolve().parent.parent / "scripts" / "visualizer.py"


@unittest.skipIf(flask is None, "flask is not installed")
class TestVisualizerRunUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, "path", [str(VISUALIZER.parent), *sys.path]):
            cls.visualizer = runpy.run_path(str(VISUALIZER))
        cls.global_config_source = cls.visualizer["GLOBAL_CONFIG_PATH"]
        cls.template_root = cls.visualizer["TEMPLATE_DIR"]
        cls.default_template = cls.visualizer["DEFAULT_TEMPLATE_ID"]

    def setUp(self):
        self.visualizer = self.__class__.visualizer
        self.module_globals = self.visualizer["run_api"].__globals__
        self.module_globals["current_run"] = None
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.gui_runs = Path(self.temporary.name) / "runs"
        self.global_config = Path(self.temporary.name) / "global-config.yml"
        source_config = self.__class__.global_config_source.read_text(encoding="utf-8")
        self.global_config.write_text(source_config, encoding="utf-8")
        self.module_globals["GUI_RUNS_DIR"] = self.gui_runs
        self.module_globals["GLOBAL_CONFIG_PATH"] = self.global_config
        self.module_globals["TEMPLATE_DIR"] = self.__class__.template_root
        self.original_global_config = source_config
        self.template_dir = self.__class__.template_root / self.__class__.default_template
        self.source_files = {
            name: (self.template_dir / name).read_text(encoding="utf-8")
            for name in ("init.py", "runner_adapter.py", "prompt.md")
        }
        self.client = self.visualizer["app"].test_client()

    @staticmethod
    def payload(**overrides):
        value = {
            "template": "famou-调参",
            "task_name": "测试任务",
            "initial_program": "def run_search():\n    return 1\n",
            "prompt": "Tune only the parameter values.",
            "evaluator": "import json, sys\nprint(json.dumps({'wmape': 0.1, 'bias_ratio': 0.0}))\n",
            "iterations": 2,
        }
        value.update(overrides)
        return value

    def test_launch_page_loads_solver_editors(self):
        response = self.client.get("/launch")

        self.assertEqual(response.status_code, 200)
        self.assertIn("任务模板".encode(), response.data)
        self.assertIn("任务名称".encode(), response.data)
        self.assertIn("初始程序代码".encode(), response.data)
        self.assertIn("任务提示词（Markdown）".encode(), response.data)
        self.assertIn("评测脚本代码".encode(), response.data)
        self.assertIn("评分配置".encode(), response.data)
        self.assertIn("进化轮数".encode(), response.data)
        self.assertNotIn("导入".encode(), response.data)
        self.assertNotIn("配置文件（YAML）".encode(), response.data)
        self.assertNotIn("config.yml".encode(), response.data)

    def test_launch_defaults_api_returns_editor_defaults(self):
        response = self.client.get("/api/launch")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["initial_program"], self.source_files["init.py"])
        self.assertEqual(payload["evaluator"], self.source_files["runner_adapter.py"])
        self.assertEqual(payload["prompt"], self.source_files["prompt.md"])
        self.assertGreater(payload["iterations"], 0)
        self.assertEqual(payload["default_template"], self.__class__.default_template)
        self.assertTrue(payload["templates"])
        self.assertIn("wmape", (payload["objective"] or {}).get("metrics", [{}])[0].get("name", ""))

    def test_launch_defaults_api_switches_template(self):
        response = self.client.get("/api/launch?template=generic-python")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["template"]["id"], "generic-python")
        self.assertIn("def solve()", payload["initial_program"])

    def test_run_rejects_unknown_template(self):
        response = self.client.post(
            "/api/run",
            json=self.payload(template="does-not-exist"),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)

    def test_run_rejects_invalid_objective(self):
        response = self.client.post(
            "/api/run",
            json=self.payload(objective={"metrics": [{"name": "score", "direction": "up"}]}),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("direction", response.get_json()["error"])

    def test_visualizer_has_grouped_toolbar_and_in_page_launch_dialog(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="toolbar-primary"', response.data)
        self.assertIn(b'class="toolbar-settings"', response.data)
        self.assertIn(b'id="launch-dialog"', response.data)
        self.assertIn(b'id="open-launch-dialog"', response.data)
        self.assertIn(b'role="tablist"', response.data)

    def test_all_pages_share_navigation(self):
        for path in ("/", "/manual", "/launch"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'href="/"', response.data)
                self.assertIn(b'href="/manual"', response.data)
                self.assertIn(b'href="/launch"', response.data)
                self.assertIn("进化可视化".encode(), response.data)
                self.assertIn("人工反馈".encode(), response.data)
                self.assertIn("新建进化".encode(), response.data)

    def test_file_import_endpoint_is_removed(self):
        response = self.client.post("/api/import-experiment")
        self.assertEqual(response.status_code, 404)

    def test_run_generates_runner_command_from_submitted_evaluator(self):
        process = MagicMock()
        process.poll.return_value = None
        import yaml

        original_global_config = self.global_config.read_text(encoding="utf-8")
        with patch("subprocess.Popen", return_value=process) as popen:
            response = self.client.post(
                "/api/run",
                json=self.payload(initial_program=self.source_files["init.py"]),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)
        run = response.get_json()["run"]
        self.assertEqual(run["status"], "running")
        run_dir = Path(self.module_globals["current_run"]["run_dir"])
        output_dir = Path(self.module_globals["current_run"]["output_dir"])
        self.assertEqual(
            run["run_dir"],
            Path(os.path.relpath(run_dir, self.module_globals["PROJECT_ROOT"])).as_posix(),
        )
        self.assertEqual(
            run["output_dir"],
            Path(os.path.relpath(output_dir, self.module_globals["PROJECT_ROOT"])).as_posix(),
        )
        self.assertEqual(
            (run_dir / "input" / "init.py").read_text(encoding="utf-8"),
            self.source_files["init.py"],
        )
        self.assertTrue((run_dir / "input" / "evaluator_callback.py").is_file())
        self.assertEqual(
            (run_dir / "input" / "runner_adapter.py").read_text(encoding="utf-8"),
            self.payload()["evaluator"],
        )
        self.assertTrue((run_dir / "input" / "config.yaml").is_file())
        self.assertEqual(
            (run_dir / "input" / "prompt.md").read_text(encoding="utf-8"),
            self.payload()["prompt"],
        )
        generated_config = yaml.safe_load(
            (run_dir / "input" / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(generated_config["max_iterations"], 2)
        self.assertEqual(
            generated_config["prompt"],
            {"template_dir": ".", "system_message": "prompt"},
        )
        evaluator_args = generated_config["evaluator"]["evaluator_args"]
        self.assertEqual(
            evaluator_args["runner_command"],
            [
                sys.executable,
                str(run_dir / "input" / "runner_adapter.py"),
                "{program_path}",
            ],
        )
        callback_path = run_dir / "input" / "evaluator_callback.py"
        spec = importlib.util.spec_from_file_location("run_evaluator_callback", callback_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        callback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(callback)
        evaluation = callback.evaluate(
            str(run_dir / "input" / "init.py"),
            **evaluator_args,
        )
        self.assertEqual(evaluation.metrics["wmape"], 0.1)
        self.assertEqual(evaluation.metrics["bias_ratio"], 0.0)
        self.assertEqual(self.global_config.read_text(encoding="utf-8"), original_global_config)
        for name, original_content in self.source_files.items():
            with self.subTest(source_file=name):
                self.assertEqual(
                    (self.template_dir / name).read_text(encoding="utf-8"),
                    original_content,
                )
        self.assertTrue((output_dir / "experiment.log").is_file())
        self.assertEqual(output_dir.parent, run_dir)
        self.assertTrue(run["id"].startswith("evolve_task_"))
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--output") + 1], str(output_dir))
        self.assertIn("--iterations", command)

        log_path = output_dir / "experiment.log"
        log_path.write_text(
            f"Logging to {self.module_globals['PROJECT_ROOT']}/evolve_tasks/sample/output/log.txt\n",
            encoding="utf-8",
        )
        status = self.client.get("/api/run").get_json()["run"]
        self.assertIn("Logging to evolve_tasks/sample/output/log.txt", status["log"])
        self.assertNotIn(str(self.module_globals["PROJECT_ROOT"]), status["log"])

    def test_run_records_template_objective_and_task_name(self):
        process = MagicMock()
        process.poll.return_value = None
        import yaml

        objective = {"metrics": [{"name": "quality", "direction": "maximize"}]}
        with patch("subprocess.Popen", return_value=process):
            response = self.client.post(
                "/api/run",
                json=self.payload(objective=objective, task_name="我的任务"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)
        run_dir = Path(self.module_globals["current_run"]["run_dir"])
        generated_config = yaml.safe_load(
            (run_dir / "input" / "config.yaml").read_text(encoding="utf-8")
        )
        evaluator_args = generated_config["evaluator"]["evaluator_args"]
        self.assertEqual(evaluator_args["objective"], objective)
        self.assertEqual(evaluator_args["validation"]["mode"], "params_dict")
        self.assertEqual(evaluator_args["reference_program_path"], str(run_dir / "input" / "init.py"))

        manifest = json.loads((run_dir / "input" / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "我的任务")
        self.assertEqual(manifest["template"], "famou-调参")

    def test_ollama_config_does_not_require_codex_cli(self):
        import yaml

        config = yaml.safe_load(self.global_config.read_text(encoding="utf-8"))
        config["llm"] = {
            "api_base": "http://localhost:11434/v1",
            "primary_model": "qwen3.6:35b",
        }
        self.global_config.write_text(yaml.safe_dump(config), encoding="utf-8")
        process = MagicMock()
        process.poll.return_value = None
        with patch("subprocess.Popen", return_value=process):
            response = self.client.post(
                "/api/run",
                json=self.payload(),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)

    def test_rejects_non_local_and_cross_origin_requests(self):
        response = self.client.get(
            "/launch",
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            "/api/run",
            json=self.payload(),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://example.invalid",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_run_input_file_preview(self):
        run_dir = self.gui_runs / "evolve_task_preview"
        (run_dir / "input").mkdir(parents=True)
        (run_dir / "input" / "prompt.md").write_text("# hello preview", encoding="utf-8")

        response = self.client.get("/api/runs/evolve_task_preview/input/prompt.md")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["content"], "# hello preview")
        self.assertFalse(payload["truncated"])

        # 白名单之外的文件一律拒绝。
        self.assertEqual(
            self.client.get("/api/runs/evolve_task_preview/input/secret.py").status_code,
            400,
        )
        # 白名单内但未保存的文件返回 404。
        self.assertEqual(
            self.client.get("/api/runs/evolve_task_preview/input/task.json").status_code,
            404,
        )
        # 未知任务返回 404。
        self.assertEqual(
            self.client.get("/api/runs/evolve_task_missing/input/prompt.md").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for the local run-configuration UI."""

import json
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

    def setUp(self):
        self.visualizer = self.__class__.visualizer
        self.module_globals = self.visualizer["run_api"].__globals__
        self.module_globals["current_run"] = None
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.gui_runs = Path(self.temporary.name) / "runs"
        self.module_globals["GUI_RUNS_DIR"] = self.gui_runs
        self.client = self.visualizer["app"].test_client()

    @staticmethod
    def payload(**overrides):
        value = {
            "initial_program": "def run_search():\n    return 1\n",
            "evaluator": "def evaluate(program_path):\n    return {'score': 1}\n",
            "config": "max_iterations: 2\n",
            "iterations": 2,
            "evaluator_args": '{"data_path":"data/input.csv"}',
        }
        value.update(overrides)
        return value

    def test_launch_page_loads_example_editors(self):
        response = self.client.get("/launch")

        self.assertEqual(response.status_code, 200)
        self.assertIn("初始程序代码".encode(), response.data)
        self.assertIn("评测器参数".encode(), response.data)

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

    def test_run_starts_with_submitted_code_and_arguments(self):
        process = MagicMock()
        process.poll.return_value = None
        with patch("subprocess.Popen", return_value=process) as popen:
            response = self.client.post(
                "/api/run",
                json=self.payload(),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)
        run = response.get_json()["run"]
        self.assertEqual(run["status"], "running")
        run_dir = Path(run["output_dir"])
        self.assertEqual(
            (run_dir / "inputs" / "initial_program.py").read_text(encoding="utf-8"),
            self.payload()["initial_program"],
        )
        command = popen.call_args.args[0]
        self.assertIn("--iterations", command)
        self.assertIn("--evaluator-args", command)
        arguments = command[command.index("--evaluator-args") + 1]
        self.assertEqual(json.loads(arguments), {"data_path": "data/input.csv"})

    def test_invalid_evaluator_arguments_do_not_start_process(self):
        with patch("subprocess.Popen") as popen:
            response = self.client.post(
                "/api/run",
                json=self.payload(evaluator_args="[1, 2]"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON 对象", response.get_json()["error"])
        popen.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()

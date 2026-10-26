"""Tests for run controls that the Vue frontend exposes: checkpoints, resume, cancel, export."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import flask
except ImportError:  # pragma: no cover - visualizer extras not installed
    flask = None

ROOT = Path(__file__).resolve().parent.parent
GLOBAL_CONFIG = ROOT / "evolve_tasks" / "global_config.yml"
TEMPLATE_DIR = ROOT / "solver" / "templates"


def _program(program_id, score=0.0):
    return {
        "id": program_id,
        "code": "PARAMS = {}",
        "generation": 0,
        "iteration_found": 0,
        "metrics": {"combined_score": score},
        "metadata": {},
        "parent_id": None,
        "language": "python",
        "complexity": 1.0,
        "diversity": 0.0,
        "timestamp": 1.0,
    }


@unittest.skipIf(flask is None, "flask is not installed")
class TestVisualizerRunControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend import paths, state
        from backend.app import create_app

        cls.paths = paths
        cls.state = state
        cls.app = create_app()

    def setUp(self):
        self.addCleanup(self._restore_shared_state)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "evolve_tasks"
        self.runs_root.mkdir()
        self.temp_root = Path(self.temporary.name)
        self.global_config = self.temp_root / "global-config.yml"
        self.global_config.write_text(GLOBAL_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        self.paths.GUI_RUNS_DIR = self.runs_root
        self.paths.GLOBAL_CONFIG_PATH = self.global_config
        self.paths.TEMPLATE_DIR = TEMPLATE_DIR
        self.state.current_run = None
        self.state.active_runs = {}
        import sys as _sys

        visualizer = _sys.modules.get("visualizer")
        if visualizer is not None:
            visualizer.checkpoint_dir = None
        self.client = self.app.test_client()

    def _restore_shared_state(self):
        self.paths.GUI_RUNS_DIR = ROOT / "evolve_tasks"
        self.paths.GLOBAL_CONFIG_PATH = ROOT / "evolve_tasks" / "global_config.yml"
        self.paths.TEMPLATE_DIR = ROOT / "solver" / "templates"
        self.state.current_run = None
        self.state.active_runs = {}

    def _make_run(self, run_id, programs=None, checkpoints=(10,), feature_maps=None):
        run_dir = self.runs_root / run_id
        (run_dir / "input").mkdir(parents=True)
        (run_dir / "output").mkdir()
        (run_dir / "input" / "config.yaml").write_text(
            json.dumps({"max_iterations": 10, "llm": {"primary_model": "test-model"}}),
            encoding="utf-8",
        )
        (run_dir / "output" / "experiment.log").write_text(
            "Evolution complete!\n", encoding="utf-8"
        )

        programs = programs or ["seed"]
        for iteration in checkpoints:
            checkpoint = run_dir / "output" / "checkpoints" / f"checkpoint_{iteration}"
            (checkpoint / "programs").mkdir(parents=True)
            metadata = {
                "islands": [programs],
                "archive": programs,
                "best_program_id": programs[0],
                "island_best_programs": [programs[0]],
                "last_iteration": iteration,
                "island_generations": [1],
                "island_feature_maps": feature_maps if feature_maps is not None else [{}],
                "feature_stats": {"complexity": {"min": 0.0, "max": 10.0}},
            }
            (checkpoint / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for index, program_id in enumerate(programs):
                (checkpoint / "programs" / f"{program_id}.json").write_text(
                    json.dumps(_program(program_id, float(index))), encoding="utf-8"
                )
        return run_dir

    # ------------------------------------------------------------------ checkpoints

    def test_checkpoint_api_lists_checkpoints_oldest_first(self):
        self._make_run("evolve_task_20261023120000", checkpoints=(10, 20))

        response = self.client.get("/api/runs/evolve_task_20261023120000/checkpoints")

        self.assertEqual(response.status_code, 200)
        checkpoints = response.get_json()["checkpoints"]
        self.assertEqual([item["name"] for item in checkpoints], ["checkpoint_10", "checkpoint_20"])
        self.assertEqual(checkpoints[0]["iteration"], 10)
        self.assertGreater(checkpoints[0]["programs"], 0)

    def test_checkpoint_api_rejects_unknown_run(self):
        self.assertEqual(self.client.get("/api/runs/missing/checkpoints").status_code, 404)

    def test_api_data_can_select_a_specific_checkpoint(self):
        self._make_run("evolve_task_20261023120000", programs=["seed"], checkpoints=(10, 20))
        run_dir = self.runs_root / "evolve_task_20261023120000"
        (
            run_dir / "output" / "checkpoints" / "checkpoint_20" / "programs" / "seed.json"
        ).write_text(json.dumps(_program("seed", 99.0)), encoding="utf-8")

        response = self.client.get(
            "/api/data?run_id=evolve_task_20261023120000&checkpoint=checkpoint_20"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["selected_checkpoint"], "checkpoint_20")
        self.assertEqual(payload["nodes"][0]["metrics"]["combined_score"], 99.0)
        self.assertEqual(
            [item["name"] for item in payload["checkpoints"]], ["checkpoint_10", "checkpoint_20"]
        )

    def test_api_data_rejects_traversal_checkpoint_names(self):
        self._make_run("evolve_task_20261023120000")

        for name in ("../..", "checkpoint_99", "config.yaml", "sub/checkpoint_10"):
            response = self.client.get(
                f"/api/data?run_id=evolve_task_20261023120000&checkpoint={name}"
            )
            self.assertEqual(response.status_code, 404, name)

    def test_api_data_returns_feature_maps(self):
        self._make_run(
            "evolve_task_20261023120000",
            programs=["seed", "child"],
            feature_maps=[{"0-1": "seed", "2-3": "child"}],
        )

        payload = self.client.get("/api/data?run_id=evolve_task_20261023120000").get_json()

        grid = payload["feature_maps"][0]
        self.assertEqual(grid["bins_x"], 3)
        self.assertEqual(grid["bins_y"], 4)
        self.assertEqual(sorted((cell["x"], cell["y"]) for cell in grid["cells"]), [(0, 1), (2, 3)])
        self.assertEqual(grid["dimensions"], ["complexity", "diversity"])
        by_id = {cell["program_id"]: cell for cell in grid["cells"]}
        self.assertEqual(by_id["child"]["fitness"], 1.0)

    # ------------------------------------------------------------------ cancel

    def test_cancel_terminates_the_running_task(self):
        run_dir = self._make_run("evolve_task_20261023120000")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
        )
        self.addCleanup(process.kill)
        self.state.current_run = {
            "id": "evolve_task_20261023120000",
            "status": "running",
            "process": process,
            "run_dir": str(run_dir),
            "output_dir": str(run_dir / "output"),
            "log_path": str(run_dir / "output" / "experiment.log"),
        }

        response = self.client.post("/api/runs/evolve_task_20261023120000/cancel")

        self.assertEqual(response.status_code, 200)
        for _ in range(50):
            if process.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(process.poll())

    def test_cancel_rejects_tasks_not_started_here(self):
        self._make_run("evolve_task_20261023120000")

        response = self.client.post("/api/runs/evolve_task_20261023120000/cancel")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.post("/api/runs/missing/cancel").status_code, 404)

    # ------------------------------------------------------------------ resume

    def _run_payload(self, **overrides):
        payload = {
            "template": "generic-python",
            "task_name": "续跑测试",
            "initial_program": "def solve():\n    return {'score': 1.0}\n",
            "prompt": "Improve the score.",
            "evaluator": "import json, sys\nprint(json.dumps({'score': 1.0}))\n",
            "iterations": 3,
        }
        payload.update(overrides)
        return payload

    def test_run_accepts_resume_from_a_checkpoint(self):
        self._make_run("evolve_task_20261023120000", checkpoints=(10,))
        process = MagicMock()
        process.poll.return_value = None

        with patch("subprocess.Popen", return_value=process) as popen:
            response = self.client.post(
                "/api/run",
                json=self._run_payload(resume_from="evolve_task_20261023120000/checkpoint_10"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)
        command = popen.call_args[0][0]
        self.assertIn("--checkpoint", command)
        self.assertTrue(command[command.index("--checkpoint") + 1].endswith("checkpoint_10"))

        run_dir = Path(self.state.current_run["run_dir"])
        manifest = json.loads((run_dir / "input" / "task.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["resumed_from"].endswith("checkpoint_10"))

    def test_run_accepts_resume_from_latest_checkpoint(self):
        self._make_run("evolve_task_20261023120000", checkpoints=(10, 20))
        process = MagicMock()
        process.poll.return_value = None

        with patch("subprocess.Popen", return_value=process) as popen:
            response = self.client.post(
                "/api/run",
                json=self._run_payload(resume_from="evolve_task_20261023120000"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 202)
        command = popen.call_args[0][0]
        self.assertTrue(command[command.index("--checkpoint") + 1].endswith("checkpoint_20"))

    def test_multiple_tasks_can_run_concurrently(self):
        process = MagicMock()
        process.poll.return_value = None

        with patch("subprocess.Popen", return_value=process):
            first = self.client.post(
                "/api/run",
                json=self._run_payload(task_name="任务一"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            second = self.client.post(
                "/api/run",
                json=self._run_payload(task_name="任务二"),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(first.get_json()["run"]["id"], second.get_json()["run"]["id"])
        self.assertEqual(len(self.state.active_runs), 2)

    def test_finished_tasks_leave_the_active_registry(self):
        process = MagicMock()
        process.poll.return_value = 0
        with patch("subprocess.Popen", return_value=process):
            self.client.post(
                "/api/run",
                json=self._run_payload(),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.client.get("/api/runs")

        self.assertEqual(self.state.active_runs, {})

    def test_run_rejects_invalid_resume_sources(self):
        # 不存在的任务/检查点应拒绝；空串与空白表示「不续跑」（新建任务时总会带空串），
        # 会正常启动而非 400。
        for resume_from in ("missing-task", "missing-task/checkpoint_10"):
            response = self.client.post(
                "/api/run",
                json=self._run_payload(resume_from=resume_from),
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self.assertEqual(response.status_code, 400, resume_from)

    # ------------------------------------------------------------------ export

    def test_export_writes_a_static_page(self):
        run_dir = self._make_run("evolve_task_20261023120000")

        response = self.client.post("/api/runs/evolve_task_20261023120000/export")

        self.assertEqual(response.status_code, 200)
        static_dir = run_dir / "static"
        self.assertTrue((static_dir / "index.html").is_file())
        self.assertTrue((static_dir / "static").is_dir())
        self.assertIn("STATIC_DATA", (static_dir / "index.html").read_text(encoding="utf-8"))

    def test_export_rejects_unknown_run_and_checkpoint(self):
        self._make_run("evolve_task_20261023120000")

        self.assertEqual(self.client.post("/api/runs/missing/export").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/runs/evolve_task_20261023120000/export", json={"checkpoint": "checkpoint_99"}
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()

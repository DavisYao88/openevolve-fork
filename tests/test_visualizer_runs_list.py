"""Tests for the evolution run list page and run-scoped data loading."""

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import flask
except ImportError:  # pragma: no cover - visualizer extras not installed
    flask = None

VISUALIZER = Path(__file__).resolve().parent.parent / "scripts" / "visualizer.py"


@unittest.skipIf(flask is None, "flask is not installed")
class TestVisualizerRunsList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, "path", [str(VISUALIZER.parent), *sys.path]):
            cls.visualizer = runpy.run_path(str(VISUALIZER))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "evolve_tasks"
        self.runs_root.mkdir()
        self.module_globals = self.visualizer["runs_api"].__globals__
        self.module_globals["GUI_RUNS_DIR"] = self.runs_root
        self.module_globals["current_run"] = None
        self.module_globals["checkpoint_dir"] = None
        self.client = self.visualizer["app"].test_client()

    def _make_run(
        self,
        run_id,
        *,
        iterations=10,
        programs=None,
        best_metrics=None,
        log="Evolution complete!\n",
        model="qwen3.6:35b",
        write_best=True,
    ):
        run_dir = self.runs_root / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir()
        config = {"max_iterations": iterations, "llm": {"primary_model": model}}
        (input_dir / "config.yaml").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (output_dir / "experiment.log").write_text(log, encoding="utf-8")

        programs = programs if programs is not None else ["seed"]
        checkpoint = output_dir / "checkpoints" / "checkpoint_10"
        (checkpoint / "programs").mkdir(parents=True)
        (checkpoint / "metadata.json").write_text(
            json.dumps(
                {
                    "islands": [programs],
                    "archive": programs,
                    "best_program_id": programs[0],
                    "island_best_programs": [programs[0]],
                    "last_iteration": iterations,
                    "current_island": 0,
                    "island_generations": [1],
                    "last_migration_generation": 0,
                    "island_feature_maps": [[]],
                }
            ),
            encoding="utf-8",
        )
        for program_id in programs:
            (checkpoint / "programs" / f"{program_id}.json").write_text(
                json.dumps(
                    {
                        "id": program_id,
                        "code": "PARAMS = {}",
                        "generation": 0,
                        "iteration": 0,
                        "metrics": {"combined_score": 0.0},
                        "metadata": {},
                        "parent_id": None,
                        "language": "python",
                        "timestamp": 1.0,
                        "island": 0,
                    }
                ),
                encoding="utf-8",
            )
        if write_best:
            best_dir = output_dir / "best"
            best_dir.mkdir()
            (best_dir / "best_program_info.json").write_text(
                json.dumps(
                    {
                        "id": programs[0],
                        "metrics": best_metrics if best_metrics is not None else {"combined_score": 0.0},
                    }
                ),
                encoding="utf-8",
            )
        return run_dir

    def test_page_links_to_task_list(self):
        response = self.client.get("/runs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("演化任务列表".encode(), response.data)
        self.assertIn(b"/api/runs", response.data + b"/api/runs")

    def test_other_pages_link_to_task_list(self):
        for path in ("/", "/launch", "/manual"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/runs"'.encode(), response.data)

    def test_api_lists_runs_with_summary_fields(self):
        self._make_run(
            "evolve_task_20261023120000",
            iterations=50,
            programs=["a", "b"],
            best_metrics={"combined_score": 12.5, "wmape": 0.1},
        )
        self._make_run(
            "evolve_task_20261023130000",
            iterations=10,
            programs=["seed"],
            log="Error: boom\n",
        )

        response = self.client.get("/api/runs")

        self.assertEqual(response.status_code, 200)
        runs = {run["id"]: run for run in response.get_json()["runs"]}
        self.assertEqual(set(runs), {"evolve_task_20261023120000", "evolve_task_20261023130000"})

        completed = runs["evolve_task_20261023120000"]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["iterations"], 50)
        self.assertEqual(completed["programs"], 2)
        self.assertEqual(completed["model"], "qwen3.6:35b")
        self.assertEqual(completed["best_metrics"]["wmape"], 0.1)
        self.assertTrue(completed["has_checkpoint"])
        self.assertTrue(completed["checkpoint_dir"].endswith("checkpoint_10"))

        failed = runs["evolve_task_20261023130000"]
        self.assertEqual(failed["status"], "failed")

    def test_api_ignores_non_task_directories(self):
        (self.runs_root / "notes").mkdir()
        (self.runs_root / "global_config.yml").write_text("max_iterations: 1\n", encoding="utf-8")

        response = self.client.get("/api/runs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["runs"], [])

    def test_api_data_selects_requested_run(self):
        self._make_run("evolve_task_20261023120000", programs=["older"])
        self._make_run("evolve_task_20261023130000", programs=["newer"])

        response = self.client.get("/api/data?run_id=evolve_task_20261023120000")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([node["id"] for node in payload["nodes"]], ["older"])
        self.assertIn("evolve_task_20261023120000", payload["checkpoint_dir"].replace("\\", "/"))

    def test_api_data_rejects_unknown_and_traversal_run_ids(self):
        self._make_run("evolve_task_20261023120000")

        for run_id in ("evolve_task_missing", "../evolve_task_20261023120000", ".."):
            response = self.client.get(f"/api/data?run_id={run_id}")
            self.assertEqual(response.status_code, 404, run_id)

    def test_program_page_honors_run_id(self):
        self._make_run("evolve_task_20261023120000", programs=["older"])
        self._make_run("evolve_task_20261023130000", programs=["newer"])

        response = self.client.get("/program/newer?run_id=evolve_task_20261023130000")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"newer", response.data)
        self.assertIn(b"evolve_task_20261023130000", response.data)


if __name__ == "__main__":
    unittest.main()

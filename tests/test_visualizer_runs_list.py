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

    def _write_log(self, run_id, lines):
        log_path = self.runs_root / run_id / "output" / "experiment.log"
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_progress_parses_running_iterations(self):
        self._make_run("evolve_task_20261023120000", iterations=50)
        self._write_log(
            "evolve_task_20261023120000",
            [
                "2026-10-23 18:30:46,323 - INFO - Logging to logs/openevolve.log",
                "2026-10-23 18:30:46,410 - INFO - Starting process-based evolution "
                "from iteration 1 for 50 iterations (total: 51)",
                "2026-10-23 18:32:28,306 - WARNING - Iteration 1 error: None of the 1 "
                "SEARCH block(s) matched the parent program | tokens: 8187 "
                "(prompt: 3757, completion: 4430)",
                "2026-10-23 18:33:40,001 - INFO - Iteration 2: Program child-2 "
                "(parent: seed) completed in 71.50s | tokens: 9000 (prompt: 4000, "
                "completion: 5000)",
                "2026-10-23 18:33:40,002 - INFO - Metrics: combined_score=12.5000",
            ],
        )

        payload = self.client.get("/api/runs/evolve_task_20261023120000/progress").get_json()

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["total_iterations"], 50)
        self.assertEqual(payload["finished_iterations"], 2)
        self.assertEqual(payload["completed_iterations"], 1)
        self.assertEqual(payload["failed_iterations"], 1)
        self.assertEqual(payload["current_iteration"], 2)
        self.assertEqual(payload["percent"], 4.0)
        self.assertEqual(payload["tokens"], {"prompt": 7757, "completion": 9430})

        first, second = payload["iterations"]
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["stage"], "diff")
        self.assertEqual(first["tokens"]["total"], 8187)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["program_id"], "child-2")
        self.assertEqual(second["duration_seconds"], 71.5)
        self.assertEqual(second["metrics_text"], "combined_score=12.5000")

    def test_progress_reports_completed_and_interrupted(self):
        self._make_run("evolve_task_20261023120000", iterations=10)
        self._write_log(
            "evolve_task_20261023120000",
            [
                "2026-10-23 18:30:46,410 - INFO - Starting process-based evolution "
                "from iteration 1 for 10 iterations (total: 11)",
                "2026-10-23 18:31:00,000 - INFO - Saved checkpoint at iteration 10",
                "2026-10-23 18:31:01,000 - INFO - ✅ Evolution completed - "
                "Maximum iterations reached",
                "Evolution complete!",
            ],
        )
        completed = self.client.get("/api/runs/evolve_task_20261023120000/progress").get_json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["checkpoints"], [10])

        self._make_run("evolve_task_20261023130000", iterations=10)
        self._write_log(
            "evolve_task_20261023130000",
            [
                "2026-10-23 18:30:46,410 - INFO - Starting process-based evolution "
                "from iteration 1 for 10 iterations (total: 11)",
                "2026-10-23 18:31:01,000 - INFO - ✅ Evolution completed - "
                "Shutdown requested",
            ],
        )
        interrupted = self.client.get(
            "/api/runs/evolve_task_20261023130000/progress"
        ).get_json()
        self.assertEqual(interrupted["status"], "interrupted")

    def test_run_details_expose_iterations_and_input_files(self):
        self._make_run("evolve_task_20261023120000", iterations=10)
        self._write_log(
            "evolve_task_20261023120000",
            [
                "2026-10-23 18:30:46,410 - INFO - Starting process-based evolution "
                "from iteration 1 for 10 iterations (total: 11)",
                "2026-10-23 18:32:28,306 - WARNING - Iteration 1 error: LLM generation "
                "failed: boom",
            ],
        )

        response = self.client.get("/api/runs/evolve_task_20261023120000")

        self.assertEqual(response.status_code, 200)
        run = response.get_json()["run"]
        self.assertEqual(run["id"], "evolve_task_20261023120000")
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["iterations"][0]["stage"], "llm")
        self.assertTrue(run["has_checkpoint"])
        self.assertTrue(run["input_files"]["config.yaml"])
        self.assertFalse(run["input_files"]["prompt.md"])

    def test_run_log_endpoint_supports_offsets(self):
        self._make_run("evolve_task_20261023120000")
        self._write_log(
            "evolve_task_20261023120000",
            ["2026-10-23 18:30:46,323 - INFO - first", "2026-10-23 18:30:47,323 - INFO - second"],
        )

        full = self.client.get("/api/runs/evolve_task_20261023120000/log").get_json()
        self.assertTrue(full["eof"])
        self.assertIn("first", full["log"])
        self.assertIn("second", full["log"])

        tail = self.client.get(
            f"/api/runs/evolve_task_20261023120000/log?offset={full['size'] - 40}"
        ).get_json()
        self.assertEqual(tail["offset"], full["size"])
        self.assertIn("second", tail["log"])
        self.assertNotIn("first", tail["log"])

    def test_detail_endpoints_reject_unknown_runs(self):
        for path in ("/api/runs/missing", "/api/runs/missing/log", "/api/runs/missing/progress"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_progress_reports_islands_and_log_tail(self):
        run_dir = self._make_run(
            "evolve_task_20261023120000", iterations=10, programs=["seed", "child"]
        )
        self._write_log(
            "evolve_task_20261023120000",
            [
                "2026-10-23 18:30:46,410 - INFO - Island Status:",
                "2026-10-23 18:30:46,411 - INFO -  * Island 0: 2 programs, best=0.0000, "
                "avg=0.0000, diversity=0.00, gen=1 (best: seed)",
                "2026-10-23 18:30:46,411 - INFO -    Island 1: 1 programs, best=0.0000, "
                "avg=0.0000, diversity=12.50, gen=0 (best: child)",
                "2026-10-23 18:31:00,000 - INFO - Performing migration at iteration 5",
                "2026-10-23 18:31:01,000 - INFO - 🌟 New best solution found at iteration 5: child",
            ],
        )
        # The checkpoint metadata is the source of truth for per-island membership.
        checkpoint = run_dir / "output" / "checkpoints" / "checkpoint_10"
        (checkpoint / "metadata.json").write_text(
            json.dumps(
                {
                    "islands": [["seed"], ["child"]],
                    "archive": ["seed", "child"],
                    "island_best_programs": ["seed", "child"],
                    "island_generations": [3, 2],
                    "last_iteration": 10,
                }
            ),
            encoding="utf-8",
        )

        payload = self.client.get("/api/runs/evolve_task_20261023120000/progress").get_json()

        islands = {entry["index"]: entry for entry in payload["islands"]}
        self.assertEqual(set(islands), {0, 1})
        self.assertEqual(islands[0]["programs"], 1)
        self.assertEqual(islands[0]["generation"], 3)
        self.assertEqual(islands[0]["best_program_id"], "seed")
        self.assertEqual(islands[1]["diversity"], 12.5)
        self.assertEqual(payload["migrations"], [5])
        self.assertEqual(payload["best_events"][0]["program_id"], "child")
        self.assertTrue(any("Island Status" in line for line in payload["log_tail"]))


if __name__ == "__main__":
    unittest.main()

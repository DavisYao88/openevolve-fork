"""Tests for the shared evolutionary solver (solver/evaluator.py + templates)."""

import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOLVER_EVALUATOR = ROOT / "solver" / "evaluator.py"
TEMPLATE_DIR = ROOT / "solver" / "templates"
FAMOU_DIR = TEMPLATE_DIR / "famou-调参"
PARAM_DIR = TEMPLATE_DIR / "param-tuning"
GENERIC_DIR = TEMPLATE_DIR / "generic-python"


def load_template(directory: Path) -> dict:
    return yaml.safe_load((directory / "template.yml").read_text(encoding="utf-8"))


class SolverEvaluatorTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("solver_evaluator", SOLVER_EVALUATOR)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load evaluator from {SOLVER_EVALUATOR}")
        cls.evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.evaluator)

    @staticmethod
    def echo_command(payload: str) -> list:
        """A runner command that prints a fixed JSON object and ignores input."""
        return [sys.executable, "-c", f"import json;print(json.dumps({payload}))", "{program_path}"]

    @classmethod
    def write_candidate(cls, source: str, directory: Path) -> Path:
        candidate = directory / "candidate.py"
        candidate.write_text(source, encoding="utf-8")
        return candidate


class TestSolverObjective(SolverEvaluatorTestBase):
    """目标函数：任意指标 → 越大越好的 combined_score。"""

    def test_famou_template_reproduces_legacy_score(self):
        meta = load_template(FAMOU_DIR)
        result = self.evaluator.evaluate(
            str(FAMOU_DIR / "init.py"),
            runner_command=self.echo_command("{'wmape': 0.1, 'bias_ratio': 0.06}"),
            objective=meta["objective"],
            validation=meta["validation"],
            reference_program_path=str(FAMOU_DIR / "init.py"),
        )

        self.assertAlmostEqual(result.metrics["combined_score"], -math.log(0.1) * 100 - 1.0)
        self.assertAlmostEqual(result.metrics["wmape"], 0.1)
        self.assertAlmostEqual(result.metrics["bias_ratio"], 0.06)

    def test_runner_metrics_are_passed_through(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=self.echo_command("{'score': 2.5, 'latency_ms': 12.0}"),
            objective={"metrics": [{"name": "score", "direction": "maximize"}]},
        )

        self.assertEqual(result.metrics["combined_score"], 2.5)
        self.assertEqual(result.metrics["latency_ms"], 12.0)

    def test_combined_score_from_runner_when_objective_missing(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=self.echo_command("{'combined_score': 7.5}"),
        )

        self.assertEqual(result.metrics["combined_score"], 7.5)

    def test_missing_objective_and_score_is_explicit_zero(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=self.echo_command("{'quality': 1.0}"),
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("objective", result.artifacts["evaluation_error"])

    def test_weighted_multi_metric_objective(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=self.echo_command("{'quality': 4.0, 'cost': 1.0}"),
            objective={
                "metrics": [
                    {"name": "quality", "direction": "maximize", "weight": 2.0},
                    {"name": "cost", "direction": "minimize", "weight": 3.0},
                ]
            },
        )

        self.assertAlmostEqual(result.metrics["combined_score"], 8.0 - 3.0)

    def test_validate_objective_rejects_bad_direction(self):
        with self.assertRaisesRegex(ValueError, "direction"):
            self.evaluator.validate_objective({"metrics": [{"name": "x", "direction": "up"}]})

    def test_validate_objective_accepts_single_metric_shorthand(self):
        self.evaluator.validate_objective({"name": "wmape", "direction": "minimize", "scale": 100})


class TestSolverValidation(SolverEvaluatorTestBase):
    """候选校验：键集合、范围、枚举、约束与结构。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp_dir = Path(self.temporary.name)
        self.meta = load_template(FAMOU_DIR)
        self.initial = FAMOU_DIR / "init.py"

    def evaluate_candidate(self, source: str):
        candidate = self.write_candidate(source, self.temp_dir)
        return self.evaluator.evaluate(
            str(candidate),
            runner_command=[
                sys.executable,
                "-c",
                "raise SystemExit('must not run')",
                "{program_path}",
            ],
            objective=self.meta["objective"],
            validation=self.meta["validation"],
            reference_program_path=str(self.initial),
        )

    def test_renamed_key_scores_zero_without_running_runner(self):
        source = self.initial.read_text(encoding="utf-8").replace(
            '"learning_rate": 0.05', '"learning_rate_typo": 0.05'
        )
        result = self.evaluate_candidate(source)

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("键集合", result.artifacts["evaluation_error"])

    def test_out_of_range_value_scores_zero(self):
        source = self.initial.read_text(encoding="utf-8").replace(
            '"learning_rate": 0.05', '"learning_rate": 5.0'
        )
        result = self.evaluate_candidate(source)

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("learning_rate", result.artifacts["evaluation_error"])

    def test_non_integer_value_scores_zero(self):
        source = self.initial.read_text(encoding="utf-8").replace(
            '"num_leaves": 31', '"num_leaves": 31.5'
        )
        result = self.evaluate_candidate(source)

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("num_leaves", result.artifacts["evaluation_error"])

    def test_cross_constraint_is_enforced(self):
        source = self.initial.read_text(encoding="utf-8").replace(
            '"bias_correction_clamp_min": 0.7', '"bias_correction_clamp_min": 1.5'
        )
        result = self.evaluate_candidate(source)

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("bias_correction_clamp_min", result.artifacts["evaluation_error"])

    def test_structure_change_outside_params_scores_zero(self):
        source = self.initial.read_text(encoding="utf-8") + "\nimport os\n"
        result = self.evaluate_candidate(source)

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("结构", result.artifacts["evaluation_error"])

    def test_mode_none_allows_free_rewriting(self):
        meta = load_template(GENERIC_DIR)
        candidate = self.write_candidate("def solve():\n    return {'score': 1.0}\n", self.temp_dir)
        result = self.evaluator.evaluate(
            str(candidate),
            runner_command=self.echo_command("{'score': 1.0}"),
            objective=meta["objective"],
            validation=meta["validation"],
            reference_program_path=str(candidate),
        )

        self.assertEqual(result.metrics["combined_score"], 1.0)

    def test_validate_validation_rejects_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "validation.mode"):
            self.evaluator.validate_validation({"mode": "signature"})


class TestSolverRunner(SolverEvaluatorTestBase):
    """评测命令的失败路径都记 0 分并保留原因。"""

    def test_missing_runner_configuration_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "runner_command"):
            self.evaluator.evaluate(str(GENERIC_DIR / "initial_program.py"))

    def test_runner_command_must_include_program_path_placeholder(self):
        with self.assertRaisesRegex(ValueError, r"\{program_path\}"):
            self.evaluator.evaluate(
                str(GENERIC_DIR / "initial_program.py"),
                runner_command=[sys.executable, "-c", "pass"],
            )

    def test_non_zero_exit_scores_zero(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=[sys.executable, "-c", "raise SystemExit('boom')", "{program_path}"],
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("退出码", result.artifacts["evaluation_error"])

    def test_invalid_json_scores_zero(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=[sys.executable, "-c", "print('not json')", "{program_path}"],
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("JSON", result.artifacts["evaluation_error"])

    def test_missing_metric_scores_zero(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=self.echo_command("{'other': 1.0}"),
            objective={"metrics": [{"name": "score", "direction": "maximize"}]},
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("score", result.artifacts["evaluation_error"])

    def test_timeout_scores_zero(self):
        result = self.evaluator.evaluate(
            str(GENERIC_DIR / "initial_program.py"),
            runner_command=[sys.executable, "-c", "import time;time.sleep(5)", "{program_path}"],
            timeout_seconds=1,
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("超时", result.artifacts["evaluation_error"])


class TestSolverTemplates(SolverEvaluatorTestBase):
    """内置模板必须自带可跑通的评测闭环。"""

    def test_every_template_declares_objective_and_files(self):
        template_dirs = [
            path for path in TEMPLATE_DIR.iterdir() if (path / "template.yml").is_file()
        ]
        self.assertTrue(template_dirs)
        for directory in template_dirs:
            with self.subTest(template=directory.name):
                meta = load_template(directory)
                self.assertTrue(meta.get("name"))
                self.assertIn("metrics", meta["objective"])
                self.evaluator.validate_objective(meta["objective"])
                self.evaluator.validate_validation(meta.get("validation"))
                files = meta.get("files") or {}
                for key, default in (
                    ("initial_program", "init.py"),
                    ("prompt", "prompt.md"),
                    ("adapter", "runner_adapter.py"),
                ):
                    self.assertTrue((directory / (files.get(key) or default)).is_file())

    def test_param_tuning_template_runs_end_to_end(self):
        meta = load_template(PARAM_DIR)
        seed = PARAM_DIR / meta["files"]["initial_program"]
        result = self.evaluator.evaluate(
            str(seed),
            runner_command=[sys.executable, str(PARAM_DIR / "runner_adapter.py"), "{program_path}"],
            objective=meta["objective"],
            validation=meta["validation"],
            reference_program_path=str(seed),
        )

        self.assertGreater(result.metrics["combined_score"], 0.0)
        self.assertGreater(result.metrics["loss"], 0.0)

    def test_generic_python_template_runs_end_to_end(self):
        meta = load_template(GENERIC_DIR)
        seed = GENERIC_DIR / meta["files"]["initial_program"]
        result = self.evaluator.evaluate(
            str(seed),
            runner_command=[
                sys.executable,
                str(GENERIC_DIR / "runner_adapter.py"),
                "{program_path}",
            ],
            objective=meta["objective"],
            validation=meta.get("validation"),
            reference_program_path=str(seed),
        )

        self.assertLess(result.metrics["combined_score"], 0.0)
        self.assertAlmostEqual(result.metrics["combined_score"], result.metrics["score"])


if __name__ == "__main__":
    unittest.main()

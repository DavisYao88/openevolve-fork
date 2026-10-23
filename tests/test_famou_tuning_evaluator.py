"""Tests for the famou OpenEvolve evaluator."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "famou" / "调参"
INITIAL_PROGRAM = TASK_DIR / "init.py"


class TestFamouTuningEvaluator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evaluator_path = TASK_DIR / "evaluator.py"
        spec = importlib.util.spec_from_file_location("famou_tuning_evaluator", evaluator_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load evaluator from {evaluator_path}")
        cls.evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.evaluator)

    def test_valid_candidate_uses_adapter_wmape_and_bias(self):
        result = self.evaluator.evaluate(
            str(INITIAL_PROGRAM),
            runner_command=[
                sys.executable,
                "-c",
                "import json,sys; print(json.dumps({'wmape':0.1,'bias_ratio':0.06,'path':sys.argv[1]}, ensure_ascii=False))",
                "{program_path}",
            ],
        )

        self.assertAlmostEqual(result.metrics["combined_score"], 229.25850929940457)
        self.assertEqual(result.metrics["wmape"], 0.1)
        self.assertEqual(result.metrics["bias_ratio"], 0.06)
        self.assertIn(str(INITIAL_PROGRAM), result.artifacts["runner_output"])

    def test_invalid_parameter_key_returns_zero_without_training(self):
        source = INITIAL_PROGRAM.read_text(encoding="utf-8").replace(
            '"learning_rate": 0.05',
            '"learning_rate_typo": 0.05',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "candidate.py"
            candidate.write_text(source, encoding="utf-8")
            result = self.evaluator.evaluate(
                str(candidate),
                runner_command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit('must not run')",
                    "{program_path}",
                ],
                reference_program_path=str(INITIAL_PROGRAM),
            )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("键集合", result.artifacts["evaluation_error"])

    def test_invalid_runner_metric_returns_zero(self):
        result = self.evaluator.evaluate(
            str(INITIAL_PROGRAM),
            runner_command=[
                sys.executable,
                "-c",
                "import json; print(json.dumps({'wmape': 0, 'bias_ratio': 0}))",
                "{program_path}",
            ],
        )

        self.assertEqual(result.metrics["combined_score"], 0.0)
        self.assertIn("wmape", result.artifacts["evaluation_error"])

    def test_missing_runner_configuration_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "runner_command"):
            self.evaluator.evaluate(str(INITIAL_PROGRAM))

    def test_open_evolve_config_loads_with_runner_arguments(self):
        from openevolve.config import load_config

        config = load_config(str(TASK_DIR / "config.yml"))

        self.assertEqual(config.max_iterations, 50)
        self.assertTrue(config.enforce_evolve_blocks)
        self.assertFalse(config.evaluator.cascade_evaluation)
        self.assertEqual(config.evaluator.evaluator_args["runner_command"], [])
        self.assertEqual(config.prompt.system_message, "prompt")

        from openevolve.prompt.templates import TemplateManager

        templates = TemplateManager(custom_template_dir=config.prompt.template_dir)
        self.assertEqual(
            templates.get_template(config.prompt.system_message),
            (TASK_DIR / "prompt.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()

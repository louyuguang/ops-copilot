from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.scenario_matrix_regression import run_scenario_matrix

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "scripts" / "scenario_matrix_regression.py"


class ScenarioMatrixRegressionTest(unittest.TestCase):
    def test_run_scenario_matrix_contains_required_core_fields(self) -> None:
        report = run_scenario_matrix()

        required_cases = {
            "llm_key_missing",
            "llm_call_failed_after_retry",
            "chroma_down",
            "retrieval_empty",
        }
        self.assertEqual(required_cases, set(report.get("cases", {}).keys()))

        required_fields = {
            "run_status",
            "had_fallback",
            "fallback_count",
            "had_retry",
            "total_retry_count",
            "primary_path",
            "effective_path",
            "error_type",
            "path_decision",
        }
        for case_name in required_cases:
            payload = report["cases"][case_name]
            self.assertTrue(required_fields.issubset(set(payload.keys())), case_name)

    def test_script_writes_json_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "matrix.json"
            import os

            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--output-json", str(output)],
                cwd=BASE_DIR,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("SCENARIO_MATRIX_REPORT", proc.stdout)
            self.assertTrue(output.exists())

            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("scenario_matrix_regression", data.get("meta", {}).get("suite"))
            self.assertEqual(4, data.get("summary", {}).get("case_count"))


if __name__ == "__main__":
    unittest.main()

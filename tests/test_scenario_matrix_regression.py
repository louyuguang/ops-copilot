from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.scenario_matrix_regression import compare_with_baseline, run_scenario_matrix

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

    def test_compare_with_baseline_detects_field_diff_and_warn_gate(self) -> None:
        baseline = {
            "cases": {
                "llm_key_missing": {
                    "run_status": "ok",
                    "had_fallback": False,
                    "fallback_count": 0,
                    "had_retry": False,
                    "total_retry_count": 0,
                    "primary_path": "llm",
                    "effective_path": "rule",
                    "path_decision": {"action": "fallback", "focus": "generator"},
                    "error_type": "llm_key_missing",
                }
            }
        }
        latest = {
            "cases": {
                "llm_key_missing": {
                    "run_status": "ok",
                    "had_fallback": True,
                    "fallback_count": 1,
                    "had_retry": False,
                    "total_retry_count": 0,
                    "primary_path": "llm",
                    "effective_path": "rule",
                    "path_decision": {"action": "fallback", "focus": "generator"},
                    "error_type": "llm_key_missing",
                }
            }
        }

        diff_report = compare_with_baseline(
            latest_report=latest,
            baseline_report=baseline,
            warn_threshold=0,
            fail_on_warn=True,
        )

        self.assertEqual(2, diff_report["summary"]["warning_count"])
        self.assertTrue(diff_report["gate"]["warn_triggered"])
        self.assertTrue(diff_report["gate"]["should_fail"])
        self.assertEqual(2, diff_report["gate"]["exit_code"])

    def test_compare_with_baseline_supports_allow_field_change(self) -> None:
        baseline = {
            "cases": {
                "llm_key_missing": {
                    "run_status": "ok",
                    "had_fallback": False,
                    "fallback_count": 0,
                    "had_retry": False,
                    "total_retry_count": 0,
                    "primary_path": "llm",
                    "effective_path": "rule",
                    "path_decision": {"action": "fallback", "focus": "generator"},
                    "error_type": "llm_key_missing",
                }
            }
        }
        latest = {
            "cases": {
                "llm_key_missing": {
                    "run_status": "ok",
                    "had_fallback": True,
                    "fallback_count": 0,
                    "had_retry": False,
                    "total_retry_count": 0,
                    "primary_path": "llm",
                    "effective_path": "rule",
                    "path_decision": {"action": "fallback", "focus": "generator"},
                    "error_type": "llm_key_missing",
                }
            }
        }

        diff_report = compare_with_baseline(
            latest_report=latest,
            baseline_report=baseline,
            warn_threshold=0,
            fail_on_warn=True,
            allow_field_changes={("llm_key_missing", "had_fallback")},
        )

        self.assertEqual(0, diff_report["summary"]["warning_count"])
        self.assertEqual(1, diff_report["summary"]["allowed_change_count"])
        self.assertEqual("changed_allowed", diff_report["cases"]["llm_key_missing"]["status"])
        self.assertFalse(diff_report["gate"]["warn_triggered"])
        self.assertEqual(0, diff_report["gate"]["exit_code"])

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

    def test_script_compare_and_fail_on_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "latest.json"
            baseline = Path(tmpdir) / "baseline.json"
            diff = Path(tmpdir) / "diff.json"

            # baseline has one intentionally changed field
            baseline_data = run_scenario_matrix()
            baseline_data["cases"]["llm_key_missing"]["had_fallback"] = not baseline_data["cases"]["llm_key_missing"]["had_fallback"]
            baseline.write_text(json.dumps(baseline_data, ensure_ascii=False, indent=2), encoding="utf-8")

            import os

            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-json",
                    str(output),
                    "--baseline-json",
                    str(baseline),
                    "--diff-json",
                    str(diff),
                    "--warn-threshold",
                    "0",
                    "--fail-on-warn",
                ],
                cwd=BASE_DIR,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(2, proc.returncode)
            self.assertTrue(diff.exists())
            self.assertIn("SCENARIO_MATRIX_DIFF_SUMMARY", proc.stdout)
            diff_data = json.loads(diff.read_text(encoding="utf-8"))
            self.assertGreaterEqual(diff_data.get("summary", {}).get("warning_count", 0), 1)
            self.assertTrue(diff_data.get("gate", {}).get("should_fail"))


if __name__ == "__main__":
    unittest.main()

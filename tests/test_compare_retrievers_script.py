from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = BASE_DIR / "scripts" / "compare_retrievers.py"


spec = spec_from_file_location("compare_retrievers", SCRIPT_PATH)
compare_retrievers = module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(compare_retrievers)


class CompareRetrieverScriptUnitTest(unittest.TestCase):
    def test_parse_top_k_values(self) -> None:
        self.assertEqual([1, 3, 5], compare_retrievers.parse_top_k_values("1,3,5"))
        self.assertEqual([], compare_retrievers.parse_top_k_values(None))

        with self.assertRaises(ValueError):
            compare_retrievers.parse_top_k_values("0,2")

    def test_summarize_diffs(self) -> None:
        case_diffs = [
            {
                "summary_equal": True,
                "recommended_refs_diff": False,
                "possible_causes_diff": True,
                "suggested_checks_diff": False,
            },
            {
                "summary_equal": False,
                "recommended_refs_diff": True,
                "possible_causes_diff": False,
                "suggested_checks_diff": True,
            },
        ]

        summary = compare_retrievers.summarize_diffs(case_diffs)
        self.assertEqual(2, summary["sample_count"])
        self.assertEqual(1, summary["recommended_refs_diff_count"])
        self.assertEqual(1, summary["possible_causes_diff_count"])
        self.assertEqual(1, summary["suggested_checks_diff_count"])
        self.assertEqual(1, summary["summary_equal_true_count"])
        self.assertEqual(1, summary["summary_equal_false_count"])

    def test_build_variants_for_matrix_and_fallback(self) -> None:
        parser = compare_retrievers.argparse.ArgumentParser()
        parser.add_argument("--top-k-values")
        parser.add_argument("--simulate-chroma-down", action="store_true")
        args = parser.parse_args(["--top-k-values", "1,3", "--simulate-chroma-down"])

        variants = compare_retrievers.build_variants(args)
        names = [v["name"] for v in variants]
        self.assertEqual(["local", "chroma_top_k_1", "chroma_top_k_3", "chroma_fallback"], names)

    def test_build_trend_vs_baseline(self) -> None:
        report = {
            "comparisons": [
                {
                    "name": "local_vs_chroma",
                    "summary": {
                        "recommended_refs_diff_count": 2,
                        "possible_causes_diff_count": 1,
                        "suggested_checks_diff_count": 3,
                        "summary_equal_true_count": 0,
                        "summary_equal_false_count": 3,
                    },
                }
            ]
        }
        baseline = {
            "comparisons": [
                {
                    "name": "local_vs_chroma",
                    "summary": {
                        "recommended_refs_diff_count": 1,
                        "possible_causes_diff_count": 1,
                        "suggested_checks_diff_count": 1,
                        "summary_equal_true_count": 1,
                        "summary_equal_false_count": 2,
                    },
                }
            ]
        }

        trend = compare_retrievers.build_trend_vs_baseline(report, baseline)
        self.assertEqual(1, len(trend))
        self.assertEqual("ok", trend[0]["status"])
        self.assertEqual(1, trend[0]["delta"]["recommended_refs_diff_count"])
        self.assertEqual(2, trend[0]["delta"]["suggested_checks_diff_count"])
        self.assertEqual(-1, trend[0]["delta"]["summary_equal_true_count"])

    def test_baseline_coverage_stats(self) -> None:
        report = {
            "comparisons": [
                {"name": "local_vs_chroma"},
                {"name": "local_vs_chroma_top_k_1"},
            ]
        }
        baseline = {
            "comparisons": [
                {"name": "local_vs_chroma"},
                {"name": "local_vs_chroma_top_k_3"},
            ]
        }
        trend_rows = [
            {"name": "local_vs_chroma", "status": "ok"},
            {"name": "local_vs_chroma_top_k_1", "status": "baseline_missing"},
        ]

        stats = compare_retrievers.build_baseline_coverage_stats(report, baseline, trend_rows)
        self.assertEqual(1, stats["baseline_missing_count"])
        self.assertEqual(["local_vs_chroma_top_k_1"], stats["baseline_missing_names"])
        self.assertEqual(1, stats["comparison_missing_in_baseline_count"])
        self.assertEqual(["local_vs_chroma_top_k_3"], stats["comparison_missing_in_baseline_names"])

    def test_main_output_json_warnings_and_meta(self) -> None:
        def fake_run_case(sample: str, retriever: str, chroma_top_k=None, extra_env=None):
            base = {
                "summary": f"summary-{sample}",
                "possible_causes": ["cause-a"],
                "suggested_checks": ["check-a"],
                "recommended_refs": ["ref-a"],
                "retriever_metadata": {"query_len": 1, "retrieved_context_len": 2, "top_k": 3, "returned_count": 1, "fallback": False},
            }
            if retriever == "chroma":
                base["recommended_refs"] = ["ref-b"]
                base["possible_causes"] = ["cause-b"]
                base["suggested_checks"] = ["check-b"]
            return base

        with tempfile.TemporaryDirectory() as tmpdir:
            output_json = Path(tmpdir) / "reports" / "latest.json"
            baseline_json = Path(tmpdir) / "baseline.json"
            baseline_json.write_text(
                json.dumps(
                    {
                        "comparisons": [
                            {
                                "name": "local_vs_chroma",
                                "summary": {
                                    "recommended_refs_diff_count": 0,
                                    "possible_causes_diff_count": 0,
                                    "suggested_checks_diff_count": 0,
                                    "summary_equal_true_count": 1,
                                    "summary_equal_false_count": 0,
                                },
                            },
                            {
                                "name": "local_vs_chroma_top_k_9",
                                "summary": {},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            argv = [
                "compare_retrievers.py",
                "--samples",
                "high_cpu",
                "--output-json",
                str(output_json),
                "--baseline-json",
                str(baseline_json),
                "--warn-threshold",
                "0",
            ]
            buf = io.StringIO()
            with patch.object(compare_retrievers, "run_case", side_effect=fake_run_case), patch("sys.argv", argv), contextlib.redirect_stdout(buf):
                rc = compare_retrievers.main()

            self.assertEqual(0, rc)
            self.assertTrue(output_json.exists())
            written = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertIn("meta", written)
            self.assertIn("run_timestamp", written["meta"])
            self.assertIn("git_commit", written["meta"])
            self.assertIn("args", written["meta"])
            self.assertIn("samples", written["meta"]["args"])
            self.assertIn("trend_vs_baseline", written)
            self.assertIn("baseline_coverage", written)
            self.assertEqual(1, written["baseline_coverage"]["comparison_missing_in_baseline_count"])
            self.assertTrue(written["warnings"])
            self.assertEqual("local_vs_chroma", written["trend_vs_baseline"][0]["name"])
            self.assertIn("--- WARNINGS ---", buf.getvalue())

    def test_main_fail_on_warn_exit_code(self) -> None:
        def fake_run_case(sample: str, retriever: str, chroma_top_k=None, extra_env=None):
            base = {
                "summary": f"summary-{sample}",
                "possible_causes": ["cause-a"],
                "suggested_checks": ["check-a"],
                "recommended_refs": ["ref-a"],
                "retriever_metadata": {},
            }
            if retriever == "chroma":
                base["recommended_refs"] = ["ref-b"]
            return base

        argv = [
            "compare_retrievers.py",
            "--samples",
            "high_cpu",
            "--warn-threshold",
            "0",
            "--fail-on-warn",
        ]
        with patch.object(compare_retrievers, "run_case", side_effect=fake_run_case), patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            rc = compare_retrievers.main()
        self.assertNotEqual(0, rc)


if __name__ == "__main__":
    unittest.main()

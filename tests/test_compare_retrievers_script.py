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

    def test_main_output_json_and_warnings(self) -> None:
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
                            }
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
            self.assertIn("trend_vs_baseline", written)
            self.assertTrue(written["warnings"])
            self.assertEqual("local_vs_chroma", written["trend_vs_baseline"][0]["name"])
            self.assertIn("--- WARNINGS ---", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()

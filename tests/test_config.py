import os
import unittest

from opscopilot.config import ConfigError, resolve_runtime_config
from opscopilot.io import load_event
from opscopilot.knowledge import ChromaCardRetriever
from opscopilot.llm_engine import LLMAnalyzer

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class ConfigResolutionTest(unittest.TestCase):
    def test_priority_cli_over_env_for_core_config(self) -> None:
        env = {
            "ANALYSIS_MODE": "rule",
            "RETRIEVER_MODE": "local",
            "CHROMA_TOP_K": "9",
        }
        cfg = resolve_runtime_config(
            cli_analysis_mode="llm",
            cli_retriever_mode="chroma",
            cli_chroma_top_k=2,
            env=env,
        )

        self.assertEqual("llm", cfg.analysis_mode)
        self.assertEqual("chroma", cfg.retriever_mode)
        self.assertEqual(2, cfg.chroma_top_k)

    def test_priority_env_over_default_when_cli_missing(self) -> None:
        env = {
            "ANALYSIS_MODE": "llm",
            "RETRIEVER_MODE": "chroma",
            "CHROMA_TOP_K": "5",
        }
        cfg = resolve_runtime_config(
            cli_analysis_mode=None,
            cli_retriever_mode=None,
            cli_chroma_top_k=None,
            env=env,
        )

        self.assertEqual("llm", cfg.analysis_mode)
        self.assertEqual("chroma", cfg.retriever_mode)
        self.assertEqual(5, cfg.chroma_top_k)

    def test_invalid_chroma_top_k_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            resolve_runtime_config(
                cli_analysis_mode=None,
                cli_retriever_mode=None,
                cli_chroma_top_k=None,
                env={"CHROMA_TOP_K": "abc"},
            )

    def test_llm_mode_without_api_key_emits_fallback_warning(self) -> None:
        cfg = resolve_runtime_config(
            cli_analysis_mode="llm",
            cli_retriever_mode=None,
            cli_chroma_top_k=None,
            env={},
        )
        self.assertTrue(cfg.warnings)
        self.assertIn("OPENAI_API_KEY", cfg.warnings[0])


class ConfigBehaviorTest(unittest.TestCase):
    def test_llm_missing_api_key_falls_back_with_clear_reason(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        analyzer = LLMAnalyzer(client=None)
        result = analyzer.generate(event, "")

        self.assertIn("high_cpu", result.summary)
        self.assertEqual("llm_api_key_missing", analyzer.last_metadata.get("fallback_reason"))

    def test_chroma_top_k_env_invalid_raises_on_retriever_init(self) -> None:
        prev = os.environ.get("CHROMA_TOP_K")
        os.environ["CHROMA_TOP_K"] = "0"
        try:
            with self.assertRaises(ConfigError):
                _ = ChromaCardRetriever()
        finally:
            if prev is None:
                os.environ.pop("CHROMA_TOP_K", None)
            else:
                os.environ["CHROMA_TOP_K"] = prev


if __name__ == "__main__":
    unittest.main()

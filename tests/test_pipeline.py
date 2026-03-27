from pathlib import Path
import json
import os
import unittest

from opscopilot.io import load_event
from opscopilot.knowledge import ChromaCardRetriever, LocalCardRetriever
from opscopilot.llm_engine import LLMAnalyzer
from opscopilot.pipeline import IncidentAnalysisPipeline
from opscopilot.models import IncidentEvent
from opscopilot.rule_engine import RuleBasedAnalyzer


BASE_DIR = Path(__file__).resolve().parent.parent


class FakeClient:
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        _ = system_prompt
        parsed = json.loads(user_prompt)
        assert "incident" in parsed["input_layers"]
        assert "retrieved_context" in parsed["input_layers"]
        assert "rule_result" in parsed["input_layers"]
        return {
            "summary": "LLM summary",
            "possible_causes": ["A", "B"],
            "suggested_checks": ["C"],
            "recommended_refs": ["docs/cards/custom.md"],
            "confidence": "high",
        }


class FailingClient:
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        _ = (system_prompt, user_prompt)
        raise RuntimeError("boom")


class FakeChromaAPI:
    def query(self, query_embedding: list[float], n_results: int) -> dict:
        _ = query_embedding
        docs = [
            "# high_cpu\n\nmock chroma card",
            "# high_memory\n\nmock chroma memory card",
        ]
        meta = [
            {"path": "docs/cards/high_cpu.md", "event_type": "high_cpu"},
            {"path": "docs/cards/high_memory.md", "event_type": "high_memory"},
        ]
        ids = ["high_cpu", "high_memory"]
        return {
            "ids": [ids[:n_results]],
            "documents": [docs[:n_results]],
            "metadatas": [meta[:n_results]],
            "distances": [[0.1, 0.2][:n_results]],
        }


class FailingChromaAPI:
    def query(self, query_embedding: list[float], n_results: int) -> dict:
        _ = (query_embedding, n_results)
        raise RuntimeError("chroma_down")


class PipelineTest(unittest.TestCase):
    def test_high_cpu_sample_rule(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"

        event = load_event(event_path)
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(cards_dir), RuleBasedAnalyzer())
        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        self.assertGreaterEqual(len(result.possible_causes), 1)
        self.assertIn("docs/cards/high_cpu.md", result.recommended_refs)
        self.assertEqual("rule", pipeline.last_run_metadata["generator"].get("mode"))

    def test_high_cpu_sample_llm(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"

        event = load_event(event_path)
        generator = LLMAnalyzer(client=FakeClient())
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(cards_dir), generator)
        result = pipeline.run(event)

        self.assertEqual("LLM summary", result.summary)
        self.assertEqual(["A", "B"], result.possible_causes)
        self.assertEqual(["C"], result.suggested_checks)
        # pipeline retriever refs should take priority
        self.assertIn("docs/cards/high_cpu.md", result.recommended_refs)
        self.assertTrue(pipeline.last_run_metadata["generator"].get("llm_used"))
        self.assertEqual(
            ["incident", "retrieved_context", "rule_result"],
            pipeline.last_run_metadata["generator"].get("input_layers"),
        )

    def test_unknown_event_type_and_missing_card(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"

        event = load_event(event_path)
        event.raw["event_type"] = "disk_io_saturation"
        event = IncidentEvent.from_dict(event.raw)

        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(cards_dir), RuleBasedAnalyzer())
        result = pipeline.run(event)

        self.assertEqual(["需要进一步分析"], result.possible_causes)
        self.assertEqual(["需要补充标准排查项"], result.suggested_checks)
        self.assertEqual([], result.recommended_refs)
        self.assertFalse(pipeline.last_run_metadata["retriever"].get("card_found"))

    def test_llm_fallback_when_client_failed(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"

        event = load_event(event_path)
        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(cards_dir),
            LLMAnalyzer(client=FailingClient()),
        )
        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        metadata = pipeline.last_run_metadata["generator"]
        self.assertTrue(metadata.get("fallback"))
        self.assertEqual("llm_error:RuntimeError", metadata.get("fallback_reason"))

    def test_chroma_retriever_returns_context(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        event = load_event(event_path)

        retriever = ChromaCardRetriever(api=FakeChromaAPI(), top_k=2)
        context, refs = retriever.fetch(event)

        self.assertIn("mock chroma card", context)
        self.assertEqual(["docs/cards/high_cpu.md", "docs/cards/high_memory.md"], refs)
        self.assertEqual("chroma", retriever.last_metadata.get("mode"))
        self.assertEqual(2, retriever.last_metadata.get("top_k"))
        self.assertFalse(retriever.last_metadata.get("fallback"))
        self.assertEqual(2, retriever.last_metadata.get("returned_count"))
        self.assertEqual(2, len(retriever.last_metadata.get("matched_cards", [])))
        self.assertGreater(retriever.last_metadata.get("retrieved_context_len", 0), 0)
        self.assertGreater(retriever.last_metadata.get("query_len", 0), 0)

    def test_chroma_fallback_to_local_when_error(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"
        event = load_event(event_path)

        retriever = ChromaCardRetriever(
            api=FailingChromaAPI(),
            fallback=LocalCardRetriever(cards_dir),
        )
        context, refs = retriever.fetch(event)

        self.assertIn("CPU", context)
        self.assertIn("docs/cards/high_cpu.md", refs)
        self.assertTrue(retriever.last_metadata.get("fallback"))
        self.assertEqual("local", retriever.last_metadata.get("fallback_target"))
        self.assertTrue(retriever.last_metadata.get("local", {}).get("mode") == "local")
        self.assertGreater(retriever.last_metadata.get("retrieved_context_len", 0), 0)
        self.assertGreater(retriever.last_metadata.get("query_len", 0), 0)

    def test_local_retriever_metadata_contains_required_fields(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_memory.json")
        retriever = LocalCardRetriever(BASE_DIR / "docs" / "cards")
        _, _ = retriever.fetch(event)

        metadata = retriever.last_metadata
        self.assertEqual("local", metadata.get("mode"))
        self.assertTrue(metadata.get("query"))
        self.assertTrue(metadata.get("query_summary"))
        self.assertGreater(metadata.get("query_len", 0), 0)
        self.assertEqual(1, metadata.get("top_k"))
        self.assertGreater(metadata.get("retrieved_context_len", 0), 0)
        self.assertEqual(1, metadata.get("returned_count"))
        self.assertFalse(metadata.get("fallback"))
        self.assertIsNone(metadata.get("fallback_reason"))

    def test_chroma_top_k_from_env(self) -> None:
        os.environ["CHROMA_TOP_K"] = "5"
        try:
            retriever = ChromaCardRetriever(api=FakeChromaAPI())
            self.assertEqual(5, retriever.top_k)
        finally:
            os.environ.pop("CHROMA_TOP_K", None)


class RetrieverComparisonTest(unittest.TestCase):
    def test_local_vs_chroma_minimal_coverage(self) -> None:
        cards_dir = BASE_DIR / "docs" / "cards"
        local = LocalCardRetriever(cards_dir)
        chroma = ChromaCardRetriever(api=FakeChromaAPI(), top_k=1, fallback=local)

        for sample in ["high_cpu", "high_memory", "mysql_too_many_connections"]:
            event = load_event(BASE_DIR / "samples" / "incidents" / f"{sample}.json")

            local_context, local_refs = local.fetch(event)
            chroma_context, chroma_refs = chroma.fetch(event)

            self.assertTrue(local_context.strip())
            self.assertTrue(local_refs)
            self.assertTrue(chroma_context.strip())
            self.assertTrue(chroma_refs)


class CompareScriptTest(unittest.TestCase):
    def test_compare_script_prints_diff_sections(self) -> None:
        import subprocess
        import sys

        script = BASE_DIR / "scripts" / "compare_retrievers.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"

        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=BASE_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        stdout = proc.stdout
        self.assertIn("=== high_cpu ===", stdout)
        self.assertIn("recommended_refs", stdout)
        self.assertIn("possible_causes", stdout)
        self.assertIn("suggested_checks", stdout)
        self.assertIn("--- JSON_REPORT ---", stdout)


if __name__ == "__main__":
    unittest.main()

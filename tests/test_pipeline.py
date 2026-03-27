from pathlib import Path
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
        _ = (system_prompt, user_prompt)
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
        _ = (query_embedding, n_results)
        return {
            "ids": [["high_cpu"]],
            "documents": [["# high_cpu\n\nmock chroma card"]],
            "metadatas": [[{"path": "docs/cards/high_cpu.md"}]],
            "distances": [[0.1]],
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

        retriever = ChromaCardRetriever(api=FakeChromaAPI())
        context, refs = retriever.fetch(event)

        self.assertIn("mock chroma card", context)
        self.assertEqual(["docs/cards/high_cpu.md"], refs)
        self.assertEqual("chroma", retriever.last_metadata.get("mode"))
        self.assertFalse(retriever.last_metadata.get("fallback"))

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


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import json
import os
import unittest

from opscopilot.io import load_event
from opscopilot.knowledge import ChromaCardRetriever, LocalCardRetriever
from opscopilot.errors import OutputParseError
from opscopilot.llm_engine import LLMAnalyzer
from opscopilot.pipeline import IncidentAnalysisPipeline
from opscopilot.models import IncidentEvent
from opscopilot.rule_engine import RuleBasedAnalyzer
from opscopilot.workflow import ExtractStructuredChecksStep


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


class ParseFailingClient:
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        _ = (system_prompt, user_prompt)
        raise OutputParseError("bad_json")


class RetryThenSuccessClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        _ = (system_prompt, user_prompt)
        self.calls += 1
        if self.calls == 1:
            from opscopilot.errors import LLMCallError

            raise LLMCallError("timeout while connecting upstream")
        return {
            "summary": "retry success",
            "possible_causes": ["transient"],
            "suggested_checks": ["recheck"],
            "recommended_refs": ["docs/cards/custom.md"],
            "confidence": "medium",
        }


class NonRetryableLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        _ = (system_prompt, user_prompt)
        self.calls += 1
        from opscopilot.errors import LLMCallError

        raise LLMCallError("invalid request payload")


class FlakyChromaAPI:
    def __init__(self) -> None:
        self.calls = 0

    def query(self, query_embedding: list[float], n_results: int) -> dict:
        _ = (query_embedding, n_results)
        self.calls += 1
        if self.calls == 1:
            from opscopilot.errors import ExternalDependencyError

            raise ExternalDependencyError("chroma_request_failed:timeout")
        docs = ["# high_cpu\n\nretry success"]
        meta = [{"path": "docs/cards/high_cpu.md", "event_type": "high_cpu"}]
        ids = ["high_cpu"]
        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [meta],
            "distances": [[0.1]],
        }


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


class RaisingRetriever:
    def __init__(self) -> None:
        self.last_metadata: dict[str, object] = {}

    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        _ = event
        raise RuntimeError("retriever_step_boom")


class FailingBaselineAnalyzer:
    def generate(self, event: IncidentEvent, context: str):  # type: ignore[no-untyped-def]
        _ = (event, context)
        raise RuntimeError("checks_step_boom")


class ContextCapturingGenerator:
    def __init__(self) -> None:
        self.last_context = ""
        self.last_metadata: dict[str, object] = {
            "mode": "capture",
            "fallback": False,
            "path_decision": {
                "action": "primary",
                "from": "capture",
                "to": "capture",
                "reason": None,
                "after_retry": False,
            },
            "retry_count": 0,
            "retried": False,
        }

    def generate(self, event: IncidentEvent, context: str):  # type: ignore[no-untyped-def]
        _ = event
        self.last_context = context
        return RuleBasedAnalyzer().generate(event, context)


class ExplodingGenerator:
    def __init__(self) -> None:
        self.last_metadata: dict[str, object] = {}

    def generate(self, event: IncidentEvent, context: str):  # type: ignore[no-untyped-def]
        _ = (event, context)
        raise RuntimeError("final synthesis boom")


def _trace_by_step(run_meta: dict, step: str) -> dict:
    for entry in run_meta.get("workflow_trace", []):
        if entry.get("step") == step:
            return entry
    raise AssertionError(f"step not found in workflow_trace: {step}")


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
        self.assertEqual("empty_retrieval_continue", pipeline.last_run_metadata.get("run_status"))
        self.assertFalse(pipeline.last_run_metadata.get("had_fallback"))
        self.assertEqual(0, pipeline.last_run_metadata.get("fallback_count"))

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
        self.assertEqual("llm", metadata.get("fallback_from"))
        self.assertEqual("rule", metadata.get("fallback_to"))
        self.assertEqual("llm_unexpected_error:RuntimeError", metadata.get("fallback_reason"))
        self.assertEqual("llm_unexpected_error", metadata.get("error_type"))
        self.assertEqual("degraded_success", pipeline.last_run_metadata.get("run_status"))
        self.assertTrue(pipeline.last_run_metadata.get("had_fallback"))
        self.assertEqual(1, pipeline.last_run_metadata.get("fallback_count"))
        self.assertEqual("fallback", pipeline.last_run_metadata.get("final_analysis", {}).get("path"))
        self.assertEqual(
            "fallback",
            pipeline.last_run_metadata.get("decisions", {}).get("generator", {}).get("action"),
        )

    def test_llm_missing_key_fallback_semantics(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        cards_dir = BASE_DIR / "docs" / "cards"

        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(cards_dir),
            LLMAnalyzer(client=None),
        )
        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        meta = pipeline.last_run_metadata["generator"]
        self.assertTrue(meta.get("fallback"))
        self.assertEqual("llm", meta.get("fallback_from"))
        self.assertEqual("rule", meta.get("fallback_to"))
        self.assertEqual("llm_api_key_missing", meta.get("fallback_reason"))
        self.assertFalse(meta.get("fallback_after_retry"))
        self.assertEqual("degraded_success", pipeline.last_run_metadata.get("run_status"))

    def test_llm_fallback_when_output_parse_failed(self) -> None:
        event_path = BASE_DIR / "samples" / "incidents" / "high_cpu.json"
        cards_dir = BASE_DIR / "docs" / "cards"

        event = load_event(event_path)
        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(cards_dir),
            LLMAnalyzer(client=ParseFailingClient(), max_retries=2),
        )
        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        metadata = pipeline.last_run_metadata["generator"]
        self.assertTrue(metadata.get("fallback"))
        self.assertEqual("llm", metadata.get("fallback_from"))
        self.assertEqual("rule", metadata.get("fallback_to"))
        self.assertEqual("llm_output_parse_failed", metadata.get("fallback_reason"))
        self.assertEqual("output_parse_failed", metadata.get("error_type"))
        self.assertEqual(0, metadata.get("retry_count"))
        self.assertFalse(metadata.get("fallback_after_retry"))

    def test_llm_retry_then_success(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        cards_dir = BASE_DIR / "docs" / "cards"
        client = RetryThenSuccessClient()

        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(cards_dir),
            LLMAnalyzer(client=client, max_retries=1),
        )
        result = pipeline.run(event)

        self.assertEqual("retry success", result.summary)
        meta = pipeline.last_run_metadata["generator"]
        self.assertEqual(1, meta.get("retry_count"))
        self.assertTrue(meta.get("retried"))
        self.assertFalse(meta.get("fallback"))
        self.assertEqual(2, client.calls)
        self.assertTrue(pipeline.last_run_metadata.get("had_retry"))
        self.assertEqual(1, pipeline.last_run_metadata.get("total_retry_count"))

    def test_llm_non_retryable_error_fallback_without_retry(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        cards_dir = BASE_DIR / "docs" / "cards"
        client = NonRetryableLLMClient()

        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(cards_dir),
            LLMAnalyzer(client=client, max_retries=2),
        )
        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        meta = pipeline.last_run_metadata["generator"]
        self.assertTrue(meta.get("fallback"))
        self.assertEqual("llm", meta.get("fallback_from"))
        self.assertEqual("rule", meta.get("fallback_to"))
        self.assertEqual("llm_call_failed", meta.get("fallback_reason"))
        self.assertEqual(0, meta.get("retry_count"))
        self.assertFalse(meta.get("fallback_after_retry"))
        self.assertEqual(1, client.calls)

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
        self.assertEqual("chroma", retriever.last_metadata.get("fallback_from"))
        self.assertEqual("local", retriever.last_metadata.get("fallback_to"))
        self.assertEqual("local", retriever.last_metadata.get("fallback_target"))
        self.assertTrue(retriever.last_metadata.get("local", {}).get("mode") == "local")
        self.assertGreater(retriever.last_metadata.get("retrieved_context_len", 0), 0)
        self.assertGreater(retriever.last_metadata.get("query_len", 0), 0)

    def test_chroma_retry_then_success(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        retriever = ChromaCardRetriever(api=FlakyChromaAPI(), top_k=1, max_retries=1)

        context, refs = retriever.fetch(event)

        self.assertIn("retry success", context)
        self.assertIn("docs/cards/high_cpu.md", refs)
        self.assertEqual(1, retriever.last_metadata.get("retry_count"))
        self.assertTrue(retriever.last_metadata.get("retried"))
        self.assertFalse(retriever.last_metadata.get("fallback"))

    def test_pipeline_aggregated_fields_and_effective_path(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        client = NonRetryableLLMClient()

        pipeline = IncidentAnalysisPipeline(
            ChromaCardRetriever(api=FlakyChromaAPI(), top_k=1, max_retries=1),
            LLMAnalyzer(client=client, max_retries=0),
        )
        _ = pipeline.run(event)

        run_meta = pipeline.last_run_metadata
        self.assertTrue(run_meta.get("had_fallback"))
        self.assertEqual(1, run_meta.get("fallback_count"))
        self.assertTrue(run_meta.get("had_retry"))
        self.assertEqual(1, run_meta.get("total_retry_count"))
        self.assertEqual("retriever:chroma -> generator:llm", run_meta.get("primary_path"))
        self.assertEqual("retriever:chroma -> generator:rule", run_meta.get("effective_path"))

    def test_workflow_skeleton_trace_and_path(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(BASE_DIR / "docs" / "cards"), RuleBasedAnalyzer())

        _ = pipeline.run(event)

        run_meta = pipeline.last_run_metadata
        workflow_meta = run_meta.get("workflow", {})
        workflow_trace = run_meta.get("workflow_trace", [])

        self.assertEqual(
            "incident -> retrieve -> checks -> final_analysis",
            workflow_meta.get("step_path"),
        )
        self.assertEqual(
            ["incident", "retrieve", "checks", "final_analysis"],
            workflow_meta.get("steps"),
        )
        self.assertEqual(4, len(workflow_trace))
        for entry in workflow_trace:
            self.assertIn("step", entry)
            self.assertIn("status", entry)
            self.assertIn("path_decision", entry)
            self.assertIn("degraded", entry)
            self.assertIn("details", entry)
            self.assertTrue(
                {"action", "from", "to", "reason", "after_retry"}.issubset(
                    set(entry.get("path_decision", {}).keys())
                )
            )

        overview = run_meta.get("workflow_overview", {})
        self.assertEqual(4, overview.get("total_steps"))
        self.assertEqual(0, overview.get("degraded_step_count"))
        self.assertEqual(0, overview.get("fallback_step_count"))
        self.assertEqual(0, overview.get("continue_step_count"))
        self.assertEqual("primary", overview.get("final_path"))
        self.assertFalse(overview.get("degraded"))

        self.assertEqual(5, run_meta.get("structured_checks", {}).get("count"))
        self.assertEqual("structured_mixed", run_meta.get("structured_checks", {}).get("source"))
        self.assertEqual(4, run_meta.get("structured_checks", {}).get("source_counts", {}).get("rule"))
        self.assertEqual(1, run_meta.get("structured_checks", {}).get("source_counts", {}).get("retrieved_context"))
        self.assertEqual("primary", run_meta.get("structured_checks", {}).get("path"))

    def test_workflow_retrieve_metadata_is_structured(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(BASE_DIR / "docs" / "cards"), RuleBasedAnalyzer())

        _ = pipeline.run(event)
        retrieve_meta = pipeline.last_run_metadata.get("retrieve", {})

        self.assertEqual("local", retrieve_meta.get("source"))
        self.assertEqual(1, retrieve_meta.get("count"))
        self.assertIn("docs/cards/high_cpu.md", retrieve_meta.get("refs", []))
        self.assertEqual("primary", retrieve_meta.get("path"))
        self.assertFalse(retrieve_meta.get("fallback"))

    def test_final_step_consumes_retrieve_and_checks_structured_inputs(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        generator = ContextCapturingGenerator()
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(BASE_DIR / "docs" / "cards"), generator)

        _ = pipeline.run(event)

        self.assertIn("[structured_checks]", generator.last_context)
        self.assertIn("- 检查近 15 分钟 QPS 与延迟变化", generator.last_context)

        final_meta = pipeline.last_run_metadata.get("final_analysis", {})
        self.assertEqual("primary", final_meta.get("path"))
        self.assertEqual(1, final_meta.get("consumed_inputs", {}).get("retrieve", {}).get("refs_count"))
        self.assertEqual(5, final_meta.get("consumed_inputs", {}).get("checks", {}).get("count"))
        self.assertTrue(
            final_meta.get("output_sources", {})
            .get("workflow_aggregated", {})
            .get("structured_checks_in_context")
        )

    def test_final_step_fallback_when_synthesis_failed(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(BASE_DIR / "docs" / "cards"),
            ExplodingGenerator(),
        )

        result = pipeline.run(event)

        self.assertIn("high_cpu", result.summary)
        self.assertEqual("degraded_success", pipeline.last_run_metadata.get("run_status"))
        self.assertTrue(pipeline.last_run_metadata.get("had_fallback"))
        self.assertEqual("fallback", pipeline.last_run_metadata.get("final_analysis", {}).get("path"))
        self.assertEqual(
            "fallback",
            pipeline.last_run_metadata.get("workflow_trace", [])[3].get("details", {}).get("path"),
        )
        self.assertEqual(
            "degraded",
            pipeline.last_run_metadata.get("workflow_trace", [])[3].get("status"),
        )

    def test_workflow_checks_items_are_structured_with_source(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_memory.json")
        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(BASE_DIR / "docs" / "cards"), RuleBasedAnalyzer())

        _ = pipeline.run(event)
        checks_meta = pipeline.last_run_metadata.get("checks", {})
        items = checks_meta.get("items", [])

        self.assertGreaterEqual(len(items), 2)
        self.assertTrue(all("title" in x and "source" in x and "category" in x for x in items))
        self.assertIn("rule", {x.get("source") for x in items})
        self.assertIn("retrieved_context", {x.get("source") for x in items})

    def test_workflow_step_failure_keeps_pipeline_explainable(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(RaisingRetriever(), RuleBasedAnalyzer())
        pipeline.workflow.steps[1] = ExtractStructuredChecksStep(FailingBaselineAnalyzer())

        result = pipeline.run(event)

        self.assertTrue(result.summary)
        run_meta = pipeline.last_run_metadata
        self.assertEqual("continue", run_meta.get("retrieve", {}).get("path"))
        self.assertTrue(run_meta.get("retrieve", {}).get("fallback"))
        self.assertEqual("fallback", run_meta.get("structured_checks", {}).get("path"))
        self.assertEqual(1, run_meta.get("structured_checks", {}).get("source_counts", {}).get("fallback"))
        self.assertEqual("degraded", run_meta.get("workflow_trace", [])[1].get("status"))
        self.assertEqual("degraded", run_meta.get("workflow_trace", [])[2].get("status"))
        self.assertEqual("continue", run_meta.get("decisions", {}).get("retriever", {}).get("action"))
        self.assertEqual("fallback", run_meta.get("decisions", {}).get("checks", {}).get("action"))
        self.assertGreaterEqual(run_meta.get("workflow_overview", {}).get("degraded_step_count", 0), 2)

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


class WorkflowScenarioRegressionTest(unittest.TestCase):
    def _run_with(self, retriever, generator) -> dict:  # type: ignore[no-untyped-def]
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(retriever, generator)
        _ = pipeline.run(event)
        return pipeline.last_run_metadata

    def test_workflow_scenario_primary_all_steps_primary(self) -> None:
        run_meta = self._run_with(
            LocalCardRetriever(BASE_DIR / "docs" / "cards"),
            RuleBasedAnalyzer(),
        )

        overview = run_meta.get("workflow_overview", {})
        self.assertEqual("primary", overview.get("final_path"))
        self.assertEqual(0, overview.get("degraded_step_count"))
        self.assertEqual(0, overview.get("fallback_step_count"))
        self.assertEqual(0, overview.get("continue_step_count"))
        self.assertEqual(
            {"incident": "primary", "retrieve": "primary", "checks": "primary", "final_analysis": "primary"},
            overview.get("step_path_decisions"),
        )

    def test_workflow_scenario_retrieve_fail_continue_degraded(self) -> None:
        run_meta = self._run_with(RaisingRetriever(), RuleBasedAnalyzer())

        overview = run_meta.get("workflow_overview", {})
        self.assertTrue(overview.get("degraded"))
        self.assertEqual(1, overview.get("degraded_step_count"))
        self.assertEqual(1, overview.get("continue_step_count"))
        self.assertEqual("continue", overview.get("step_path_decisions", {}).get("retrieve"))

        retrieve_trace = _trace_by_step(run_meta, "retrieve")
        self.assertEqual("degraded", retrieve_trace.get("status"))
        self.assertEqual("continue", retrieve_trace.get("path_decision", {}).get("action"))

    def test_workflow_scenario_checks_fail_fallback_degraded(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        event.raw["event_type"] = "unknown_event_for_day6"
        event = IncidentEvent.from_dict(event.raw)

        pipeline = IncidentAnalysisPipeline(LocalCardRetriever(BASE_DIR / "docs" / "cards"), RuleBasedAnalyzer())
        pipeline.workflow.steps[1] = ExtractStructuredChecksStep(FailingBaselineAnalyzer())

        _ = pipeline.run(event)
        run_meta = pipeline.last_run_metadata

        overview = run_meta.get("workflow_overview", {})
        self.assertTrue(overview.get("degraded"))
        self.assertEqual(1, overview.get("degraded_step_count"))
        self.assertEqual(1, overview.get("fallback_step_count"))
        self.assertEqual("fallback", overview.get("step_path_decisions", {}).get("checks"))

        checks_trace = _trace_by_step(run_meta, "checks")
        self.assertEqual("degraded", checks_trace.get("status"))
        self.assertEqual("fallback", checks_trace.get("path_decision", {}).get("action"))

    def test_workflow_scenario_final_synthesis_fail_fallback_degraded(self) -> None:
        run_meta = self._run_with(
            LocalCardRetriever(BASE_DIR / "docs" / "cards"),
            ExplodingGenerator(),
        )

        overview = run_meta.get("workflow_overview", {})
        self.assertTrue(overview.get("degraded"))
        self.assertEqual(1, overview.get("degraded_step_count"))
        self.assertEqual(1, overview.get("fallback_step_count"))
        self.assertEqual("fallback", overview.get("final_path"))
        self.assertEqual("fallback", overview.get("step_path_decisions", {}).get("final_analysis"))

        final_trace = _trace_by_step(run_meta, "final_analysis")
        self.assertEqual("degraded", final_trace.get("status"))
        self.assertEqual("fallback", final_trace.get("path_decision", {}).get("action"))


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


class ObservabilityFieldsTest(unittest.TestCase):
    """Week 7 Day 2: request_id, total_duration_ms, per-step duration_ms."""

    def _run_pipeline(self) -> tuple:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(BASE_DIR / "docs" / "cards"),
            RuleBasedAnalyzer(),
        )
        result = pipeline.run(event)
        return result, pipeline.last_run_metadata

    def test_request_id_present_and_stable(self) -> None:
        _, meta = self._run_pipeline()
        request_id = meta.get("request_id")
        self.assertIsNotNone(request_id)
        self.assertIsInstance(request_id, str)
        self.assertEqual(len(request_id), 16)

    def test_request_id_unique_across_runs(self) -> None:
        _, meta1 = self._run_pipeline()
        _, meta2 = self._run_pipeline()
        self.assertNotEqual(meta1["request_id"], meta2["request_id"])

    def test_total_duration_ms_present(self) -> None:
        _, meta = self._run_pipeline()
        total = meta.get("total_duration_ms")
        self.assertIsNotNone(total)
        self.assertIsInstance(total, float)
        self.assertGreaterEqual(total, 0)

    def test_each_step_has_duration_ms(self) -> None:
        _, meta = self._run_pipeline()
        trace = meta.get("workflow_trace", [])
        # incident step has no duration (it's a marker, not timed work)
        timed_steps = [e for e in trace if e["step"] != "incident"]
        self.assertGreaterEqual(len(timed_steps), 3)
        for entry in timed_steps:
            self.assertIn(
                "duration_ms",
                entry,
                f"step '{entry['step']}' missing duration_ms",
            )
            self.assertIsInstance(entry["duration_ms"], float)
            self.assertGreaterEqual(entry["duration_ms"], 0)

    def test_step_durations_sum_leq_total(self) -> None:
        _, meta = self._run_pipeline()
        total = meta["total_duration_ms"]
        trace = meta.get("workflow_trace", [])
        step_sum = sum(e.get("duration_ms", 0) for e in trace if e["step"] != "incident")
        # step sum should be <= total (total includes overhead)
        self.assertLessEqual(step_sum, total + 1.0)  # 1ms tolerance for rounding

    def test_degraded_run_still_has_observability_fields(self) -> None:
        event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")
        pipeline = IncidentAnalysisPipeline(
            LocalCardRetriever(BASE_DIR / "docs" / "cards"),
            ExplodingGenerator(),
        )
        _ = pipeline.run(event)
        meta = pipeline.last_run_metadata

        self.assertIn("request_id", meta)
        self.assertIn("total_duration_ms", meta)
        trace = meta.get("workflow_trace", [])
        timed_steps = [e for e in trace if e["step"] != "incident"]
        for entry in timed_steps:
            self.assertIn("duration_ms", entry)


if __name__ == "__main__":
    unittest.main()

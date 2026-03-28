from __future__ import annotations

from typing import Any

from .interfaces import AnalysisGenerator, KnowledgeRetriever
from .models import AnalysisResult, IncidentEvent


class IncidentAnalysisPipeline:
    """Composable pipeline with clear hooks for future RAG + LLM integration."""

    def __init__(self, retriever: KnowledgeRetriever, generator: AnalysisGenerator) -> None:
        self.retriever = retriever
        self.generator = generator
        self.last_run_metadata: dict[str, Any] = {}

    def run(self, event: IncidentEvent) -> AnalysisResult:
        context_text, refs = self.retriever.fetch(event)
        result = self.generator.generate(event, context_text)

        retriever_meta = getattr(self.retriever, "last_metadata", {})
        generator_meta = getattr(self.generator, "last_metadata", {})

        run_status = "success"
        if retriever_meta.get("fallback") or generator_meta.get("fallback"):
            run_status = "degraded_success"
        elif retriever_meta.get("retrieval_status") == "empty":
            run_status = "empty_retrieval_continue"

        self.last_run_metadata = {
            "event_type": event.event_type,
            "run_status": run_status,
            "retriever": retriever_meta,
            "generator": generator_meta,
        }

        return AnalysisResult(
            summary=result.summary,
            possible_causes=result.possible_causes,
            suggested_checks=result.suggested_checks,
            recommended_refs=refs or result.recommended_refs,
            confidence=result.confidence,
        )

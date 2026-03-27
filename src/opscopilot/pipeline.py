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

        self.last_run_metadata = {
            "event_type": event.event_type,
            "retriever": getattr(self.retriever, "last_metadata", {}),
            "generator": getattr(self.generator, "last_metadata", {}),
        }

        return AnalysisResult(
            summary=result.summary,
            possible_causes=result.possible_causes,
            suggested_checks=result.suggested_checks,
            recommended_refs=refs or result.recommended_refs,
            confidence=result.confidence,
        )

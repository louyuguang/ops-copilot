from __future__ import annotations

from typing import Any

from .interfaces import AnalysisGenerator, KnowledgeRetriever, WorkflowStep
from .models import IncidentEvent, WorkflowState
from .rule_engine import RuleBasedAnalyzer


def _append_trace(state: WorkflowState, *, step: str, status: str = "ok", details: dict[str, Any] | None = None) -> None:
    state.step_trace.append(
        {
            "step": step,
            "status": status,
            "details": details or {},
        }
    )


class RetrieveKnowledgeCardsStep:
    name = "retrieve"

    def __init__(self, retriever: KnowledgeRetriever) -> None:
        self._retriever = retriever

    def execute(self, state: WorkflowState) -> WorkflowState:
        context_text, refs = self._retriever.fetch(state.event)
        state.context_text = context_text
        state.reference_paths = refs

        retriever_meta = getattr(self._retriever, "last_metadata", {})
        state.metadata["retriever"] = retriever_meta

        _append_trace(
            state,
            step=self.name,
            details={
                "returned_count": retriever_meta.get("returned_count", len(refs)),
                "retrieved_context_len": retriever_meta.get("retrieved_context_len", len(context_text)),
            },
        )
        return state


class ExtractStructuredChecksStep:
    name = "checks"

    def __init__(self, baseline_analyzer: AnalysisGenerator | None = None) -> None:
        self._baseline_analyzer = baseline_analyzer or RuleBasedAnalyzer()

    def execute(self, state: WorkflowState) -> WorkflowState:
        baseline_result = self._baseline_analyzer.generate(state.event, state.context_text)
        state.structured_checks = list(baseline_result.suggested_checks)
        state.metadata["checks"] = {
            "source": "rule_baseline",
            "count": len(state.structured_checks),
        }

        _append_trace(
            state,
            step=self.name,
            details={
                "checks_count": len(state.structured_checks),
                "source": "rule_baseline",
            },
        )
        return state


class BuildFinalAnalysisStep:
    name = "final_analysis"

    def __init__(self, generator: AnalysisGenerator) -> None:
        self._generator = generator

    def execute(self, state: WorkflowState) -> WorkflowState:
        context_for_final = state.context_text
        if state.structured_checks:
            checks_text = "\n".join(f"- {x}" for x in state.structured_checks)
            context_for_final = f"{state.context_text}\n\n[structured_checks]\n{checks_text}"

        result = self._generator.generate(state.event, context_for_final)
        state.final_result = result

        generator_meta = getattr(self._generator, "last_metadata", {})
        state.metadata["generator"] = generator_meta

        _append_trace(
            state,
            step=self.name,
            details={
                "confidence": result.confidence,
                "possible_causes_count": len(result.possible_causes),
                "suggested_checks_count": len(result.suggested_checks),
            },
        )
        return state


class IncidentWorkflowRunner:
    """Lightweight workflow skeleton for Week 6 Day 2 MVP."""

    def __init__(self, retriever: KnowledgeRetriever, generator: AnalysisGenerator) -> None:
        self.steps: list[WorkflowStep] = [
            RetrieveKnowledgeCardsStep(retriever),
            ExtractStructuredChecksStep(),
            BuildFinalAnalysisStep(generator),
        ]

    def run(self, event: IncidentEvent) -> WorkflowState:
        state = WorkflowState(event=event)
        _append_trace(state, step="incident", details={"event_type": event.event_type})

        for step in self.steps:
            state = step.execute(state)

        state.metadata["workflow"] = {
            "step_path": "incident -> retrieve -> checks -> final_analysis",
            "steps": [trace["step"] for trace in state.step_trace],
        }
        return state

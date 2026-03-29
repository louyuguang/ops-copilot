from __future__ import annotations

from typing import Any

from .interfaces import AnalysisGenerator, KnowledgeRetriever
from .models import AnalysisResult, IncidentEvent
from .workflow import IncidentWorkflowRunner


def _extract_decision(meta: dict[str, Any]) -> dict[str, Any]:
    decision = meta.get("path_decision")
    if isinstance(decision, dict):
        return {
            "action": decision.get("action", "primary"),
            "from": decision.get("from"),
            "to": decision.get("to"),
            "reason": decision.get("reason"),
            "after_retry": bool(decision.get("after_retry", False)),
        }
    return {
        "action": "fallback" if meta.get("fallback") else "primary",
        "from": meta.get("mode"),
        "to": meta.get("fallback_target"),
        "reason": meta.get("fallback_reason"),
        "after_retry": bool(meta.get("retried", False)),
    }


def _effective_mode(meta: dict[str, Any], decision: dict[str, Any]) -> str:
    if decision.get("action") == "fallback" and decision.get("to"):
        return str(decision["to"])
    return str(meta.get("mode") or "unknown")


class IncidentAnalysisPipeline:
    """Composable pipeline with clear hooks for future RAG + LLM integration."""

    def __init__(self, retriever: KnowledgeRetriever, generator: AnalysisGenerator) -> None:
        self.retriever = retriever
        self.generator = generator
        self.workflow = IncidentWorkflowRunner(retriever, generator)
        self.last_run_metadata: dict[str, Any] = {}

    def run(self, event: IncidentEvent) -> AnalysisResult:
        workflow_state = self.workflow.run(event)
        result = workflow_state.final_result
        if result is None:
            raise RuntimeError("workflow did not produce final_result")

        refs = workflow_state.reference_paths
        retriever_meta = workflow_state.metadata.get("retriever", {})
        generator_meta = workflow_state.metadata.get("generator", {})

        retriever_decision = _extract_decision(retriever_meta)
        generator_decision = _extract_decision(generator_meta)

        had_fallback = any(
            d.get("action") == "fallback" for d in [retriever_decision, generator_decision]
        )
        fallback_count = sum(
            1 for d in [retriever_decision, generator_decision] if d.get("action") == "fallback"
        )

        total_retry_count = int(retriever_meta.get("retry_count") or 0) + int(
            generator_meta.get("retry_count") or 0
        )
        had_retry = total_retry_count > 0

        run_status = "success"
        if had_fallback:
            run_status = "degraded_success"
        elif retriever_decision.get("action") == "continue":
            run_status = "empty_retrieval_continue"

        primary_path = (
            f"retriever:{retriever_meta.get('mode', 'unknown')}"
            f" -> generator:{generator_meta.get('mode', 'unknown')}"
        )
        effective_path = (
            f"retriever:{_effective_mode(retriever_meta, retriever_decision)}"
            f" -> generator:{_effective_mode(generator_meta, generator_decision)}"
        )

        self.last_run_metadata = {
            "event_type": event.event_type,
            "run_status": run_status,
            "had_fallback": had_fallback,
            "fallback_count": fallback_count,
            "had_retry": had_retry,
            "total_retry_count": total_retry_count,
            "primary_path": primary_path,
            "effective_path": effective_path,
            "retriever": retriever_meta,
            "generator": generator_meta,
            "workflow": workflow_state.metadata.get("workflow", {}),
            "workflow_trace": workflow_state.step_trace,
            "structured_checks": {
                "count": len(workflow_state.structured_checks),
                "source": workflow_state.metadata.get("checks", {}).get("source", "unknown"),
            },
            "decisions": {
                "retriever": retriever_decision,
                "generator": generator_decision,
            },
        }

        return AnalysisResult(
            summary=result.summary,
            possible_causes=result.possible_causes,
            suggested_checks=result.suggested_checks,
            recommended_refs=refs or result.recommended_refs,
            confidence=result.confidence,
        )

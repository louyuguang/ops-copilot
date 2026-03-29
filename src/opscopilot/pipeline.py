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

        retrieve_meta = workflow_state.metadata.get("retrieve", {})
        checks_meta = workflow_state.metadata.get("checks", {})
        final_meta = workflow_state.metadata.get("final_analysis", {})

        workflow_meta = workflow_state.metadata.get("workflow", {})
        workflow_overview = workflow_meta.get("overview", {}) if isinstance(workflow_meta, dict) else {}

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
            "workflow": workflow_meta,
            "workflow_overview": workflow_overview,
            "workflow_trace": workflow_state.step_trace,
            "retrieve": {
                "source": retrieve_meta.get("source", retriever_meta.get("mode", "unknown")),
                "count": int(retrieve_meta.get("count", retriever_meta.get("returned_count", 0)) or 0),
                "refs": retrieve_meta.get("refs", refs),
                "path": retrieve_meta.get("path", retriever_decision.get("action", "primary")),
                "fallback": bool(retrieve_meta.get("fallback", retriever_meta.get("fallback", False))),
            },
            "structured_checks": {
                "count": len(workflow_state.structured_checks),
                "source": checks_meta.get("source", "unknown"),
                "source_counts": checks_meta.get("source_counts", {}),
                "path": checks_meta.get("path", "primary"),
            },
            "checks": {
                "items": checks_meta.get("items", []),
                "count": int(checks_meta.get("count", 0) or 0),
                "path": checks_meta.get("path", "primary"),
            },
            "final_analysis": {
                "path": final_meta.get("path", "primary"),
                "consumed_inputs": final_meta.get("consumed_inputs", {}),
                "output_sources": final_meta.get("output_sources", {}),
                "error_type": final_meta.get("error_type"),
            },
            "output_aggregation": {
                "recommended_refs_from": "retrieve" if refs else "generator",
                "recommended_refs_count": len(refs or result.recommended_refs),
                "workflow_checks_count": len(workflow_state.structured_checks),
            },
            "decisions": {
                "retriever": retriever_decision,
                "checks": {
                    "action": checks_meta.get("path", "primary"),
                    "from": checks_meta.get("source", "structured_mixed"),
                    "to": checks_meta.get("source", "structured_mixed"),
                    "reason": checks_meta.get("error_type"),
                    "after_retry": False,
                },
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

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
        status = "ok"
        step_error: str | None = None
        try:
            context_text, refs = self._retriever.fetch(state.event)
            retriever_meta = getattr(self._retriever, "last_metadata", {})
        except Exception as exc:  # pragma: no cover - defensive fallback for unknown retrievers
            context_text, refs = "", []
            retriever_meta = {
                "mode": "unknown",
                "returned_count": 0,
                "retrieved_context_len": 0,
                "matched_cards": [],
                "error_type": "retriever_step_failed",
                "error_message": str(exc),
                "fallback": True,
                "fallback_reason": "retriever_step_failed",
                "path_decision": {
                    "action": "continue",
                    "from": "unknown",
                    "to": "continue",
                    "reason": "retriever_step_failed",
                    "after_retry": False,
                },
            }
            status = "degraded"
            step_error = str(exc)

        state.context_text = context_text
        state.reference_paths = refs
        state.metadata["retriever"] = retriever_meta

        retrieve_summary = {
            "source": retriever_meta.get("mode", "unknown"),
            "count": int(retriever_meta.get("returned_count", len(refs)) or 0),
            "refs": list(refs),
            "matched_cards": list(retriever_meta.get("matched_cards") or []),
            "context_len": int(retriever_meta.get("retrieved_context_len", len(context_text)) or 0),
            "path": retriever_meta.get("path_decision", {}).get("action", "primary"),
            "fallback": bool(retriever_meta.get("fallback", False)),
            "error_type": retriever_meta.get("error_type"),
        }
        state.metadata["retrieve"] = retrieve_summary

        details = {
            "returned_count": retrieve_summary["count"],
            "source": retrieve_summary["source"],
            "path": retrieve_summary["path"],
            "fallback": retrieve_summary["fallback"],
            "retrieved_context_len": retrieve_summary["context_len"],
        }
        if step_error:
            details["error"] = step_error

        _append_trace(state, step=self.name, status=status, details=details)
        return state


class ExtractStructuredChecksStep:
    name = "checks"

    def __init__(self, baseline_analyzer: AnalysisGenerator | None = None) -> None:
        self._baseline_analyzer = baseline_analyzer or RuleBasedAnalyzer()

    def execute(self, state: WorkflowState) -> WorkflowState:
        status = "ok"
        step_error: str | None = None
        structured_items: list[dict[str, Any]] = []

        try:
            baseline_result = self._baseline_analyzer.generate(state.event, state.context_text)
            for idx, check in enumerate(baseline_result.suggested_checks, start=1):
                structured_items.append(
                    {
                        "id": f"rule-{idx}",
                        "title": str(check),
                        "category": "baseline",
                        "source": "rule",
                        "refs": [],
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive fallback for custom analyzers
            status = "degraded"
            step_error = str(exc)

        if state.context_text.strip() and state.reference_paths:
            structured_items.append(
                {
                    "id": "ctx-1",
                    "title": "结合检索到的知识卡片逐条核对告警描述与已知故障特征",
                    "category": "context_alignment",
                    "source": "retrieved_context",
                    "refs": list(state.reference_paths),
                }
            )

        if not structured_items:
            structured_items = [
                {
                    "id": "fallback-1",
                    "title": "基础排查信息不足，请先核对服务健康度、最近发布与核心监控指标",
                    "category": "fallback",
                    "source": "fallback",
                    "refs": [],
                }
            ]

        state.structured_check_items = structured_items
        state.structured_checks = [str(item.get("title", "")).strip() for item in structured_items if str(item.get("title", "")).strip()]

        source_counts: dict[str, int] = {}
        for item in structured_items:
            src = str(item.get("source", "unknown"))
            source_counts[src] = source_counts.get(src, 0) + 1

        checks_meta = {
            "source": "structured_mixed",
            "count": len(structured_items),
            "source_counts": source_counts,
            "items": structured_items,
            "path": "fallback" if source_counts.get("fallback") else "primary",
        }
        if step_error:
            checks_meta["error_type"] = "checks_step_failed"
            checks_meta["error_message"] = step_error

        state.metadata["checks"] = checks_meta

        details: dict[str, Any] = {
            "checks_count": len(structured_items),
            "source_counts": source_counts,
            "path": checks_meta["path"],
        }
        if step_error:
            details["error"] = step_error

        _append_trace(state, step=self.name, status=status, details=details)
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

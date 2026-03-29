from __future__ import annotations

from typing import Any

from .interfaces import AnalysisGenerator, KnowledgeRetriever, WorkflowStep
from .models import IncidentEvent, WorkflowState
from .rule_engine import RuleBasedAnalyzer


def _normalize_path_decision(
    *,
    action: str = "primary",
    from_mode: str | None = None,
    to_mode: str | None = None,
    reason: str | None = None,
    after_retry: bool = False,
) -> dict[str, Any]:
    return {
        "action": action,
        "from": from_mode,
        "to": to_mode,
        "reason": reason,
        "after_retry": bool(after_retry),
    }


def _append_trace(
    state: WorkflowState,
    *,
    step: str,
    status: str = "ok",
    path_decision: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    normalized_decision = path_decision or _normalize_path_decision()
    trace_entry = {
        "step": step,
        "status": status,
        "path_decision": normalized_decision,
        "degraded": status != "ok" or normalized_decision.get("action") == "fallback",
        "details": details or {},
    }
    if error_type:
        trace_entry["error_type"] = error_type
    if error_message:
        trace_entry["error_message"] = error_message
    state.step_trace.append(trace_entry)


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

        decision = retriever_meta.get("path_decision")
        if not isinstance(decision, dict):
            decision = _normalize_path_decision(
                action=retrieve_summary["path"],
                from_mode=retrieve_summary["source"],
                to_mode=retrieve_summary["source"],
            )

        _append_trace(
            state,
            step=self.name,
            status=status,
            path_decision=decision,
            details=details,
            error_type=retriever_meta.get("error_type"),
            error_message=step_error,
        )
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
        state.structured_checks = [
            str(item.get("title", "")).strip() for item in structured_items if str(item.get("title", "")).strip()
        ]

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

        decision = _normalize_path_decision(
            action=checks_meta["path"],
            from_mode="structured_mixed",
            to_mode="structured_mixed",
            reason=checks_meta.get("error_type"),
            after_retry=False,
        )

        _append_trace(
            state,
            step=self.name,
            status=status,
            path_decision=decision,
            details=details,
            error_type=checks_meta.get("error_type"),
            error_message=step_error,
        )
        return state


class BuildFinalAnalysisStep:
    name = "final_analysis"

    def __init__(
        self,
        generator: AnalysisGenerator,
        fallback_analyzer: AnalysisGenerator | None = None,
    ) -> None:
        self._generator = generator
        self._fallback_analyzer = fallback_analyzer or RuleBasedAnalyzer()

    def _generator_mode(self) -> str:
        last_meta = getattr(self._generator, "last_metadata", {})
        if isinstance(last_meta, dict) and isinstance(last_meta.get("mode"), str):
            return str(last_meta["mode"])
        name = self._generator.__class__.__name__.lower()
        if "llm" in name:
            return "llm"
        if "rule" in name:
            return "rule"
        return "unknown"

    def execute(self, state: WorkflowState) -> WorkflowState:
        retrieve_meta = state.metadata.get("retrieve", {})
        checks_meta = state.metadata.get("checks", {})

        context_for_final = state.context_text
        checks_block_injected = bool(state.structured_checks)
        if checks_block_injected:
            checks_text = "\n".join(f"- {x}" for x in state.structured_checks)
            context_for_final = f"{state.context_text}\n\n[structured_checks]\n{checks_text}"

        consumed_inputs = {
            "retrieve": {
                "context_len": int(len(state.context_text)),
                "refs_count": len(state.reference_paths),
                "path": retrieve_meta.get("path", "unknown"),
                "source": retrieve_meta.get("source", "unknown"),
            },
            "checks": {
                "count": len(state.structured_checks),
                "path": checks_meta.get("path", "unknown"),
                "source_counts": checks_meta.get("source_counts", {}),
                "checks_block_injected": checks_block_injected,
            },
        }

        status = "ok"
        final_path = "primary"
        final_error: str | None = None

        try:
            result = self._generator.generate(state.event, context_for_final)
            generator_meta = getattr(self._generator, "last_metadata", {})
        except Exception as exc:  # pragma: no cover - defensive fallback for custom generators
            status = "degraded"
            final_path = "fallback"
            final_error = str(exc)
            result = self._fallback_analyzer.generate(state.event, context_for_final)
            fallback_from = self._generator_mode()
            generator_meta = {
                "mode": fallback_from,
                "llm_configured": None,
                "llm_called": None,
                "llm_used": False,
                "fallback": True,
                "fallback_from": fallback_from,
                "fallback_to": "rule",
                "fallback_reason": f"final_synthesis_failed:{exc.__class__.__name__}",
                "fallback_after_retry": False,
                "error_type": "final_synthesis_failed",
                "error_message": str(exc),
                "retry_count": 0,
                "retried": False,
                "path_decision": {
                    "action": "fallback",
                    "from": fallback_from,
                    "to": "rule",
                    "reason": f"final_synthesis_failed:{exc.__class__.__name__}",
                    "after_retry": False,
                },
            }

        decision = generator_meta.get("path_decision")
        if not isinstance(decision, dict):
            decision = _normalize_path_decision(
                action="fallback" if generator_meta.get("fallback") else "primary",
                from_mode=generator_meta.get("mode"),
                to_mode=generator_meta.get("fallback_to") or generator_meta.get("mode"),
                reason=generator_meta.get("fallback_reason"),
                after_retry=bool(generator_meta.get("fallback_after_retry", False)),
            )

        if status == "ok" and decision.get("action") == "fallback":
            status = "degraded"
        final_path = str(decision.get("action") or final_path)

        state.final_result = result
        state.metadata["generator"] = generator_meta

        final_meta = {
            "path": final_path,
            "consumed_inputs": consumed_inputs,
            "output_sources": {
                "summary": "generator" if final_path == "primary" else "fallback_rule",
                "possible_causes": "generator" if final_path == "primary" else "fallback_rule",
                "suggested_checks": "generator" if final_path == "primary" else "fallback_rule",
                "recommended_refs": "retrieve" if state.reference_paths else "generator",
                "workflow_aggregated": {
                    "structured_checks_in_context": checks_block_injected,
                    "retrieve_refs_forced_in_pipeline_output": bool(state.reference_paths),
                },
            },
        }
        if final_error:
            final_meta["error_type"] = "final_synthesis_failed"
            final_meta["error_message"] = final_error
        state.metadata["final_analysis"] = final_meta

        trace_details = {
            "path": final_path,
            "confidence": result.confidence,
            "possible_causes_count": len(result.possible_causes),
            "suggested_checks_count": len(result.suggested_checks),
            "consumed_retrieve_refs": consumed_inputs["retrieve"]["refs_count"],
            "consumed_structured_checks": consumed_inputs["checks"]["count"],
        }
        if final_error:
            trace_details["error"] = final_error

        _append_trace(
            state,
            step=self.name,
            status=status,
            path_decision=decision,
            details=trace_details,
            error_type=final_meta.get("error_type"),
            error_message=final_error,
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
        _append_trace(
            state,
            step="incident",
            path_decision=_normalize_path_decision(action="primary", from_mode="incident", to_mode="incident"),
            details={"event_type": event.event_type},
        )

        for step in self.steps:
            state = step.execute(state)

        steps = [trace["step"] for trace in state.step_trace]
        degraded_steps = [trace["step"] for trace in state.step_trace if trace.get("status") != "ok"]
        fallback_steps = [
            trace["step"]
            for trace in state.step_trace
            if trace.get("path_decision", {}).get("action") == "fallback"
        ]
        continue_steps = [
            trace["step"]
            for trace in state.step_trace
            if trace.get("path_decision", {}).get("action") == "continue"
        ]
        final_path = str(state.step_trace[-1].get("path_decision", {}).get("action", "unknown"))
        step_status_map = {trace["step"]: trace.get("status", "unknown") for trace in state.step_trace}
        step_decision_map = {
            trace["step"]: trace.get("path_decision", {}).get("action", "primary")
            for trace in state.step_trace
        }

        state.metadata["workflow"] = {
            "step_path": "incident -> retrieve -> checks -> final_analysis",
            "steps": steps,
            "overview": {
                "total_steps": len(state.step_trace),
                "degraded_step_count": len(degraded_steps),
                "fallback_step_count": len(fallback_steps),
                "continue_step_count": len(continue_steps),
                "final_path": final_path,
                "degraded": bool(degraded_steps),
                "step_status": step_status_map,
                "step_path_decisions": step_decision_map,
            },
        }
        return state

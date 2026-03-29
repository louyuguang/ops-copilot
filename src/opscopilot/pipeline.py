from __future__ import annotations

import time
import uuid
from typing import Any

from .interfaces import AnalysisGenerator, KnowledgeRetriever
from .models import AnalysisResult, IncidentEvent
from .workflow import IncidentWorkflowRunner


_MODEL_COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    # Week 7 Day 3 MVP: lightweight, configurable later.
    # Unknown models will gracefully fallback to zero-cost estimate with explicit basis.
    "gpt-5.4": {"input": 0.0, "output": 0.0},
}


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


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_token_usage(generator_meta: dict[str, Any]) -> dict[str, Any]:
    usage = generator_meta.get("usage")
    if not isinstance(usage, dict):
        if generator_meta.get("llm_called"):
            return {
                "available": False,
                "reason": "provider_usage_missing",
                "source": "llm_provider",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        if generator_meta.get("mode") == "llm":
            return {
                "available": False,
                "reason": "llm_not_called",
                "source": "llm_provider",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        return {
            "available": False,
            "reason": "llm_not_used",
            "source": "not_applicable",
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    prompt_tokens = _to_int_or_none(usage.get("prompt_tokens"))
    if prompt_tokens is None:
        prompt_tokens = _to_int_or_none(usage.get("input_tokens"))

    completion_tokens = _to_int_or_none(usage.get("completion_tokens"))
    if completion_tokens is None:
        completion_tokens = _to_int_or_none(usage.get("output_tokens"))

    total_tokens = _to_int_or_none(usage.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return {
            "available": False,
            "reason": "provider_usage_unparseable",
            "source": "llm_provider",
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    return {
        "available": True,
        "reason": None,
        "source": "llm_provider",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _estimate_cost(generator_meta: dict[str, Any], token_usage: dict[str, Any]) -> dict[str, Any]:
    model = str(generator_meta.get("model") or "unknown")
    if not token_usage.get("available"):
        return {
            "available": False,
            "currency": "USD",
            "input_cost": None,
            "output_cost": None,
            "total_cost": None,
            "estimate_basis": f"unavailable:{token_usage.get('reason')}",
            "model": model,
        }

    pricing = _MODEL_COST_PER_1K_TOKENS.get(model)
    prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
    completion_tokens = int(token_usage.get("completion_tokens") or 0)

    if pricing is None:
        return {
            "available": True,
            "currency": "USD",
            "input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
            "estimate_basis": "usage_available_but_model_pricing_unknown_zero_fallback",
            "model": model,
        }

    input_cost = round(prompt_tokens / 1000 * pricing["input"], 6)
    output_cost = round(completion_tokens / 1000 * pricing["output"], 6)
    return {
        "available": True,
        "currency": "USD",
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": round(input_cost + output_cost, 6),
        "estimate_basis": "model_pricing_table_per_1k_tokens",
        "model": model,
    }


def _build_error_summary(
    workflow_trace: list[dict[str, Any]],
    retriever_meta: dict[str, Any],
    checks_meta: dict[str, Any],
    generator_meta: dict[str, Any],
    final_meta: dict[str, Any],
) -> dict[str, Any]:
    non_error_types = {"retrieval_empty"}

    error_entries: list[tuple[str | None, str]] = []
    for entry in workflow_trace:
        error_type = entry.get("error_type")
        if isinstance(error_type, str) and error_type and error_type not in non_error_types:
            error_entries.append((entry.get("step"), error_type))

    fallback_candidates = [
        ("retrieve", retriever_meta.get("error_type")),
        ("checks", checks_meta.get("error_type")),
        ("final_analysis", final_meta.get("error_type")),
        ("final_analysis", generator_meta.get("error_type")),
    ]
    for step, error_type in fallback_candidates:
        if isinstance(error_type, str) and error_type and error_type not in non_error_types:
            if (step, error_type) not in error_entries:
                error_entries.append((step, error_type))

    if not error_entries:
        return {
            "had_error": False,
            "error_count": 0,
            "primary_error_type": None,
            "primary_error_step": None,
        }

    primary_step, primary_type = error_entries[0]
    return {
        "had_error": True,
        "error_count": len(error_entries),
        "primary_error_type": primary_type,
        "primary_error_step": primary_step,
    }


def _build_degraded_reason(run_status: str, workflow_trace: list[dict[str, Any]]) -> dict[str, Any]:
    if run_status == "success":
        return {
            "degraded": False,
            "type": "clean_run",
            "step": None,
            "reason": None,
        }

    preferred_actions = ["fallback", "continue"]
    selected_entry: dict[str, Any] | None = None
    selected_action: str | None = None

    for action in preferred_actions:
        for entry in workflow_trace:
            path_decision = entry.get("path_decision")
            if isinstance(path_decision, dict) and path_decision.get("action") == action:
                selected_entry = entry
                selected_action = action
                break
        if selected_entry is not None:
            break

    if selected_entry is None:
        for entry in workflow_trace:
            if entry.get("status") != "ok":
                selected_entry = entry
                selected_action = "degraded"
                break

    if selected_entry is None:
        return {
            "degraded": True,
            "type": "unknown",
            "step": None,
            "reason": run_status,
        }

    decision = selected_entry.get("path_decision", {}) if isinstance(selected_entry, dict) else {}
    reason = None
    if isinstance(decision, dict):
        reason = decision.get("reason")
    if not reason:
        reason = selected_entry.get("error_type")

    return {
        "degraded": True,
        "type": selected_action,
        "step": selected_entry.get("step"),
        "reason": reason,
    }


class IncidentAnalysisPipeline:
    """Composable pipeline with clear hooks for future RAG + LLM integration."""

    def __init__(self, retriever: KnowledgeRetriever, generator: AnalysisGenerator) -> None:
        self.retriever = retriever
        self.generator = generator
        self.workflow = IncidentWorkflowRunner(retriever, generator)
        self.last_run_metadata: dict[str, Any] = {}

    def run(self, event: IncidentEvent) -> AnalysisResult:
        request_id = uuid.uuid4().hex[:16]
        t0 = time.monotonic()

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

        total_duration_ms = round((time.monotonic() - t0) * 1000, 2)
        token_usage = _extract_token_usage(generator_meta)
        cost_estimate = _estimate_cost(generator_meta, token_usage)
        error_summary = _build_error_summary(
            workflow_state.step_trace,
            retriever_meta,
            checks_meta,
            generator_meta,
            final_meta,
        )
        degraded_reason = _build_degraded_reason(run_status, workflow_state.step_trace)

        self.last_run_metadata = {
            "request_id": request_id,
            "event_type": event.event_type,
            "total_duration_ms": total_duration_ms,
            "token_usage_available": bool(token_usage.get("available")),
            "token_usage": token_usage,
            "cost_estimate": cost_estimate,
            "run_status": run_status,
            "error_summary": error_summary,
            "degraded_reason": degraded_reason,
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

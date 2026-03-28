from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opscopilot.io import load_event
from opscopilot.knowledge import ChromaCardRetriever, LocalCardRetriever
from opscopilot.llm_engine import LLMAnalyzer
from opscopilot.models import IncidentEvent
from opscopilot.pipeline import IncidentAnalysisPipeline
from opscopilot.rule_engine import RuleBasedAnalyzer

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports" / "eval"
DEFAULT_OUTPUT = REPORTS_DIR / "scenario-matrix-latest.json"


class AlwaysTransientFailClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        _ = (system_prompt, user_prompt)
        self.calls += 1
        from opscopilot.errors import LLMCallError

        raise LLMCallError("timeout while connecting upstream")


class AlwaysDownChromaAPI:
    def query(self, query_embedding: list[float], n_results: int) -> dict[str, Any]:
        _ = (query_embedding, n_results)
        from opscopilot.errors import ExternalDependencyError

        raise ExternalDependencyError("chroma_request_failed:connection_refused")


def get_git_commit_hash() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BASE_DIR,
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _pick_error_type(run_meta: dict[str, Any]) -> str | None:
    retriever_error = run_meta.get("retriever", {}).get("error_type")
    generator_error = run_meta.get("generator", {}).get("error_type")
    return generator_error or retriever_error


def _pick_path_decision(run_meta: dict[str, Any]) -> dict[str, Any]:
    decisions = run_meta.get("decisions", {})
    retriever_decision = decisions.get("retriever", {})
    generator_decision = decisions.get("generator", {})

    if generator_decision.get("action") != "primary":
        return {"focus": "generator", **generator_decision}
    if retriever_decision.get("action") != "primary":
        return {"focus": "retriever", **retriever_decision}
    return {"focus": "generator", **generator_decision}


def _extract_case_fields(case_name: str, run_meta: dict[str, Any], notes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "case": case_name,
        "run_status": run_meta.get("run_status"),
        "had_fallback": bool(run_meta.get("had_fallback")),
        "fallback_count": int(run_meta.get("fallback_count") or 0),
        "had_retry": bool(run_meta.get("had_retry")),
        "total_retry_count": int(run_meta.get("total_retry_count") or 0),
        "primary_path": run_meta.get("primary_path"),
        "effective_path": run_meta.get("effective_path"),
        "error_type": _pick_error_type(run_meta),
        "path_decision": _pick_path_decision(run_meta),
        "decisions": run_meta.get("decisions", {}),
        "notes": notes or {},
    }


def _run_pipeline(event: IncidentEvent, retriever, generator) -> dict[str, Any]:
    pipeline = IncidentAnalysisPipeline(retriever=retriever, generator=generator)
    _ = pipeline.run(event)
    return pipeline.last_run_metadata


def run_scenario_matrix() -> dict[str, Any]:
    cards_dir = BASE_DIR / "docs" / "cards"
    event = load_event(BASE_DIR / "samples" / "incidents" / "high_cpu.json")

    # case 1: llm_key_missing
    llm_key_missing_meta = _run_pipeline(
        event=event,
        retriever=LocalCardRetriever(cards_dir),
        generator=LLMAnalyzer(client=None),
    )

    # case 2: llm_call_failed_after_retry
    retry_fail_client = AlwaysTransientFailClient()
    llm_call_failed_after_retry_meta = _run_pipeline(
        event=event,
        retriever=LocalCardRetriever(cards_dir),
        generator=LLMAnalyzer(client=retry_fail_client, max_retries=1),
    )

    # case 3: chroma_down
    chroma_down_meta = _run_pipeline(
        event=event,
        retriever=ChromaCardRetriever(
            api=AlwaysDownChromaAPI(),
            fallback=LocalCardRetriever(cards_dir),
            max_retries=1,
        ),
        generator=RuleBasedAnalyzer(),
    )

    # case 4: retrieval_empty
    empty_event_raw = dict(event.raw)
    empty_event_raw["event_type"] = "event_type_not_exists_for_matrix"
    retrieval_empty_event = IncidentEvent.from_dict(empty_event_raw)
    retrieval_empty_meta = _run_pipeline(
        event=retrieval_empty_event,
        retriever=LocalCardRetriever(cards_dir),
        generator=RuleBasedAnalyzer(),
    )

    now_utc = datetime.now(timezone.utc)
    report = {
        "meta": {
            "suite": "scenario_matrix_regression",
            "version": "week5-day5-v1",
            "run_timestamp": now_utc.isoformat(),
            "run_epoch": int(now_utc.timestamp()),
            "git_commit": get_git_commit_hash(),
        },
        "config": {
            "cases": [
                "llm_key_missing",
                "llm_call_failed_after_retry",
                "chroma_down",
                "retrieval_empty",
            ]
        },
        "cases": {
            "llm_key_missing": _extract_case_fields(
                "llm_key_missing",
                llm_key_missing_meta,
            ),
            "llm_call_failed_after_retry": _extract_case_fields(
                "llm_call_failed_after_retry",
                llm_call_failed_after_retry_meta,
                notes={"llm_client_calls": retry_fail_client.calls},
            ),
            "chroma_down": _extract_case_fields("chroma_down", chroma_down_meta),
            "retrieval_empty": _extract_case_fields("retrieval_empty", retrieval_empty_meta),
        },
    }

    report["summary"] = {
        "case_count": len(report["cases"]),
        "run_status": {
            name: payload.get("run_status") for name, payload in report["cases"].items()
        },
        "fallback_cases": sorted(
            [name for name, payload in report["cases"].items() if payload.get("had_fallback")]
        ),
        "retry_cases": sorted(
            [name for name, payload in report["cases"].items() if payload.get("had_retry")]
        ),
    }

    return report


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lightweight scenario-matrix regression suite")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON artifact path (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_scenario_matrix()
    write_report(report, args.output_json)

    print("--- SCENARIO_MATRIX_REPORT ---")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[artifact] {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
DEFAULT_BASELINE = REPORTS_DIR / "scenario-matrix-baseline.json"
DEFAULT_DIFF = REPORTS_DIR / "scenario-matrix-diff.json"
COMPARE_FIELDS = [
    "run_status",
    "had_fallback",
    "fallback_count",
    "had_retry",
    "total_retry_count",
    "primary_path",
    "effective_path",
    "path_decision",
    "error_type",
]


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
            "version": "week5-day7-v1",
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


def _json_equivalent(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False
    )


def _parse_allow_field_change(raw_items: list[str]) -> set[tuple[str, str]]:
    parsed: set[tuple[str, str]] = set()
    for raw in raw_items:
        text = (raw or "").strip()
        if not text:
            continue
        if ":" not in text:
            raise ValueError(f"Invalid allow-field-change {raw!r}, expected format: case:field")
        case_name, field_name = text.split(":", 1)
        case_name = case_name.strip()
        field_name = field_name.strip()
        if not case_name or not field_name:
            raise ValueError(f"Invalid allow-field-change {raw!r}, expected format: case:field")
        parsed.add((case_name, field_name))
    return parsed


def compare_with_baseline(
    latest_report: dict[str, Any],
    baseline_report: dict[str, Any],
    warn_threshold: int | None,
    fail_on_warn: bool,
    allow_field_changes: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    latest_cases = latest_report.get("cases", {})
    baseline_cases = baseline_report.get("cases", {})
    case_names = sorted(set(latest_cases.keys()) | set(baseline_cases.keys()))
    allow_field_changes = allow_field_changes or set()

    warnings: list[dict[str, Any]] = []
    allowed_changes: list[dict[str, Any]] = []
    per_case: dict[str, Any] = {}

    for case_name in case_names:
        latest_case = latest_cases.get(case_name)
        baseline_case = baseline_cases.get(case_name)

        if latest_case is None:
            warning = {
                "type": "case_missing_in_latest",
                "case": case_name,
            }
            warnings.append(warning)
            per_case[case_name] = {
                "case": case_name,
                "status": "missing_in_latest",
                "field_diffs": [],
                "allowed_field_diffs": [],
            }
            continue

        if baseline_case is None:
            warning = {
                "type": "case_missing_in_baseline",
                "case": case_name,
            }
            warnings.append(warning)
            per_case[case_name] = {
                "case": case_name,
                "status": "missing_in_baseline",
                "field_diffs": [],
                "allowed_field_diffs": [],
            }
            continue

        field_diffs: list[dict[str, Any]] = []
        allowed_field_diffs: list[dict[str, Any]] = []
        for field_name in COMPARE_FIELDS:
            latest_value = latest_case.get(field_name)
            baseline_value = baseline_case.get(field_name)
            if _json_equivalent(latest_value, baseline_value):
                continue

            diff_item = {
                "field": field_name,
                "latest": latest_value,
                "baseline": baseline_value,
            }
            if (case_name, field_name) in allow_field_changes:
                allowed_item = {
                    "type": "allowed_field_changed",
                    "case": case_name,
                    **diff_item,
                }
                allowed_field_diffs.append(diff_item)
                allowed_changes.append(allowed_item)
                continue

            field_diffs.append(diff_item)
            warnings.append(
                {
                    "type": "field_changed",
                    "case": case_name,
                    **diff_item,
                }
            )

        status = "same"
        if field_diffs:
            status = "changed"
        elif allowed_field_diffs:
            status = "changed_allowed"

        per_case[case_name] = {
            "case": case_name,
            "status": status,
            "field_diffs": field_diffs,
            "allowed_field_diffs": allowed_field_diffs,
        }

    warning_count = len(warnings)
    warn_triggered = warning_count > (warn_threshold or 0)
    should_fail = fail_on_warn and warn_triggered

    diff_report = {
        "meta": {
            "suite": "scenario_matrix_diff",
            "version": "week5-day7-v1",
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "run_epoch": int(datetime.now(timezone.utc).timestamp()),
            "git_commit": get_git_commit_hash(),
        },
        "compare_fields": COMPARE_FIELDS,
        "allow_field_changes": [
            {"case": case_name, "field": field_name}
            for case_name, field_name in sorted(allow_field_changes)
        ],
        "summary": {
            "latest_case_count": len(latest_cases),
            "baseline_case_count": len(baseline_cases),
            "compared_case_count": len(case_names),
            "changed_case_count": sum(1 for item in per_case.values() if item["status"] == "changed"),
            "changed_allowed_case_count": sum(1 for item in per_case.values() if item["status"] == "changed_allowed"),
            "warning_count": warning_count,
            "allowed_change_count": len(allowed_changes),
        },
        "warnings": warnings,
        "allowed_changes": allowed_changes,
        "cases": per_case,
        "gate": {
            "warn_threshold": warn_threshold,
            "fail_on_warn": fail_on_warn,
            "warn_triggered": warn_triggered,
            "should_fail": should_fail,
            "exit_code": 2 if should_fail else 0,
        },
    }
    return diff_report


def _build_human_summary(diff_report: dict[str, Any]) -> str:
    lines = ["--- SCENARIO_MATRIX_DIFF_SUMMARY ---"]
    summary = diff_report.get("summary", {})
    gate = diff_report.get("gate", {})
    warnings = diff_report.get("warnings", [])
    allowed_changes = diff_report.get("allowed_changes", [])

    lines.append(
        "cases(latest/baseline/compared): "
        f"{summary.get('latest_case_count', 0)}/"
        f"{summary.get('baseline_case_count', 0)}/"
        f"{summary.get('compared_case_count', 0)}"
    )
    lines.append(
        "changed: "
        f"{summary.get('changed_case_count', 0)} "
        f"(allowed={summary.get('changed_allowed_case_count', 0)}, "
        f"allowed_changes={summary.get('allowed_change_count', 0)})"
    )

    if warnings:
        lines.append(f"warnings ({len(warnings)}):")
        for item in warnings:
            if item.get("type") in {"case_missing_in_latest", "case_missing_in_baseline"}:
                lines.append(f"  - {item.get('type')}: case={item.get('case')}")
                continue
            lines.append(
                "  - field_changed: "
                f"case={item.get('case')} field={item.get('field')} "
                f"baseline={json.dumps(item.get('baseline'), ensure_ascii=False)} "
                f"latest={json.dumps(item.get('latest'), ensure_ascii=False)}"
            )
    else:
        lines.append("warnings: none")

    if allowed_changes:
        lines.append(f"allowed_changes ({len(allowed_changes)}):")
        for item in allowed_changes:
            lines.append(
                "  - field_changed_allowed: "
                f"case={item.get('case')} field={item.get('field')}"
            )

    lines.append(
        "gate: "
        f"warn_threshold={gate.get('warn_threshold')} "
        f"warn_triggered={gate.get('warn_triggered')} "
        f"fail_on_warn={gate.get('fail_on_warn')} "
        f"should_fail={gate.get('should_fail')} "
        f"exit_code={gate.get('exit_code')}"
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lightweight scenario-matrix regression suite")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON artifact path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        default=None,
        help=f"Optional baseline JSON path for compare (suggested: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--diff-json",
        type=Path,
        default=DEFAULT_DIFF,
        help=f"Diff JSON artifact path when --baseline-json is provided (default: {DEFAULT_DIFF})",
    )
    parser.add_argument(
        "--warn-threshold",
        type=int,
        default=0,
        help="Warning threshold for diff warning_count (default: 0)",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Return non-zero exit code when warning_count is greater than threshold",
    )
    parser.add_argument(
        "--allow-field-change",
        action="append",
        default=[],
        help="Allow a known field change without gate warning, format: case:field (repeatable)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.warn_threshold is not None and args.warn_threshold < 0:
        print("[error] --warn-threshold must be >= 0", file=sys.stderr)
        return 1

    try:
        allow_field_changes = _parse_allow_field_change(args.allow_field_change)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    report = run_scenario_matrix()
    write_report(report, args.output_json)

    print("--- SCENARIO_MATRIX_REPORT ---")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[artifact] latest: {args.output_json}")

    if not args.baseline_json:
        return 0

    if not args.baseline_json.exists():
        print(f"[error] baseline json not found: {args.baseline_json}", file=sys.stderr)
        return 1

    baseline_report = read_json(args.baseline_json)
    diff_report = compare_with_baseline(
        latest_report=report,
        baseline_report=baseline_report,
        warn_threshold=args.warn_threshold,
        fail_on_warn=args.fail_on_warn,
        allow_field_changes=allow_field_changes,
    )
    write_report(diff_report, args.diff_json)

    print("\n--- SCENARIO_MATRIX_DIFF ---")
    print(json.dumps(diff_report, ensure_ascii=False, indent=2))
    print(f"\n[artifact] baseline: {args.baseline_json}")
    print(f"[artifact] diff: {args.diff_json}")
    print()
    print(_build_human_summary(diff_report))

    return int(diff_report.get("gate", {}).get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())

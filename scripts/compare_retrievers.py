from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_MAIN = BASE_DIR / "src" / "main.py"
DEFAULT_SAMPLES = [
    "high_cpu",
    "high_memory",
    "mysql_too_many_connections",
]
TREND_KEYS = [
    "recommended_refs_diff_count",
    "possible_causes_diff_count",
    "suggested_checks_diff_count",
    "summary_equal_true_count",
    "summary_equal_false_count",
]


def parse_csv_values(raw: str | None, cast=str) -> list:
    if not raw:
        return []
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return [cast(item) for item in items]


def parse_top_k_values(raw: str | None) -> list[int]:
    values = parse_csv_values(raw, int)
    if any(v <= 0 for v in values):
        raise ValueError("top_k values must be positive integers")
    return values


def run_case(
    sample: str,
    retriever: str,
    chroma_top_k: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    env["OPSCOPILOT_DEBUG"] = "1"

    if chroma_top_k is not None:
        env["CHROMA_TOP_K"] = str(chroma_top_k)
    if extra_env:
        env.update(extra_env)

    cmd = [
        sys.executable,
        str(SRC_MAIN),
        "--event",
        str(BASE_DIR / "samples" / "incidents" / f"{sample}.json"),
        "--mode",
        "rule",
        "--retriever",
        retriever,
    ]
    if chroma_top_k is not None:
        cmd.extend(["--chroma-top-k", str(chroma_top_k)])

    proc = subprocess.run(cmd, cwd=BASE_DIR, env=env, text=True, capture_output=True, check=True)
    output = json.loads(proc.stdout)
    debug = json.loads(proc.stderr.strip().splitlines()[-1])
    return {
        "summary": output.get("summary"),
        "possible_causes": output.get("possible_causes", []),
        "suggested_checks": output.get("suggested_checks", []),
        "recommended_refs": output.get("recommended_refs", []),
        "retriever_metadata": debug.get("pipeline", {}).get("retriever", {}),
    }


def _norm_list(items: list[str]) -> list[str]:
    return [str(x).strip() for x in items if str(x).strip()]


def diff_lists(local: list[str], chroma: list[str]) -> dict[str, list[str] | bool]:
    left = _norm_list(local)
    right = _norm_list(chroma)
    left_set = set(left)
    right_set = set(right)
    return {
        "same": left == right,
        "local_only": sorted(left_set - right_set),
        "chroma_only": sorted(right_set - left_set),
    }


def build_case_diff(base: dict, candidate: dict) -> dict[str, bool]:
    return {
        "summary_equal": base.get("summary") == candidate.get("summary"),
        "recommended_refs_diff": not diff_lists(
            base.get("recommended_refs", []),
            candidate.get("recommended_refs", []),
        )["same"],
        "possible_causes_diff": not diff_lists(
            base.get("possible_causes", []),
            candidate.get("possible_causes", []),
        )["same"],
        "suggested_checks_diff": not diff_lists(
            base.get("suggested_checks", []),
            candidate.get("suggested_checks", []),
        )["same"],
    }


def summarize_diffs(case_diffs: list[dict[str, bool]]) -> dict[str, int]:
    summary_equal_true = sum(1 for item in case_diffs if item["summary_equal"])
    total = len(case_diffs)
    return {
        "sample_count": total,
        "recommended_refs_diff_count": sum(1 for item in case_diffs if item["recommended_refs_diff"]),
        "possible_causes_diff_count": sum(1 for item in case_diffs if item["possible_causes_diff"]),
        "suggested_checks_diff_count": sum(1 for item in case_diffs if item["suggested_checks_diff"]),
        "summary_equal_true_count": summary_equal_true,
        "summary_equal_false_count": total - summary_equal_true,
    }


def render_list_diff(title: str, local: list[str], chroma: list[str]) -> str:
    diff = diff_lists(local, chroma)
    lines = [f"  - {title}: {'SAME' if diff['same'] else 'DIFF'}"]
    if diff["local_only"]:
        lines.append("    local_only:")
        lines.extend(f"      - {item}" for item in diff["local_only"])
    if diff["chroma_only"]:
        lines.append("    chroma_only:")
        lines.extend(f"      - {item}" for item in diff["chroma_only"])
    if not diff["local_only"] and not diff["chroma_only"]:
        lines.append("    (no item-level diff)")
    return "\n".join(lines)


def render_metadata(local_meta: dict, chroma_meta: dict) -> str:
    keys = ["query_len", "retrieved_context_len", "top_k", "returned_count", "fallback"]
    lines = ["  - metadata:"]
    for key in keys:
        lines.append(
            f"    {key}: local={local_meta.get(key)!r}, chroma={chroma_meta.get(key)!r}"
        )
    return "\n".join(lines)


def render_case(sample: str, local: dict, chroma: dict) -> str:
    blocks = [f"=== {sample} ==="]
    blocks.append(f"  summary_equal: {local.get('summary') == chroma.get('summary')}")
    blocks.append(
        render_list_diff(
            "recommended_refs",
            local.get("recommended_refs", []),
            chroma.get("recommended_refs", []),
        )
    )
    blocks.append(
        render_list_diff(
            "possible_causes",
            local.get("possible_causes", []),
            chroma.get("possible_causes", []),
        )
    )
    blocks.append(
        render_list_diff(
            "suggested_checks",
            local.get("suggested_checks", []),
            chroma.get("suggested_checks", []),
        )
    )
    blocks.append(render_metadata(local.get("retriever_metadata", {}), chroma.get("retriever_metadata", {})))
    return "\n".join(blocks)


def render_summary(summary: dict[str, int]) -> str:
    return "\n".join(
        [
            "--- DIFF_SUMMARY ---",
            f"samples: {summary['sample_count']}",
            f"recommended_refs_diff: {summary['recommended_refs_diff_count']}",
            f"possible_causes_diff: {summary['possible_causes_diff_count']}",
            f"suggested_checks_diff: {summary['suggested_checks_diff_count']}",
            f"summary_equal_true: {summary['summary_equal_true_count']}",
            f"summary_equal_false: {summary['summary_equal_false_count']}",
        ]
    )


def build_variants(args: argparse.Namespace) -> list[dict]:
    variants: list[dict] = [{"name": "local", "retriever": "local"}]

    if args.top_k_values:
        for top_k in parse_top_k_values(args.top_k_values):
            variants.append(
                {
                    "name": f"chroma_top_k_{top_k}",
                    "retriever": "chroma",
                    "chroma_top_k": top_k,
                }
            )
    else:
        variants.append({"name": "chroma", "retriever": "chroma"})

    if args.simulate_chroma_down:
        variants.append(
            {
                "name": "chroma_fallback",
                "retriever": "chroma",
                "extra_env": {"CHROMA_PORT": "1"},
            }
        )

    return variants


def load_json_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_trend_vs_baseline(report: dict[str, Any], baseline_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not baseline_report:
        return []

    baseline_map = {
        item.get("name"): item.get("summary", {})
        for item in baseline_report.get("comparisons", [])
        if isinstance(item, dict)
    }
    trend_rows: list[dict[str, Any]] = []

    for comparison in report.get("comparisons", []):
        name = comparison.get("name")
        current_summary = comparison.get("summary", {})
        baseline_summary = baseline_map.get(name)
        if baseline_summary is None:
            trend_rows.append({"name": name, "status": "baseline_missing"})
            continue

        deltas = {
            key: int(current_summary.get(key, 0)) - int(baseline_summary.get(key, 0))
            for key in TREND_KEYS
        }
        trend_rows.append(
            {
                "name": name,
                "status": "ok",
                "current": {key: int(current_summary.get(key, 0)) for key in TREND_KEYS},
                "baseline": {key: int(baseline_summary.get(key, 0)) for key in TREND_KEYS},
                "delta": deltas,
            }
        )

    return trend_rows


def render_trend_summary(trend_rows: list[dict[str, Any]]) -> str:
    if not trend_rows:
        return ""

    lines = ["--- BASELINE_TREND ---"]
    for row in trend_rows:
        lines.append(f"comparison: {row['name']}")
        if row.get("status") != "ok":
            lines.append("  baseline: missing")
            continue
        for key in TREND_KEYS:
            delta = row["delta"][key]
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"  {key}: current={row['current'][key]}, baseline={row['baseline'][key]}, delta={sign}{delta}"
            )
    return "\n".join(lines)


def build_warnings(comparisons: list[dict[str, Any]], warn_threshold: int | None) -> list[dict[str, Any]]:
    if warn_threshold is None:
        return []

    warning_keys = [
        "recommended_refs_diff_count",
        "possible_causes_diff_count",
        "suggested_checks_diff_count",
    ]
    warnings: list[dict[str, Any]] = []
    for comp in comparisons:
        summary = comp.get("summary", {})
        exceeded = {
            key: int(summary.get(key, 0))
            for key in warning_keys
            if int(summary.get(key, 0)) > warn_threshold
        }
        if exceeded:
            warnings.append(
                {
                    "comparison": comp.get("name"),
                    "threshold": warn_threshold,
                    "exceeded": exceeded,
                }
            )
    return warnings


def render_warnings(warnings: list[dict[str, Any]]) -> str:
    if not warnings:
        return ""

    lines = ["--- WARNINGS ---"]
    for warning in warnings:
        lines.append(
            f"WARNING comparison={warning['comparison']} threshold={warning['threshold']} exceeded={warning['exceeded']}"
        )
    return "\n".join(lines)


def write_json_report(report: dict[str, Any], output_json: str | None) -> None:
    if not output_json:
        return
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local/chroma retrievers")
    parser.add_argument(
        "--samples",
        default=",".join(DEFAULT_SAMPLES),
        help="Comma-separated sample incident names",
    )
    parser.add_argument(
        "--top-k-values",
        default=None,
        help="Comma-separated top_k values for chroma matrix compare, e.g. 1,3,5",
    )
    parser.add_argument(
        "--simulate-chroma-down",
        action="store_true",
        help="Add an explicit chroma unavailable scenario to show fallback visibility",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Write JSON report artifact to file path",
    )
    parser.add_argument(
        "--baseline-json",
        default=None,
        help="Previous JSON report path used for trend comparison",
    )
    parser.add_argument(
        "--warn-threshold",
        type=int,
        default=None,
        help="Warn when diff counts are greater than this threshold",
    )
    args = parser.parse_args()

    if args.warn_threshold is not None and args.warn_threshold < 0:
        raise ValueError("warn threshold must be >= 0")

    samples = parse_csv_values(args.samples)
    variants = build_variants(args)
    report: dict[str, Any] = {
        "config": {
            "samples": samples,
            "top_k_values": parse_top_k_values(args.top_k_values),
            "simulate_chroma_down": args.simulate_chroma_down,
            "output_json": args.output_json,
            "baseline_json": args.baseline_json,
            "warn_threshold": args.warn_threshold,
        },
        "runs": {},
        "comparisons": [],
        "trend_vs_baseline": [],
        "warnings": [],
    }
    lines: list[str] = []

    for sample in samples:
        sample_runs: dict[str, dict] = {}
        for variant in variants:
            sample_runs[variant["name"]] = run_case(
                sample=sample,
                retriever=variant["retriever"],
                chroma_top_k=variant.get("chroma_top_k"),
                extra_env=variant.get("extra_env"),
            )
        report["runs"][sample] = sample_runs

    for variant in variants[1:]:
        pair_name = f"local_vs_{variant['name']}"
        lines.append(f"\n##### {pair_name} #####")

        case_diffs: list[dict[str, bool]] = []
        case_reports: dict[str, dict] = {}
        for sample in samples:
            local_result = report["runs"][sample]["local"]
            candidate_result = report["runs"][sample][variant["name"]]
            lines.append(render_case(sample, local_result, candidate_result))

            case_diff = build_case_diff(local_result, candidate_result)
            case_diffs.append(case_diff)
            case_reports[sample] = case_diff

        summary = summarize_diffs(case_diffs)
        lines.append(render_summary(summary))
        report["comparisons"].append(
            {
                "name": pair_name,
                "base": "local",
                "candidate": variant["name"],
                "summary": summary,
                "case_diffs": case_reports,
            }
        )

    baseline_report = load_json_file(args.baseline_json)
    trend_rows = build_trend_vs_baseline(report, baseline_report)
    report["trend_vs_baseline"] = trend_rows

    warnings = build_warnings(report["comparisons"], args.warn_threshold)
    report["warnings"] = warnings

    trend_text = render_trend_summary(trend_rows)
    if trend_text:
        lines.append(trend_text)

    warning_text = render_warnings(warnings)
    if warning_text:
        lines.append(warning_text)

    print("\n\n".join(lines))
    print("\n--- JSON_REPORT ---")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    write_json_report(report, args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

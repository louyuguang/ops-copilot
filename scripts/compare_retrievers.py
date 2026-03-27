from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_MAIN = BASE_DIR / "src" / "main.py"
SAMPLES = [
    "high_cpu",
    "high_memory",
    "mysql_too_many_connections",
]


def run_case(sample: str, retriever: str) -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    env["OPSCOPILOT_DEBUG"] = "1"

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
    blocks.append(render_list_diff("recommended_refs", local.get("recommended_refs", []), chroma.get("recommended_refs", [])))
    blocks.append(render_list_diff("possible_causes", local.get("possible_causes", []), chroma.get("possible_causes", [])))
    blocks.append(render_list_diff("suggested_checks", local.get("suggested_checks", []), chroma.get("suggested_checks", [])))
    blocks.append(render_metadata(local.get("retriever_metadata", {}), chroma.get("retriever_metadata", {})))
    return "\n".join(blocks)


def main() -> int:
    report: dict[str, dict[str, dict]] = {}
    lines: list[str] = []

    for sample in SAMPLES:
        local_result = run_case(sample, "local")
        chroma_result = run_case(sample, "chroma")
        report[sample] = {"local": local_result, "chroma": chroma_result}
        lines.append(render_case(sample, local_result, chroma_result))

    print("\n\n".join(lines))
    print("\n--- JSON_REPORT ---")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

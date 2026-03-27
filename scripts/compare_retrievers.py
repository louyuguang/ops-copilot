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
        "recommended_refs": output.get("recommended_refs", []),
        "retriever_metadata": debug.get("pipeline", {}).get("retriever", {}),
    }


def main() -> int:
    report: dict[str, dict[str, dict]] = {}
    for sample in SAMPLES:
        report[sample] = {
            "local": run_case(sample, "local"),
            "chroma": run_case(sample, "chroma"),
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

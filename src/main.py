from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from opscopilot import (
    ChromaCardRetriever,
    ChromaSettings,
    IncidentAnalysisPipeline,
    LLMAnalyzer,
    LocalCardRetriever,
    RuleBasedAnalyzer,
    load_event,
    result_to_dict,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_DIR = BASE_DIR / "docs" / "cards"
SAMPLES_DIR = BASE_DIR / "samples" / "incidents"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpsCopilot MVP incident analyzer")
    parser.add_argument(
        "--event",
        type=Path,
        default=SAMPLES_DIR / "high_cpu.json",
        help="Path to incident event JSON",
    )
    parser.add_argument(
        "--mode",
        choices=["rule", "llm"],
        default=os.getenv("ANALYSIS_MODE", "rule"),
        help="Analysis mode: rule (baseline) or llm",
    )
    parser.add_argument(
        "--retriever",
        choices=["local", "chroma"],
        default=os.getenv("RETRIEVER_MODE", "local"),
        help="Retriever mode: local (default) or chroma",
    )
    return parser.parse_args()


def build_generator(mode: str):
    if mode == "llm":
        return LLMAnalyzer.from_env()
    return RuleBasedAnalyzer()


def build_retriever(mode: str):
    local = LocalCardRetriever(CARDS_DIR)
    if mode == "chroma":
        return ChromaCardRetriever(settings=ChromaSettings.from_env(), fallback=local)
    return local


def _debug_enabled() -> bool:
    return os.getenv("OPSCOPILOT_DEBUG", "0").strip() in {"1", "true", "TRUE", "yes", "on"}


def main() -> int:
    if _debug_enabled():
        logging.basicConfig(level=logging.INFO)

    args = parse_args()
    event = load_event(args.event)

    pipeline = IncidentAnalysisPipeline(
        retriever=build_retriever(args.retriever),
        generator=build_generator(args.mode),
    )
    result = pipeline.run(event)

    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))

    if _debug_enabled():
        debug_meta = {
            "mode": args.mode,
            "retriever": args.retriever,
            "llm_requested": args.mode == "llm",
            "pipeline": pipeline.last_run_metadata,
        }
        print(json.dumps(debug_meta, ensure_ascii=False), file=os.sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

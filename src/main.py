from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from opscopilot import (
    ChromaCardRetriever,
    ChromaSettings,
    IncidentAnalysisPipeline,
    LLMAnalyzer,
    LocalCardRetriever,
    OpsCopilotError,
    RuleBasedAnalyzer,
    load_event,
    result_to_dict,
)
from opscopilot.config import ConfigError, resolve_runtime_config

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
        default=None,
        help="Analysis mode. Priority: CLI > env ANALYSIS_MODE > default(rule)",
    )
    parser.add_argument(
        "--retriever",
        choices=["local", "chroma"],
        default=None,
        help="Retriever mode. Priority: CLI > env RETRIEVER_MODE > default(local)",
    )
    parser.add_argument(
        "--chroma-top-k",
        type=int,
        default=None,
        help="Chroma Top-K. Priority: CLI > env CHROMA_TOP_K > default(3)",
    )
    return parser.parse_args()


def build_generator(mode: str, *, runtime_config) -> RuleBasedAnalyzer | LLMAnalyzer:
    if mode == "llm":
        return LLMAnalyzer.from_runtime_config(runtime_config)
    return RuleBasedAnalyzer()


def build_retriever(mode: str, chroma_top_k: int, *, runtime_config):
    local = LocalCardRetriever(CARDS_DIR)
    if mode == "chroma":
        return ChromaCardRetriever(
            settings=ChromaSettings.from_runtime_config(runtime_config),
            top_k=chroma_top_k,
            fallback=local,
            max_retries=runtime_config.chroma_max_retries,
        )
    return local


def _debug_enabled() -> bool:
    return os.getenv("OPSCOPILOT_DEBUG", "0").strip() in {"1", "true", "TRUE", "yes", "on"}


def main() -> int:
    if _debug_enabled():
        logging.basicConfig(level=logging.INFO)

    args = parse_args()
    try:
        runtime_config = resolve_runtime_config(
            cli_analysis_mode=args.mode,
            cli_retriever_mode=args.retriever,
            cli_chroma_top_k=args.chroma_top_k,
            env=os.environ,
        )
    except ConfigError as exc:
        print(f"[config_error] {exc}", file=sys.stderr)
        return 2

    for warning in runtime_config.warnings:
        logging.warning("[config_warning] %s", warning)

    try:
        event = load_event(args.event)
        pipeline = IncidentAnalysisPipeline(
            retriever=build_retriever(
                runtime_config.retriever_mode,
                runtime_config.chroma_top_k,
                runtime_config=runtime_config,
            ),
            generator=build_generator(runtime_config.analysis_mode, runtime_config=runtime_config),
        )
        result = pipeline.run(event)
    except ConfigError as exc:
        print(f"[config_error] {exc}", file=sys.stderr)
        return 2
    except OpsCopilotError as exc:
        print(f"[{exc.error_type}] {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[runtime_error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))

    if _debug_enabled():
        debug_meta = {
            "mode": runtime_config.analysis_mode,
            "retriever": runtime_config.retriever_mode,
            "chroma_top_k": runtime_config.chroma_top_k,
            "llm_requested": runtime_config.analysis_mode == "llm",
            "llm_timeout_seconds": runtime_config.llm_timeout_seconds,
            "chroma_timeout_seconds": runtime_config.chroma_timeout_seconds,
            "llm_max_retries": runtime_config.llm_max_retries,
            "chroma_max_retries": runtime_config.chroma_max_retries,
            "pipeline": pipeline.last_run_metadata,
            "run_status": pipeline.last_run_metadata.get("run_status", "unknown"),
            "config_warnings": list(runtime_config.warnings),
        }
        print(json.dumps(debug_meta, ensure_ascii=False), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

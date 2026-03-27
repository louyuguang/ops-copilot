from __future__ import annotations

import json
from pathlib import Path

from .models import AnalysisResult, IncidentEvent


def load_event(path: Path) -> IncidentEvent:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return IncidentEvent.from_dict(payload)


def result_to_dict(result: AnalysisResult) -> dict:
    return {
        "summary": result.summary,
        "possible_causes": result.possible_causes,
        "suggested_checks": result.suggested_checks,
        "recommended_refs": result.recommended_refs,
        "confidence": result.confidence,
    }

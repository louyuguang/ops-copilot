from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REQUIRED_EVENT_FIELDS = {
    "id",
    "source",
    "service",
    "environment",
    "event_type",
    "severity",
    "title",
    "description",
    "timestamp",
}

SEVERITY_VALUES = {"info", "warning", "critical"}


@dataclass(frozen=True)
class IncidentEvent:
    id: str
    source: str
    service: str
    environment: str
    event_type: str
    severity: str
    title: str
    description: str
    timestamp: str
    symptoms: list[str]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IncidentEvent":
        missing = sorted(REQUIRED_EVENT_FIELDS - set(data.keys()))
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        severity = str(data.get("severity", "")).lower()
        if severity not in SEVERITY_VALUES:
            raise ValueError(
                f"Invalid severity '{data.get('severity')}', allowed: {sorted(SEVERITY_VALUES)}"
            )

        symptoms = data.get("symptoms") or []
        if not isinstance(symptoms, list):
            raise ValueError("symptoms must be a list of strings")

        return cls(
            id=str(data["id"]),
            source=str(data["source"]),
            service=str(data["service"]),
            environment=str(data["environment"]),
            event_type=str(data["event_type"]),
            severity=severity,
            title=str(data["title"]),
            description=str(data["description"]),
            timestamp=str(data["timestamp"]),
            symptoms=[str(x) for x in symptoms],
            raw=data,
        )


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    possible_causes: list[str]
    suggested_checks: list[str]
    recommended_refs: list[str]
    confidence: str


@dataclass
class WorkflowState:
    event: IncidentEvent
    context_text: str = ""
    reference_paths: list[str] = field(default_factory=list)
    structured_checks: list[str] = field(default_factory=list)
    structured_check_items: list[dict[str, Any]] = field(default_factory=list)
    final_result: AnalysisResult | None = None
    step_trace: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

from __future__ import annotations

from typing import Protocol

from .models import IncidentEvent, AnalysisResult, WorkflowState


class KnowledgeRetriever(Protocol):
    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        """Return context text and its reference paths."""


class AnalysisGenerator(Protocol):
    def generate(self, event: IncidentEvent, context: str) -> AnalysisResult:
        """Generate analysis result from event + context."""


class WorkflowStep(Protocol):
    name: str

    def execute(self, state: WorkflowState) -> WorkflowState:
        """Execute a workflow step and return updated state."""

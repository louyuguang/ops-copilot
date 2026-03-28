from __future__ import annotations


class OpsCopilotError(RuntimeError):
    """Base typed error for runtime semantics."""

    error_type = "runtime_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ExternalDependencyError(OpsCopilotError):
    """External dependency is unavailable or failed unexpectedly."""

    error_type = "external_dependency_error"


class RetrievalEmptyError(OpsCopilotError):
    """Retriever completed successfully but returned no useful context."""

    error_type = "retrieval_empty"


class LLMCallError(OpsCopilotError):
    """LLM API call failed (network/http/server)."""

    error_type = "llm_call_failed"


class OutputParseError(OpsCopilotError):
    """Structured output parsing/validation failed."""

    error_type = "output_parse_failed"

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import RuntimeConfig
from .errors import LLMCallError, OutputParseError
from .models import AnalysisResult, IncidentEvent
from .rule_engine import RuleBasedAnalyzer


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    model: str = "gpt-5.4"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "OpenAISettings | None":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        model = os.getenv("OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4"
        base_url = (
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
            or "https://api.openai.com/v1"
        )
        return cls(api_key=api_key, model=model, base_url=base_url)

    @classmethod
    def from_runtime_config(cls, config: RuntimeConfig) -> "OpenAISettings | None":
        if not config.openai_api_key:
            return None
        return cls(
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
        )


class PromptTemplateStore:
    """Load prompt templates from files, with safe defaults."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir = prompts_dir or Path(__file__).resolve().parent / "prompts"

    def load(self, name: str, default: str) -> str:
        path = self.prompts_dir / name
        if not path.exists():
            return default
        return path.read_text(encoding="utf-8").strip() or default


class OpenAIChatClient:
    """Tiny OpenAI-compatible chat client using stdlib only."""

    def __init__(self, settings: OpenAISettings) -> None:
        self.settings = settings

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.settings.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except error.URLError as exc:
            raise LLMCallError(f"LLM request failed: {exc}") from exc

        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OutputParseError("LLM returned non-JSON content") from exc


class LLMAnalyzer:
    """LLM-first analyzer with deterministic rule fallback.

    Input basis: incident + knowledge card + rule baseline result.
    Output schema remains AnalysisResult.
    """

    def __init__(
        self,
        client: OpenAIChatClient | None = None,
        fallback_analyzer: RuleBasedAnalyzer | None = None,
        prompt_store: PromptTemplateStore | None = None,
    ) -> None:
        self.client = client
        self.fallback_analyzer = fallback_analyzer or RuleBasedAnalyzer()
        self.prompt_store = prompt_store or PromptTemplateStore()
        self.last_metadata: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> "LLMAnalyzer":
        settings = OpenAISettings.from_env()
        return cls(client=OpenAIChatClient(settings) if settings else None)

    @classmethod
    def from_runtime_config(cls, config: RuntimeConfig) -> "LLMAnalyzer":
        settings = OpenAISettings.from_runtime_config(config)
        return cls(client=OpenAIChatClient(settings) if settings else None)

    def generate(self, event: IncidentEvent, context: str) -> AnalysisResult:
        rule_result = self.fallback_analyzer.generate(event, context)

        metadata: dict[str, Any] = {
            "mode": "llm",
            "llm_configured": self.client is not None,
            "llm_called": False,
            "llm_used": False,
            "fallback": False,
            "fallback_reason": None,
            "error_type": None,
            "error_message": None,
        }

        if self.client is None:
            metadata["fallback"] = True
            metadata["fallback_reason"] = "llm_api_key_missing"
            metadata["error_type"] = "config_error"
            metadata["error_message"] = "OPENAI_API_KEY is missing"
            self.last_metadata = metadata
            logger.info("LLM skipped, using rule fallback: %s", metadata)
            return rule_result

        system_prompt = self.prompt_store.load(
            "llm_system.txt",
            "你是运维故障分析助手。请基于输入给出结构化 JSON，且只输出 JSON，不要额外解释。",
        )
        task = self.prompt_store.load(
            "llm_task.txt",
            "在不改变输出字段的前提下，补充更贴近当前事件的排查建议",
        )
        user_prompt = self._build_user_prompt(event, context, rule_result, task)
        metadata["input_layers"] = ["incident", "retrieved_context", "rule_result"]

        try:
            metadata["llm_called"] = True
            llm_data = self.client.complete_json(system_prompt, user_prompt)
            result = self._merge_with_fallback(llm_data, rule_result)
            metadata["llm_used"] = True
            self.last_metadata = metadata
            logger.info("LLM analyze success: %s", metadata)
            return result
        except OutputParseError as exc:
            metadata["fallback"] = True
            metadata["fallback_reason"] = "llm_output_parse_failed"
            metadata["error_type"] = exc.error_type
            metadata["error_message"] = str(exc)
            self.last_metadata = metadata
            logger.warning("LLM output parse failed, using fallback: %s", metadata)
            return rule_result
        except LLMCallError as exc:
            metadata["fallback"] = True
            metadata["fallback_reason"] = "llm_call_failed"
            metadata["error_type"] = exc.error_type
            metadata["error_message"] = str(exc)
            self.last_metadata = metadata
            logger.warning("LLM call failed, using fallback: %s", metadata)
            return rule_result
        except Exception as exc:
            metadata["fallback"] = True
            metadata["fallback_reason"] = f"llm_unexpected_error:{exc.__class__.__name__}"
            metadata["error_type"] = "llm_unexpected_error"
            metadata["error_message"] = str(exc)
            self.last_metadata = metadata
            logger.warning("LLM analyze failed, using fallback: %s", metadata)
            return rule_result

    def _build_user_prompt(
        self,
        event: IncidentEvent,
        context: str,
        rule_result: AnalysisResult,
        task: str,
    ) -> str:
        return json.dumps(
            {
                "task": task,
                "input_layers": {
                    "incident": event.raw,
                    "retrieved_context": {
                        "raw_text": context,
                        "present": bool(context.strip()),
                    },
                    "rule_result": {
                        "summary": rule_result.summary,
                        "possible_causes": rule_result.possible_causes,
                        "suggested_checks": rule_result.suggested_checks,
                        "recommended_refs": rule_result.recommended_refs,
                        "confidence": rule_result.confidence,
                    },
                },
                "instructions": [
                    "必须区分 incident、retrieved_context、rule_result 三层信息。",
                    "若 retrieved_context 为空，不要编造资料来源。",
                    "输出必须严格遵守 output_schema。",
                ],
                "output_schema": {
                    "summary": "string",
                    "possible_causes": ["string"],
                    "suggested_checks": ["string"],
                    "recommended_refs": ["string"],
                    "confidence": "low|medium|high",
                },
            },
            ensure_ascii=False,
        )

    def _merge_with_fallback(
        self,
        llm_data: dict[str, Any],
        fallback: AnalysisResult,
    ) -> AnalysisResult:
        if not isinstance(llm_data, dict):
            raise OutputParseError("LLM JSON payload must be an object")

        summary = str(llm_data.get("summary") or fallback.summary)
        causes = self._clean_list(llm_data.get("possible_causes"), fallback.possible_causes)
        checks = self._clean_list(llm_data.get("suggested_checks"), fallback.suggested_checks)
        refs = self._clean_list(llm_data.get("recommended_refs"), fallback.recommended_refs)

        confidence = str(llm_data.get("confidence") or fallback.confidence).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = fallback.confidence

        return AnalysisResult(
            summary=summary,
            possible_causes=causes,
            suggested_checks=checks,
            recommended_refs=refs,
            confidence=confidence,
        )

    def _clean_list(self, value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            return fallback
        items = [str(x).strip() for x in value if str(x).strip()]
        return items or fallback

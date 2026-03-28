from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class ConfigError(ValueError):
    """Raised when configuration value exists but is invalid."""


@dataclass(frozen=True)
class RuntimeConfig:
    analysis_mode: str
    retriever_mode: str
    chroma_top_k: int
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    llm_timeout_seconds: int
    chroma_timeout_seconds: int
    llm_max_retries: int
    chroma_max_retries: int
    warnings: tuple[str, ...] = ()


def _pick(cli_value: str | int | None, env: Mapping[str, str], env_name: str, default: str) -> str:
    if cli_value is not None:
        return str(cli_value).strip()
    raw = env.get(env_name, "").strip()
    return raw or default


def resolve_choice(
    *,
    cli_value: str | None,
    env: Mapping[str, str],
    env_name: str,
    default: str,
    allowed: set[str],
) -> str:
    value = _pick(cli_value, env, env_name, default)
    if value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ConfigError(f"Invalid {env_name}={value!r}. Allowed values: {allowed_text}.")
    return value


def resolve_positive_int(
    *,
    cli_value: int | None,
    env: Mapping[str, str],
    env_name: str,
    default: int,
) -> int:
    raw = _pick(cli_value, env, env_name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Invalid {env_name}={raw!r}. It must be a positive integer (e.g. {default})."
        ) from exc
    if value <= 0:
        raise ConfigError(
            f"Invalid {env_name}={raw!r}. It must be > 0 (e.g. {default})."
        )
    return value


def resolve_non_negative_int(
    *,
    cli_value: int | None,
    env: Mapping[str, str],
    env_name: str,
    default: int,
) -> int:
    raw = _pick(cli_value, env, env_name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Invalid {env_name}={raw!r}. It must be a non-negative integer (e.g. {default})."
        ) from exc
    if value < 0:
        raise ConfigError(
            f"Invalid {env_name}={raw!r}. It must be >= 0 (e.g. {default})."
        )
    return value


def resolve_runtime_config(
    *,
    cli_analysis_mode: str | None,
    cli_retriever_mode: str | None,
    cli_chroma_top_k: int | None,
    env: Mapping[str, str],
) -> RuntimeConfig:
    analysis_mode = resolve_choice(
        cli_value=cli_analysis_mode,
        env=env,
        env_name="ANALYSIS_MODE",
        default="rule",
        allowed={"rule", "llm"},
    )
    retriever_mode = resolve_choice(
        cli_value=cli_retriever_mode,
        env=env,
        env_name="RETRIEVER_MODE",
        default="local",
        allowed={"local", "chroma"},
    )
    chroma_top_k = resolve_positive_int(
        cli_value=cli_chroma_top_k,
        env=env,
        env_name="CHROMA_TOP_K",
        default=3,
    )

    openai_api_key = env.get("OPENAI_API_KEY", "").strip()
    openai_model = env.get("OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    openai_base_url = (
        env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        or "https://api.openai.com/v1"
    )

    llm_timeout_seconds = resolve_positive_int(
        cli_value=None,
        env=env,
        env_name="LLM_TIMEOUT_SECONDS",
        default=20,
    )
    chroma_timeout_seconds = resolve_positive_int(
        cli_value=None,
        env=env,
        env_name="CHROMA_TIMEOUT_SECONDS",
        default=5,
    )
    llm_max_retries = resolve_non_negative_int(
        cli_value=None,
        env=env,
        env_name="LLM_MAX_RETRIES",
        default=1,
    )
    chroma_max_retries = resolve_non_negative_int(
        cli_value=None,
        env=env,
        env_name="CHROMA_MAX_RETRIES",
        default=1,
    )

    warnings: list[str] = []
    if analysis_mode == "llm" and not openai_api_key:
        warnings.append(
            "ANALYSIS_MODE=llm but OPENAI_API_KEY is missing. "
            "LLM analyzer will fallback to rule-based output."
        )

    return RuntimeConfig(
        analysis_mode=analysis_mode,
        retriever_mode=retriever_mode,
        chroma_top_k=chroma_top_k,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        llm_timeout_seconds=llm_timeout_seconds,
        chroma_timeout_seconds=chroma_timeout_seconds,
        llm_max_retries=llm_max_retries,
        chroma_max_retries=chroma_max_retries,
        warnings=tuple(warnings),
    )

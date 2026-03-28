from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import ConfigError, RuntimeConfig, resolve_positive_int
from .errors import ExternalDependencyError
from .models import IncidentEvent


class LocalCardRetriever:
    """Minimal local retriever (future: replace with vector DB / RAG retriever)."""

    def __init__(self, cards_dir: Path) -> None:
        self.cards_dir = cards_dir
        self.last_metadata: dict[str, Any] = {}

    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        card_path = self.cards_dir / f"{event.event_type}.md"
        query = build_incident_query(event)
        query_summary = summarize_query(query)
        if not card_path.exists():
            self.last_metadata = {
                "mode": "local",
                "event_type": event.event_type,
                "query": query,
                "query_summary": query_summary,
                "query_len": len(query),
                "top_k": 1,
                "retrieved_context_len": 0,
                "matched_cards": [],
                "returned_count": 0,
                "fallback": False,
                "fallback_from": None,
                "fallback_to": None,
                "fallback_reason": None,
                "fallback_after_retry": False,
                "retrieval_status": "empty",
                "error_type": "retrieval_empty",
                "card_found": False,
                "card_path": str(card_path),
                "retry_count": 0,
                "retried": False,
                "max_retries": 0,
                "path_decision": {
                    "action": "continue",
                    "from": "local",
                    "to": "continue",
                    "reason": "retrieval_empty",
                    "after_retry": False,
                },
            }
            return "", []

        context = card_path.read_text(encoding="utf-8")
        self.last_metadata = {
            "mode": "local",
            "event_type": event.event_type,
            "query": query,
            "query_summary": query_summary,
            "query_len": len(query),
            "top_k": 1,
            "retrieved_context_len": len(context),
            "matched_cards": [f"docs/cards/{event.event_type}.md"],
            "returned_count": 1,
            "fallback": False,
            "fallback_from": None,
            "fallback_to": None,
            "fallback_reason": None,
            "fallback_after_retry": False,
            "retrieval_status": "ok",
            "error_type": None,
            "card_found": True,
            "card_path": str(card_path),
            "retry_count": 0,
            "retried": False,
            "max_retries": 0,
            "path_decision": {
                "action": "primary",
                "from": "local",
                "to": "local",
                "reason": None,
                "after_retry": False,
            },
        }
        return context, [f"docs/cards/{event.event_type}.md"]


def build_incident_query(event: IncidentEvent) -> str:
    """Build richer retriever query from multiple incident fields."""
    parts = [
        event.event_type,
        event.title,
        event.description,
        event.service,
        event.environment,
        " ".join(event.symptoms),
    ]
    return "\n".join(p.strip() for p in parts if p and p.strip())


def summarize_query(query: str, max_len: int = 180) -> str:
    compact = " ".join(query.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[:max_len].rstrip()}..."


class SimpleHashEmbedder:
    """Very small deterministic embedder (no external dependency)."""

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = re.findall(r"[a-zA-Z0-9_\-\.]+", text.lower())
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], byteorder="big", signed=False) % self.dim
            vec[idx] += 1.0

        norm = sum(x * x for x in vec) ** 0.5
        if norm == 0:
            return vec
        return [x / norm for x in vec]


@dataclass(frozen=True)
class ChromaSettings:
    host: str = "localhost"
    port: int = 18000
    tenant: str = "default_tenant"
    database: str = "default_database"
    collection: str = "opscopilot_cards_v1"
    timeout_seconds: int = 5
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> "ChromaSettings":
        port_raw = os.getenv("CHROMA_PORT", "18000").strip() or "18000"
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid CHROMA_PORT={port_raw!r}. It must be an integer, e.g. 18000."
            ) from exc
        if port <= 0:
            raise ConfigError(
                f"Invalid CHROMA_PORT={port_raw!r}. It must be > 0, e.g. 18000."
            )

        timeout_seconds = resolve_positive_int(
            cli_value=None,
            env=os.environ,
            env_name="CHROMA_TIMEOUT_SECONDS",
            default=5,
        )
        max_retries_raw = os.getenv("CHROMA_MAX_RETRIES", "1").strip() or "1"
        try:
            max_retries = int(max_retries_raw)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid CHROMA_MAX_RETRIES={max_retries_raw!r}. It must be >= 0, e.g. 1."
            ) from exc
        if max_retries < 0:
            raise ConfigError(
                f"Invalid CHROMA_MAX_RETRIES={max_retries_raw!r}. It must be >= 0, e.g. 1."
            )

        return cls(
            host=os.getenv("CHROMA_HOST", "localhost").strip() or "localhost",
            port=port,
            tenant=os.getenv("CHROMA_TENANT", "default_tenant").strip() or "default_tenant",
            database=(
                os.getenv("CHROMA_DATABASE", "default_database").strip() or "default_database"
            ),
            collection=os.getenv("CHROMA_COLLECTION", "opscopilot_cards_v1").strip()
            or "opscopilot_cards_v1",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    @classmethod
    def from_runtime_config(cls, config: RuntimeConfig) -> "ChromaSettings":
        base = cls.from_env()
        return cls(
            host=base.host,
            port=base.port,
            tenant=base.tenant,
            database=base.database,
            collection=base.collection,
            timeout_seconds=config.chroma_timeout_seconds,
            max_retries=config.chroma_max_retries,
        )


class ChromaHttpAPI:
    def __init__(self, settings: ChromaSettings) -> None:
        self.settings = settings
        self.base = (
            f"http://{settings.host}:{settings.port}/api/v2/tenants/{settings.tenant}"
            f"/databases/{settings.database}"
        )
        self._collection_id: str | None = None

    def ensure_collection_id(self) -> str:
        if self._collection_id:
            return self._collection_id
        payload = {"name": self.settings.collection, "get_or_create": True}
        data = self._request_json("POST", f"{self.base}/collections", payload)
        cid = data.get("id")
        if not cid:
            raise RuntimeError("chroma_create_collection_missing_id")
        self._collection_id = str(cid)
        return self._collection_id

    def query(
        self,
        query_embedding: list[float],
        n_results: int,
    ) -> dict[str, Any]:
        cid = self.ensure_collection_id()
        payload = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        return self._request_json("POST", f"{self.base}/collections/{cid}/query", payload)

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        cid = self.ensure_collection_id()
        payload = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "embeddings": embeddings,
        }
        _ = self._request_json("POST", f"{self.base}/collections/{cid}/upsert", payload)

    def _request_json(self, method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise ExternalDependencyError(f"chroma_request_failed:{exc.code}:{detail}") from exc
        except error.URLError as exc:
            raise ExternalDependencyError(f"chroma_request_failed:{exc}") from exc


class ChromaCardRetriever:
    """Minimal Chroma retriever for docs/cards/*.md (1 card = 1 document)."""

    def __init__(
        self,
        settings: ChromaSettings | None = None,
        top_k: int | None = None,
        fallback: LocalCardRetriever | None = None,
        api: ChromaHttpAPI | None = None,
        embedder: SimpleHashEmbedder | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.settings = settings or ChromaSettings.from_env()
        self.top_k = top_k if top_k is not None else _read_top_k_from_env()
        self.fallback = fallback
        self.api = api or ChromaHttpAPI(self.settings)
        self.embedder = embedder or SimpleHashEmbedder()
        self.last_metadata: dict[str, Any] = {}
        self.max_retries = (
            max_retries
            if max_retries is not None
            else max(0, int(getattr(self.settings, "max_retries", 1)))
        )

    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        query = build_incident_query(event)
        query_summary = summarize_query(query)
        emb = self.embedder.embed(query)

        retry_count = 0
        last_exc: Exception | None = None
        raw: dict[str, Any] | None = None
        while retry_count <= self.max_retries:
            try:
                raw = self.api.query(emb, self.top_k)
                break
            except ExternalDependencyError as exc:
                last_exc = exc
                if retry_count >= self.max_retries:
                    break
                retry_count += 1
                continue
            except Exception as exc:
                last_exc = exc
                break

        if raw is not None:
            documents = (raw.get("documents") or [[]])[0] or []
            metadatas = (raw.get("metadatas") or [[]])[0] or []
            ids = (raw.get("ids") or [[]])[0] or []

            refs: list[str] = []
            hits: list[dict[str, Any]] = []
            for idx, doc in enumerate(documents):
                if not str(doc).strip():
                    continue
                meta = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
                ref = str(meta.get("path") or ids[idx] if idx < len(ids) else "")
                if ref:
                    refs.append(ref)
                hits.append(
                    {
                        "id": str(ids[idx]) if idx < len(ids) else "",
                        "path": ref,
                        "event_type": str(meta.get("event_type") or ""),
                    }
                )

            context = "\n\n---\n\n".join(str(x) for x in documents if str(x).strip())
            returned_count = len(hits)
            is_empty = returned_count == 0
            self.last_metadata = {
                "mode": "chroma",
                "query": query,
                "query_summary": query_summary,
                "query_len": len(query),
                "top_k": self.top_k,
                "retrieved_context_len": len(context),
                "matched_cards": hits,
                "returned_count": returned_count,
                "fallback": False,
                "fallback_from": None,
                "fallback_to": None,
                "fallback_reason": None,
                "fallback_after_retry": False,
                "retrieval_status": "empty" if is_empty else "ok",
                "error_type": "retrieval_empty" if is_empty else None,
                "collection": self.settings.collection,
                "endpoint": f"{self.settings.host}:{self.settings.port}",
                "retry_count": retry_count,
                "retried": retry_count > 0,
                "max_retries": self.max_retries,
                "path_decision": {
                    "action": "continue" if is_empty else "primary",
                    "from": "chroma",
                    "to": "continue" if is_empty else "chroma",
                    "reason": "retrieval_empty" if is_empty else None,
                    "after_retry": False,
                },
            }
            return context, refs

        exc = last_exc or RuntimeError("chroma_query_unknown_error")
        error_type = exc.error_type if hasattr(exc, "error_type") else "external_dependency_error"
        self.last_metadata = {
            "mode": "chroma",
            "query": query,
            "query_summary": query_summary,
            "query_len": len(query),
            "top_k": self.top_k,
            "retrieved_context_len": 0,
            "matched_cards": [],
            "returned_count": 0,
            "fallback": True,
            "fallback_from": "chroma",
            "fallback_to": "local" if self.fallback is not None else None,
            "fallback_reason": "chroma_unavailable",
            "fallback_after_retry": retry_count > 0,
            "error_type": error_type,
            "error_message": str(exc),
            "collection": self.settings.collection,
            "endpoint": f"{self.settings.host}:{self.settings.port}",
            "retry_count": retry_count,
            "retried": retry_count > 0,
            "max_retries": self.max_retries,
            "path_decision": {
                "action": "fallback" if self.fallback is not None else "continue",
                "from": "chroma",
                "to": "local" if self.fallback is not None else "continue",
                "reason": "chroma_unavailable",
                "after_retry": retry_count > 0,
            },
        }
        if self.fallback is not None:
            context, refs = self.fallback.fetch(event)
            self.last_metadata["fallback_target"] = "local"
            self.last_metadata["retrieved_context_len"] = len(context)
            self.last_metadata["local"] = getattr(self.fallback, "last_metadata", {})
            return context, refs
        return "", []


def _read_top_k_from_env(default: int = 3) -> int:
    # No CLI override here. This helper is only used when caller chooses env/default path.
    return resolve_positive_int(
        cli_value=None,
        env=os.environ,
        env_name="CHROMA_TOP_K",
        default=default,
    )


def build_cards_index(cards_dir: Path, api: ChromaHttpAPI, embedder: SimpleHashEmbedder) -> int:
    card_paths = sorted(cards_dir.glob("*.md"))
    if not card_paths:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []

    for path in card_paths:
        content = path.read_text(encoding="utf-8")
        ids.append(path.stem)
        documents.append(content)
        metadatas.append({"path": f"docs/cards/{path.name}", "event_type": path.stem})
        embeddings.append(embedder.embed(f"{path.stem}\n{content}"))

    api.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return len(ids)

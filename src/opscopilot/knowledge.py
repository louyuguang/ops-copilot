from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .models import IncidentEvent


class LocalCardRetriever:
    """Minimal local retriever (future: replace with vector DB / RAG retriever)."""

    def __init__(self, cards_dir: Path) -> None:
        self.cards_dir = cards_dir
        self.last_metadata: dict[str, Any] = {}

    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        card_path = self.cards_dir / f"{event.event_type}.md"
        if not card_path.exists():
            self.last_metadata = {
                "event_type": event.event_type,
                "card_found": False,
                "card_path": str(card_path),
            }
            return "", []

        self.last_metadata = {
            "event_type": event.event_type,
            "card_found": True,
            "card_path": str(card_path),
        }
        return card_path.read_text(encoding="utf-8"), [f"docs/cards/{event.event_type}.md"]


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

    @classmethod
    def from_env(cls) -> "ChromaSettings":
        return cls(
            host=os.getenv("CHROMA_HOST", "localhost").strip() or "localhost",
            port=int((os.getenv("CHROMA_PORT", "18000").strip() or "18000")),
            tenant=os.getenv("CHROMA_TENANT", "default_tenant").strip() or "default_tenant",
            database=(
                os.getenv("CHROMA_DATABASE", "default_database").strip() or "default_database"
            ),
            collection=os.getenv("CHROMA_COLLECTION", "opscopilot_cards_v1").strip()
            or "opscopilot_cards_v1",
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
            raise RuntimeError(f"chroma_request_failed:{exc.code}:{detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"chroma_request_failed:{exc}") from exc


class ChromaCardRetriever:
    """Minimal Chroma retriever for docs/cards/*.md (1 card = 1 document)."""

    def __init__(
        self,
        settings: ChromaSettings | None = None,
        top_k: int = 3,
        fallback: LocalCardRetriever | None = None,
        api: ChromaHttpAPI | None = None,
        embedder: SimpleHashEmbedder | None = None,
    ) -> None:
        self.settings = settings or ChromaSettings.from_env()
        self.top_k = top_k
        self.fallback = fallback
        self.api = api or ChromaHttpAPI(self.settings)
        self.embedder = embedder or SimpleHashEmbedder()
        self.last_metadata: dict[str, Any] = {}

    def fetch(self, event: IncidentEvent) -> tuple[str, list[str]]:
        query = build_incident_query(event)
        try:
            emb = self.embedder.embed(query)
            raw = self.api.query(emb, self.top_k)
            documents = (raw.get("documents") or [[]])[0] or []
            metadatas = (raw.get("metadatas") or [[]])[0] or []
            ids = (raw.get("ids") or [[]])[0] or []

            refs: list[str] = []
            for idx, doc in enumerate(documents):
                if not str(doc).strip():
                    continue
                meta = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
                ref = str(meta.get("path") or ids[idx] if idx < len(ids) else "")
                if ref:
                    refs.append(ref)

            context = "\n\n---\n\n".join(str(x) for x in documents if str(x).strip())
            self.last_metadata = {
                "mode": "chroma",
                "query": query,
                "query_len": len(query),
                "results": len(refs),
                "fallback": False,
                "collection": self.settings.collection,
                "endpoint": f"{self.settings.host}:{self.settings.port}",
            }
            return context, refs
        except Exception as exc:
            self.last_metadata = {
                "mode": "chroma",
                "query": query,
                "fallback": True,
                "fallback_reason": f"chroma_error:{exc.__class__.__name__}",
                "collection": self.settings.collection,
                "endpoint": f"{self.settings.host}:{self.settings.port}",
            }
            if self.fallback is not None:
                context, refs = self.fallback.fetch(event)
                self.last_metadata["fallback_target"] = "local"
                self.last_metadata["local"] = getattr(self.fallback, "last_metadata", {})
                return context, refs
            return "", []


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

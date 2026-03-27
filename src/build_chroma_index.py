from __future__ import annotations

import json
from pathlib import Path

from opscopilot.knowledge import ChromaHttpAPI, ChromaSettings, SimpleHashEmbedder, build_cards_index


BASE_DIR = Path(__file__).resolve().parent.parent
CARDS_DIR = BASE_DIR / "docs" / "cards"


def main() -> int:
    settings = ChromaSettings.from_env()
    api = ChromaHttpAPI(settings)
    count = build_cards_index(cards_dir=CARDS_DIR, api=api, embedder=SimpleHashEmbedder())
    print(
        json.dumps(
            {
                "indexed_cards": count,
                "collection": settings.collection,
                "chroma": f"{settings.host}:{settings.port}",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

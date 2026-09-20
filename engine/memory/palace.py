"""Long-term memory backed by MemPalace (github.com/MemPalace/mempalace).

Storage : ChromaDB via MemPalace's palace layer
Embeddings: all-MiniLM-L6-v2 (30 MB, runs 100% locally, no Ollama needed)
Search : hybrid BM25 + cosine semantic via MemPalace's _hybrid_rank (96.6% R@5)

Privacy: nothing leaves the machine. MiniLM is downloaded once (~30 MB from
HuggingFace) then cached. All vectors and conversation text stay in
data/mp_palace/ on disk.

Public API (unchanged):
    remember(role, text)        → store a conversation turn verbatim
    recall(query, k=5) -> str   → compact context block of relevant past turns
"""
from __future__ import annotations
import datetime as _dt
import hashlib

import settings

_PALACE_DIR  = str(settings.DATA_DIR / "mp_palace")
_COLLECTION  = "curie_conversations"
_WING        = "conversations"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _uid(role: str, text: str) -> str:
    blob = f"{_now()}-{role}-{text[:60]}"
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


class Memory:
    def __init__(self):
        self._col     = None
        self._backend = "none"
        self._init()

    def _init(self):
        import os
        os.makedirs(_PALACE_DIR, exist_ok=True)
        try:
            from mempalace.palace import get_collection
            self._col     = get_collection(_PALACE_DIR, collection_name=_COLLECTION, create=True)
            self._backend = "mempalace"
            print("[Memory] backend: MemPalace v3.5 (MiniLM hybrid BM25+semantic)")
        except Exception as e:
            print(f"[Memory] MemPalace init failed ({e}); memory disabled.")

    # ── write ─────────────────────────────────────────────────
    def remember(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text or self._col is None:
            return
        try:
            self._col.add(
                documents=[text],
                metadatas=[{
                    "role":        role,
                    "ts":          _now(),
                    "source_file": "curie_voice",
                    "wing":        _WING,
                    "room":        role,
                }],
                ids=[_uid(role, text)],
            )
        except Exception as e:
            print(f"[Memory] remember failed: {e}")

    # ── read ──────────────────────────────────────────────────
    def recall(self, query: str, k: int = 5) -> str:
        if self._col is None or not (query or "").strip():
            return ""
        try:
            total = self._col.count()
            if total == 0:
                return ""

            # Fetch more candidates than we need so BM25 re-ranking has room
            n = min(k * 3, total)
            results = self._col.query(
                query_texts=[query],
                n_results=n,
                include=["documents", "distances", "metadatas"],
            )

            # Unwrap result lists (ChromaDB returns list-of-lists)
            def _first(key):
                outer = results.get(key) or []
                return (outer[0] if outer else []) or []

            docs  = _first("documents")
            dists = _first("distances")

            if not docs:
                return ""

            # Hybrid BM25 + cosine re-rank — MemPalace's own ranker
            from mempalace.searcher import _hybrid_rank
            candidates = [
                {"text": d or "", "distance": float(v)}
                for d, v in zip(docs, dists)
            ]
            _hybrid_rank(candidates, query)

            top = [c["text"] for c in candidates[:k] if c["text"].strip()]
            if not top:
                return ""
            lines = "\n".join(f"- {h}" for h in top)
            return f"Relevant things from past conversations:\n{lines}"

        except Exception as e:
            print(f"[Memory] recall failed: {e}")
            return ""


# standalone test:  python -m engine.memory.palace
if __name__ == "__main__":
    m = Memory()
    m.remember("user",      "I run a coffee shop in Delhi called Bean There.")
    m.remember("user",      "My sister Priya lives in Mumbai.")
    m.remember("assistant", "Got it, I will remember that about your coffee shop.")
    print(m.recall("tell me about my shop"))
    print("---")
    print(m.recall("who is Priya"))

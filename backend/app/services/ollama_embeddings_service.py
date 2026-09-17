# svc: ollama_embeddings | tr: ollama ile metin vektörü (embedding) üret, rag için kullan / en: generate text embeddings via ollama for rag

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha1
from typing import Dict, List, Optional

import httpx

from app.services.ollama_service import ollama_available

_emb_lock = threading.Lock()
# tr: aynı metin tekrar embed edilmesin / en: cache so same text is not re-embedded
_emb_cache: Dict[str, List[float]] = {}


# fn: _base_url | tr: ollama sunucu adresi / en: ollama server url
def _base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


# fn: _embed_model | tr: embedding model adı / en: embedding model name
def _embed_model() -> str:
    return os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text").strip()


# fn: _timeout | tr: istek zaman aşımı / en: request timeout
def _timeout() -> float:
    return float(os.getenv("OLLAMA_EMBED_TIMEOUT", "120"))


# fn: _max_chars_per_chunk | tr: embed edilecek max karakter / en: max chars per embed request
def _max_chars_per_chunk() -> int:
    return int(os.getenv("PDF_EMBED_CHUNK_MAX_CHARS", "12000").strip() or "12000")


# fn: embed_text | tr: tek metin -> vektor / en: single text to embedding vector
def embed_text(text: str) -> Optional[List[float]]:
    if not ollama_available():
        return None
    if not (text or "").strip():
        return None
    t = text.strip()[: _max_chars_per_chunk()]
    key = sha1(t.encode("utf-8", errors="ignore")).hexdigest()
    with _emb_lock:
        cached = _emb_cache.get(key)
        if cached is not None:
            return cached
    base = _base_url()
    model = _embed_model()
    try:
        with httpx.Client(timeout=_timeout()) as client:
            r = client.post(
                f"{base}/api/embeddings",
                json={"model": model, "prompt": t},
            )
            r.raise_for_status()
            data = r.json()
            emb = data.get("embedding")
            if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
                vec = [float(x) for x in emb]
                with _emb_lock:
                    max_cache = int(os.getenv("PDF_EMBED_TEXT_CACHE_SIZE", "3000").strip() or "3000")
                    max_cache = max(300, min(max_cache, 15000))
                    if len(_emb_cache) >= max_cache:
                        # tr: cache dolunca en eskiyi sil / en: drop oldest cache entry when full
                        _emb_cache.pop(next(iter(_emb_cache)))
                    _emb_cache[key] = vec
                return vec
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        pass
    return None


# fn: embed_chunks | tr: chunk listesi -> vektör listesi (paralel) / en: chunk list to vector list (parallel)
def embed_chunks(texts: List[str]) -> List[Optional[List[float]]]:
    if not os.getenv("PDF_EMBED_ENABLED", "true").lower() in ("1", "true", "yes"):
        return [None] * len(texts)

    max_chunks = int(os.getenv("PDF_EMBED_MAX_CHUNKS", "200").strip() or "200")
    max_chunks = max(10, min(max_chunks, 500))
    out: List[Optional[List[float]]] = [None] * len(texts)
    workers = int(os.getenv("PDF_EMBED_WORKERS", "4").strip() or "4")
    workers = max(1, min(workers, 12))
    idxs = [i for i in range(min(len(texts), max_chunks))]
    if workers == 1 or len(idxs) <= 1:
        for i in idxs:
            out[i] = embed_text(texts[i] or "")
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_map = {ex.submit(embed_text, texts[i] or ""): i for i in idxs}
        for fut in as_completed(fut_map):
            i = fut_map[fut]
            try:
                out[i] = fut.result()
            except Exception:
                out[i] = None
    return out


# fn: cosine_similarity | tr: iki vektör arası benzerlik (0-1) / en: similarity between two vectors (0-1)
def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)

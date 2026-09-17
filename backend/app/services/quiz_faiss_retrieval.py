# svc: quiz_faiss_retrieval | tr: quiz için konuya uygun pdf parçası bul (faiss + embedding) / en: find pdf chunks for quiz topic (faiss + embeddings)

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

_log = logging.getLogger(__name__)

_lock = threading.Lock()
# tr: döküman başına chunk metinleri / en: chunk texts per document
_chunk_texts: Dict[str, List[str]] = {}
# tr: döküman başına faiss index / en: faiss index per document
_index_by_doc: Dict[str, object] = {}
_model = None
_model_name: Optional[str] = None


# fn: _embed_model | tr: sentence-transformers model yükle (quiz için) / en: load sentence-transformers model for quiz
def _embed_model():
    global _model, _model_name
    name = (os.getenv("QUIZ_EMBED_MODEL") or "all-MiniLM-L6-v2").strip()
    if _model is not None and _model_name == name:
        return _model
    try:
        from app.services.ml_runtime_env import configure_ml_runtime_env

        configure_ml_runtime_env()
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise RuntimeError("sentence-transformers is required for quiz FAISS retrieval.") from e
    _model = SentenceTransformer(name)
    _model_name = name
    _log.info("quiz FAISS: loaded embedding model %s", name)
    return _model


# fn: drop_document_index | tr: pdf silinince quiz index temizle / en: clear quiz index when pdf removed
def drop_document_index(document_id: str) -> None:
    did = (document_id or "").strip()
    if not did:
        return
    with _lock:
        _chunk_texts.pop(did, None)
        _index_by_doc.pop(did, None)


# fn: ensure_index | tr: chunk'lardan faiss index oluştur / en: build faiss index from chunks
def ensure_index(document_id: str, chunks: List[str]) -> bool:
    did = (document_id or "").strip()
    if not did or not chunks:
        return False
    with _lock:
        if did in _index_by_doc and did in _chunk_texts:
            return True
    try:
        import faiss
    except ImportError as e:
        _log.warning("faiss not available: %s", e)
        return False

    texts = [c.strip() for c in chunks if c and len(c.strip()) > 20]
    if not texts:
        texts = [c.strip() for c in chunks if c.strip()] or [chunks[0][:2000]]

    model = _embed_model()
    emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    if emb.ndim != 2 or emb.shape[0] != len(texts):
        return False
    dim = int(emb.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(np.asarray(emb, dtype=np.float32))

    with _lock:
        _chunk_texts[did] = texts
        _index_by_doc[did] = index
    return True


# fn: retrieve_for_topic | tr: konu etiketine en uygun chunk metnini döndür / en: return chunk text best matching topic label
def retrieve_for_topic(
    document_id: str,
    chunks: List[str],
    topic: str,
    *,
    top_k: int = 4,
    max_chars: int = 2200,
) -> str:
    did = (document_id or "").strip()
    q = (topic or "").strip()
    if not did or not q:
        return ""
    cap = max(400, min(max_chars, 6000))

    # tr: önce paylaşılan document_retrieval (ollama rag) dene / en: try shared document_retrieval (ollama rag) first
    try:
        from app.services.document_store import get_document
        from app.services.document_retrieval import retrieve_top_chunk_texts

        doc = get_document(did)
        if doc is not None and doc.chunks:
            parts = retrieve_top_chunk_texts(doc, q, top_k=top_k)
            if parts:
                picked: List[str] = []
                total = 0
                for block in parts:
                    b = (block or "").strip()
                    if not b:
                        continue
                    take = b[: cap - total]
                    picked.append(take)
                    total += len(take) + 2
                    if total >= cap:
                        break
                if picked:
                    return "\n\n".join(picked).strip()[:cap]
    except Exception:
        _log.debug("shared document RAG failed; falling back to quiz MiniLM index", exc_info=True)

    # tr: yedek: quiz özel miniLM faiss index / en: fallback: quiz-specific miniLM faiss index
    if not ensure_index(did, chunks):
        return ""

    try:
        import faiss
    except ImportError:
        return ""

    with _lock:
        index = _index_by_doc.get(did)
        texts = _chunk_texts.get(did)
    if index is None or not texts:
        return ""

    model = _embed_model()
    qv = model.encode([q], convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
    k = max(1, min(top_k, len(texts)))
    scores, idxs = index.search(qv, k)
    picked2: List[str] = []
    total2 = 0
    for j in idxs[0]:
        if j < 0 or j >= len(texts):
            continue
        block = texts[int(j)].strip()
        if not block:
            continue
        take = block[: cap - total2]
        picked2.append(take)
        total2 += len(take) + 2
        if total2 >= cap:
            break
    _ = scores
    return "\n\n".join(picked2).strip()[:cap]

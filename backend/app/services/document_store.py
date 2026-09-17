# svc: document_store | tr: pdf oturumlarını ram'de tut (chunk + embedding + rag) / en: keep pdf sessions in ram (chunks + embeddings + rag)

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from hashlib import sha1
from typing import Dict, List, Optional, Tuple

from app.services.ollama_embeddings_service import embed_chunks
from app.services.text_chunking import chunk_text_with_topics, is_valid_chunk

try:
    from app.services import quiz_faiss_retrieval as _quiz_faiss_retrieval
except ImportError:
    _quiz_faiss_retrieval = None  # type: ignore


_lock = threading.Lock()
_store: Dict[str, "StoredDocument"] = {}
_content_index: Dict[Tuple[int, str], str] = {}
# tr: ttl/limit ile silinen id'ler (expired vs missing ayirt et) / en: tombstones for expired vs missing ids
_session_ended_ids: Dict[str, float] = {}
_MAX_SESSION_ENDED = int(os.getenv("PDF_SESSION_ENDED_TOMBSTONE_MAX", "3000").strip() or "3000")

# cfg: oturum ttl (varsayilan 7 gun) / en: session ttl default 7 days
TTL_SECONDS = int(os.getenv("PDF_DOCUMENT_TTL_SECONDS", str(7 * 24 * 3600)))


# cls: StoredDocument | tr: ram'deki tek pdf oturumu / en: single pdf session in ram
@dataclass
class StoredDocument:
    document_id: str
    user_id: int
    filename: str
    study_text: str
    chunks: List[str]
    chunk_topics: List[str]
    chunk_embeddings: List[Optional[List[float]]]
    chunk_topic_embeddings: List[Optional[List[float]]]
    content_hash: str
    created_at: float
    quiz_topic_cards: Optional[List[Dict[str, str]]] = None
    rag_faiss_index: Optional[object] = field(default=None, repr=False)
    rag_faiss_chunk_indices: Optional[List[int]] = field(default=None, repr=False)
    rag_index_built: bool = field(default=False, repr=False)


# fn: _remember_session_ended_ids | tr: silinen oturum id'lerini kaydet / en: remember removed session ids
def _remember_session_ended_ids(ids: List[str]) -> None:
    if not ids:
        return
    now = time.time()
    for i in ids:
        s = (i or "").strip()
        if s:
            _session_ended_ids[s] = now
    cap = max(100, min(_MAX_SESSION_ENDED, 50_000))
    while len(_session_ended_ids) > cap:
        oldest = min(_session_ended_ids.items(), key=lambda x: x[1])[0]
        _session_ended_ids.pop(oldest, None)


# fn: _purge_expired_locked | tr: ttl dolmuş oturumları sil / en: remove sessions past ttl
def _purge_expired_locked() -> None:
    now = time.time()
    dead = [k for k, v in _store.items() if now - v.created_at > TTL_SECONDS]
    if dead:
        _remember_session_ended_ids(dead)
    for k in dead:
        dv = _store.get(k)
        if dv:
            _content_index.pop((dv.user_id, dv.content_hash), None)
            if _quiz_faiss_retrieval:
                _quiz_faiss_retrieval.drop_document_index(k)
        del _store[k]


# fn: _trim_documents_for_user_locked | tr: kullanıcı başına max pdf sayısı uygula / en: enforce max pdfs per user
def _trim_documents_for_user_locked(user_id: int) -> None:
    per_user_max = int(os.getenv("PDF_DOCUMENTS_PER_USER_MAX", "6").strip() or "6")
    per_user_max = max(2, min(per_user_max, 20))
    items = [(k, v) for k, v in _store.items() if v.user_id == user_id]
    if len(items) <= per_user_max:
        return
    items.sort(key=lambda x: x[1].created_at, reverse=True)
    dead = items[per_user_max:]
    if dead:
        _remember_session_ended_ids([k for k, _ in dead])
    for k, dv in dead:
        _content_index.pop((dv.user_id, dv.content_hash), None)
        if _quiz_faiss_retrieval:
            _quiz_faiss_retrieval.drop_document_index(k)
        _store.pop(k, None)


# fn: _content_hash | tr: aynı pdf içeriğini tespit et / en: detect duplicate pdf content
def _content_hash(study_text: str) -> str:
    return sha1((study_text or "").strip().encode("utf-8", errors="ignore")).hexdigest()


# fn: _build_structured_chunks | tr: metni parçalara böl + topic etiketi / en: split text into chunks with topics
def _build_structured_chunks(
    text: str,
    chunk_w: int,
    overlap: int,
) -> Tuple[List[str], List[str]]:
    structured_chunks = chunk_text_with_topics(text, chunk_w, overlap)

    chunks = [str(item.get("text", "")).strip() for item in structured_chunks if str(item.get("text", "")).strip()]
    chunk_topics = [str(item.get("topic", "")).strip() for item in structured_chunks if str(item.get("text", "")).strip()]

    if os.getenv("PDF_CHUNK_FILTER_ENABLED", "true").lower() in ("1", "true", "yes"):
        kept = [(c, t) for c, t in zip(chunks, chunk_topics) if is_valid_chunk(c)]
        if kept:
            chunks = [p[0] for p in kept]
            chunk_topics = [p[1] for p in kept]

    if text and not chunks:
        chunks = [text]
        chunk_topics = ["general topic"]

    if len(chunk_topics) != len(chunks):
        chunk_topics = [f"general topic {i + 1}" for i in range(len(chunks))]

    return chunks, chunk_topics


# fn: create_document | tr: yeni pdf yükle: chunk + embed + rag index / en: ingest new pdf: chunk, embed, rag index
def create_document(study_text: str, filename: str, user_id: int = 1) -> StoredDocument:
    chunk_w = int(os.getenv("PDF_CHAT_CHUNK_WORDS", "800").strip() or "800")
    overlap = int(os.getenv("PDF_CHAT_CHUNK_OVERLAP", "120").strip() or "120")
    chunk_w = max(150, min(chunk_w, 900))
    overlap = max(0, min(overlap, chunk_w // 2))

    text = (study_text or "").strip()
    chash = _content_hash(text)

    with _lock:
        _purge_expired_locked()
        # tr: ayni içerik varsa cache'den dön / en: return cached doc if same content exists
        cached_id = _content_index.get((int(user_id), chash))
        if cached_id and cached_id in _store:
            cached = _store[cached_id]
            cached.filename = filename or cached.filename
            cached.created_at = time.time()
            from app.services.document_retrieval import build_rag_index_if_possible

            build_rag_index_if_possible(cached)
            return cached

    chunks, chunk_topics = _build_structured_chunks(text, chunk_w, overlap)

    embeddings = embed_chunks(chunks) if chunks else []
    if len(embeddings) != len(chunks):
        embeddings = [None] * len(chunks)

    topic_embeddings = embed_chunks(chunk_topics) if chunk_topics else []
    if len(topic_embeddings) != len(chunk_topics):
        topic_embeddings = [None] * len(chunk_topics)

    did = str(uuid.uuid4())
    doc = StoredDocument(
        document_id=did,
        user_id=int(user_id),
        filename=filename or "document.pdf",
        study_text=text,
        chunks=chunks,
        chunk_topics=chunk_topics,
        chunk_embeddings=embeddings,
        chunk_topic_embeddings=topic_embeddings,
        content_hash=chash,
        created_at=time.time(),
    )

    from app.services.document_retrieval import build_rag_index_if_possible

    build_rag_index_if_possible(doc)

    with _lock:
        _purge_expired_locked()
        _trim_documents_for_user_locked(int(user_id))
        _store[did] = doc
        _content_index[(int(user_id), chash)] = did

    return doc


# fn: restore_document_session | tr: mysql'den aynı id ile ram'e geri yukle / en: restore to ram from mysql with same id
def restore_document_session(
    document_id: str,
    user_id: int,
    study_text: str,
    filename: str = "document.pdf",
) -> StoredDocument:
    did = (document_id or "").strip()
    text = (study_text or "").strip()
    if not did or not text:
        raise ValueError("restore_document_session requires non-empty document_id and study_text")

    chunk_w = int(os.getenv("PDF_CHAT_CHUNK_WORDS", "800").strip() or "800")
    overlap = int(os.getenv("PDF_CHAT_CHUNK_OVERLAP", "120").strip() or "120")
    chunk_w = max(150, min(chunk_w, 900))
    overlap = max(0, min(overlap, chunk_w // 2))

    chash = _content_hash(text)
    chunks, chunk_topics = _build_structured_chunks(text, chunk_w, overlap)

    embeddings = embed_chunks(chunks) if chunks else []
    if len(embeddings) != len(chunks):
        embeddings = [None] * len(chunks)

    topic_embeddings = embed_chunks(chunk_topics) if chunk_topics else []
    if len(topic_embeddings) != len(chunk_topics):
        topic_embeddings = [None] * len(chunk_topics)

    doc = StoredDocument(
        document_id=did,
        user_id=int(user_id),
        filename=filename or "document.pdf",
        study_text=text,
        chunks=chunks,
        chunk_topics=chunk_topics,
        chunk_embeddings=embeddings,
        chunk_topic_embeddings=topic_embeddings,
        content_hash=chash,
        created_at=time.time(),
        quiz_topic_cards=None,
    )

    from app.services.document_retrieval import build_rag_index_if_possible

    build_rag_index_if_possible(doc)

    with _lock:
        _purge_expired_locked()
        _trim_documents_for_user_locked(int(user_id))
        _store[did] = doc
        _content_index[(int(user_id), chash)] = did
        _session_ended_ids.pop(did, None)

    return doc


# fn: set_document_quiz_topic_cards | tr: quiz konu kartlarını oturuma ekle / en: attach quiz topic cards to session
def set_document_quiz_topic_cards(document_id: str, cards: List[Dict[str, str]]) -> None:
    did = (document_id or "").strip()
    if not did or not cards:
        return
    with _lock:
        doc = _store.get(did)
        if doc is not None:
            doc.quiz_topic_cards = [dict(c) for c in cards]


# fn: get_document | tr: ram'den oturum getir, ttl yenile / en: get session from ram, refresh ttl
def get_document(document_id: str) -> Optional[StoredDocument]:
    if not document_id or not isinstance(document_id, str):
        return None
    with _lock:
        _purge_expired_locked()
        doc = _store.get(document_id.strip())
        # tr: okuma yapılınca oturum süresi uzar / en: sliding ttl on read
        if doc is not None:
            doc.created_at = time.time()
        return doc


# fn: document_missing_reason | tr: neden yok: expired vs missing / en: why missing: expired vs missing
def document_missing_reason(document_id: str) -> Optional[str]:
    if not document_id or not isinstance(document_id, str):
        return "missing_document"
    sid = document_id.strip()
    if not sid:
        return "missing_document"
    with _lock:
        if sid in _store:
            return None
        if sid in _session_ended_ids:
            return "expired_document"
        return "missing_document"

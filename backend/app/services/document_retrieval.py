# svc: document_retrieval | tr: pdf chunk'larindan soruya en uygun parcalari bul (rag) / en: find best pdf chunks for query (rag)

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, List, Optional

import numpy as np

from app.services.ollama_embeddings_service import cosine_similarity, embed_text
from app.services.pdf_text_clean import clean_retrieved_chunks, notebook_noise_penalty_for_chunk

if TYPE_CHECKING:
    from app.services.document_store import StoredDocument


_log = logging.getLogger(__name__)

_QUERY_TOKEN_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,}")
# cfg: sorgu kelimelerinden cikarilacak stopword'ler / en: stopwords filtered from query terms
_QUERY_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "about", "between",
    "how", "what", "why", "where", "when", "which", "use", "using", "used", "into",
    "ve", "ile", "için", "olan", "olarak", "bu", "şu", "bir", "çok", "daha", "gibi",
    "ama", "ancak", "fakat", "veya", "ya", "de", "da", "mi", "mı", "mu", "mü",
}


# fn: default_rag_top_k | tr: kac chunk donulecegi (env, 3-8) / en: how many chunks to return (env, 3-8)
def default_rag_top_k() -> int:
    raw = int(os.getenv("SUMMARY_RAG_TOP_K", "4").strip() or "4")
    return max(3, min(raw, 8))


# fn: _expand_with_neighbors | tr: secilen chunk'in komsularini da ekle / en: add neighbor chunks around selected indices
def _expand_with_neighbors(indices: List[int], total_chunks: int, limit: int) -> List[int]:
    if total_chunks <= 0 or not indices:
        return []

    expanded: set[int] = set()
    for i in indices:
        if i < 0 or i >= total_chunks:
            continue
        expanded.add(i)
        if i - 1 >= 0:
            expanded.add(i - 1)
        if i + 1 < total_chunks:
            expanded.add(i + 1)

    ordered = sorted(expanded)
    cap = max(1, min(limit, total_chunks))
    return ordered[:cap]


# fn: _neighbor_lexical_hit | tr: komsuda sorgu kelimesi var mi / en: does neighbor chunk contain query terms
def _neighbor_lexical_hit(chunks: List[str], idx: int, query: str) -> bool:
    terms = _query_terms(query)
    if not terms:
        return True
    low = (chunks[idx] or "").lower()
    return any(t in low for t in terms)


# fn: _expand_with_neighbors_filtered | tr: alakasiz komsulari filtrele / en: expand neighbors but drop irrelevant ones
def _expand_with_neighbors_filtered(
    primary_indices: List[int],
    total_chunks: int,
    limit: int,
    chunks: List[str],
    query: str,
) -> List[int]:
    if total_chunks <= 0 or not primary_indices:
        return []

    primary_set = {i for i in primary_indices if 0 <= i < total_chunks}
    expanded: set[int] = set(primary_set)
    for i in primary_set:
        for j in (i - 1, i + 1):
            if j < 0 or j >= total_chunks:
                continue
            if j in primary_set:
                expanded.add(j)
                continue
            if _neighbor_lexical_hit(chunks, j, query):
                expanded.add(j)

    ordered = sorted(expanded)
    cap = max(1, min(limit, total_chunks))
    return ordered[:cap]


# fn: _chunks_from_indices | tr: index listesinden chunk metinlerini al / en: get chunk texts from index list
def _chunks_from_indices(
    chunks: List[str],
    indices: List[int],
    *,
    limit: int,
    query: Optional[str] = None,
) -> List[str]:
    if query and query.strip():
        picked = _expand_with_neighbors_filtered(indices, len(chunks), limit, chunks, query.strip())
    else:
        picked = _expand_with_neighbors(indices, len(chunks), limit)
    return [chunks[i] for i in picked]


# fn: _expanded_limit | tr: komsu ekledikten sonra max chunk sayisi / en: max chunks after neighbor expansion
def _expanded_limit(k: int, total_chunks: int) -> int:
    k = max(1, int(k))
    extra = int(os.getenv("RAG_NEIGHBOR_MAX_EXTRA", "2").strip() or "2")
    extra = max(0, min(extra, 6))
    return max(1, min(total_chunks, k + extra))


# fn: _min_embed_similarity | tr: min embedding benzerlik esigi / en: min embedding similarity threshold
def _min_embed_similarity() -> float:
    raw = os.getenv("RAG_MIN_RELEVANCE_SIM", "0.12").strip()
    try:
        val = float(raw)
    except ValueError:
        val = 0.12
    return max(0.0, min(val, 0.95))


# fn: _min_lexical_score | tr: min kelime eslesme skoru / en: min lexical match score
def _min_lexical_score() -> int:
    raw = os.getenv("RAG_MIN_LEXICAL_SCORE", "2").strip()
    try:
        val = int(raw)
    except ValueError:
        val = 2
    return max(1, min(val, 12))


# fn: _query_terms | tr: sorgudan anlamli kelimeleri cikar / en: extract meaningful query terms
def _query_terms(query: str) -> List[str]:
    toks = [t.lower() for t in _QUERY_TOKEN_RE.findall(query or "")]
    return [t for t in toks if t not in _QUERY_STOP]


# cfg: explain modu icin chunk ipuclari / en: chunk cues for explain mode
_EXPLAIN_CHUNK_CUES = re.compile(
    r"\b(because|therefore|thus|hence|implies|means that|for example|specifically|"
    r"step\s+\d|first,|second,|finally,|çünkü|böylece|yani|örneğin|örnek olarak|"
    r"sonuç olarak|tanımlan|açısından|dolayısıyla)\b",
    re.IGNORECASE,
)
# cfg: exam modu icin chunk ipuclari / en: chunk cues for exam mode
_EXAM_CHUNK_CUES = re.compile(
    r"\b(risk|risks|warning|caution|trap|common mistake|false|incorrect|must not|"
    r"should not|important|definition|theorem|lemma|proof|contrast|versus|vs\.|"
    r"tanım|teorem|ispat|dikkat|yanlış|sınav|kavram|zorunlu|olmaz|dikkat ed|"
    r"hatırla|sıkça sorulan)\b",
    re.IGNORECASE,
)


# fn: _normalize_study_mode_for_rag | tr: ozet modunu summary/explain/exam yap / en: normalize mode to summary/explain/exam
def _normalize_study_mode_for_rag(mode: Optional[str]) -> Optional[str]:
    if not mode:
        return None
    k = str(mode).strip().lower()
    if k in ("summary", "quick_summary"):
        return "summary"
    if k in ("explain", "explain_simple", "explain_mode"):
        return "explain"
    if k in ("exam", "exam_focus", "exam_mode"):
        return "exam"
    return None


# fn: _chunk_retrieval_mode_bonus | tr: moda gore chunk skor bonusu / en: chunk score bonus by study mode
def _chunk_retrieval_mode_bonus(chunk: str, mode: Optional[str]) -> float:
    m = _normalize_study_mode_for_rag(mode)
    if not m or not chunk:
        return 0.0
    sym = sum(1 for c in chunk if c in "[]{}=<>/") / max(len(chunk), 1)
    words = len(chunk.split())

    if m == "summary":
        b = 0.0
        if words <= 240:
            b += 0.045
        if sym < 0.045:
            b += 0.035
        head = (chunk[:160] or "").lstrip()
        if head and head[0].isupper():
            b += 0.02
        return min(0.13, b)

    if m == "explain":
        if _EXPLAIN_CHUNK_CUES.search(chunk):
            return min(0.12, 0.055 + min(0.04, 0.012 * len(_EXPLAIN_CHUNK_CUES.findall(chunk))))
        if sym < 0.06:
            return 0.02
        return 0.0

    if m == "exam":
        if _EXAM_CHUNK_CUES.search(chunk):
            return min(0.14, 0.07 + min(0.05, 0.01 * len(_EXAM_CHUNK_CUES.findall(chunk))))
        if sym > 0.02:
            return 0.0
        return 0.0

    return 0.0


# fn: _query_attempts | tr: sorgunun kisa/uzun varyantlarini uret / en: build shorter query variants for retry
def _query_attempts(query: str) -> List[str]:
    base = re.sub(r"\s+", " ", (query or "").strip())
    if not base:
        return []

    terms = _query_terms(base)
    variants: List[str] = [base]

    if terms:
        variants.append(" ".join(terms[:7]))
        variants.append(" ".join(terms[:4]))
        if len(terms) >= 2:
            variants.append(f"{terms[0]} {terms[1]}")

    out: List[str] = []
    seen: set[str] = set()
    for v in variants:
        vv = re.sub(r"\s+", " ", (v or "").strip())
        if not vv or vv in seen:
            continue
        seen.add(vv)
        out.append(vv)

    return out


# fn: _lexical_scored_indices | tr: kelime/topic eslesmesine gore chunk sirala / en: rank chunks by lexical/topic match
def _lexical_scored_indices(
    chunks: List[str],
    topics: List[str],
    query: str,
    retrieval_mode: Optional[str] = None,
) -> List[tuple[int, int]]:
    terms = _query_terms(query)
    if not terms:
        return []

    scored: List[tuple[int, int]] = []
    for i, ch in enumerate(chunks):
        topic_low = (topics[i] or "").lower()
        text_low = (ch or "").lower()
        score = 0
        for t in terms:
            score += topic_low.count(t) * 5
            score += text_low.count(t)
        score += int(round(_chunk_retrieval_mode_bonus(ch, retrieval_mode) * 85))
        pen = notebook_noise_penalty_for_chunk(ch or "")
        score -= int(pen * 420)
        if score < 0:
            score = 0
        scored.append((score, i))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


# fn: _topic_candidate_indices | tr: topic eslesmesiyle aday chunk'lari daralt / en: narrow candidates by topic match
def _topic_candidate_indices(topics: List[str], query: str, max_candidates: int) -> List[int]:
    terms = _query_terms(query)
    if not topics or not terms:
        return []

    scored: List[tuple[int, int]] = []
    for i, topic in enumerate(topics):
        topic_low = (topic or "").lower()
        score = 0
        for t in terms:
            if t in topic_low:
                score += 3
                score += topic_low.count(t)
        if score > 0:
            scored.append((score, i))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:max_candidates]]


# fn: _max_topic_match_score | tr: en iyi topic eslesme skoru / en: best topic overlap score
def _max_topic_match_score(topics: List[str], query: str) -> int:
    terms = _query_terms(query)
    if not terms or not topics:
        return 0
    best = 0
    for topic in topics:
        topic_low = (topic or "").lower()
        score = 0
        for t in terms:
            if t in topic_low:
                score += 3
                score += topic_low.count(t)
        best = max(best, score)
    return best


# fn: _topic_gate_use_all_chunks | tr: topic zayifsa tum chunk'larda ara / en: search all chunks if topic match is weak
def _topic_gate_use_all_chunks(topics: List[str], query: str, candidate_indices: List[int], total_chunks: int) -> bool:
    if total_chunks <= 0 or not candidate_indices or len(candidate_indices) >= total_chunks:
        return False
    try:
        min_best = int(os.getenv("RAG_TOPIC_MATCH_MIN_BEST_SCORE", "5").strip() or "5")
    except ValueError:
        min_best = 5
    min_best = max(1, min(min_best, 50))
    return _max_topic_match_score(topics, query) < min_best


# fn: _lexical_proxy_similarity | tr: embedding yoksa kelime skorunu pseudo-sim yap / en: lexical score as pseudo similarity when no embedding
def _lexical_proxy_similarity(
    attempt: str,
    chunk: str,
    topics: List[str],
    idx: int,
    retrieval_mode: Optional[str],
) -> float:
    terms = _query_terms(attempt)
    if not terms:
        return 0.0
    topic_low = (topics[idx] or "").lower() if idx < len(topics) else ""
    text_low = (chunk or "").lower()
    score = 0
    for t in terms:
        score += topic_low.count(t) * 5
        score += text_low.count(t)
    score += int(round(_chunk_retrieval_mode_bonus(chunk, retrieval_mode) * 85))
    denom = max(1, len(terms) * 10)
    return min(0.24, 0.03 + (score / float(denom)) * 0.18)


# fn: ranked_chunk_indices_for_lexical_query | tr: public: kelime skoruna gore index listesi / en: public: indices sorted by lexical score
def ranked_chunk_indices_for_lexical_query(
    chunks: List[str],
    topics: List[str],
    query: str,
    *,
    retrieval_mode: Optional[str] = None,
    limit: int = 48,
) -> List[int]:
    scored = _lexical_scored_indices(chunks, topics, query, retrieval_mode)
    lim = max(1, min(int(limit), len(chunks)))
    return [i for _, i in scored[:lim]]


# fn: build_rag_index_if_possible | tr: chunk embedding'lerden faiss index kur / en: build faiss index from chunk embeddings
def build_rag_index_if_possible(doc: "StoredDocument") -> None:
    if getattr(doc, "rag_index_built", False):
        return
    setattr(doc, "rag_index_built", True)

    chunks = doc.chunks or []
    embs = getattr(doc, "chunk_topic_embeddings", None) or []
    if len(embs) != len(chunks):
        embs = doc.chunk_embeddings or []
    if len(embs) != len(chunks):
        embs = [None] * len(chunks)

    row_indices: List[int] = []
    vectors: List[List[float]] = []
    for i, e in enumerate(embs):
        if e and isinstance(e, (list, tuple)) and len(e) >= 8:
            row_indices.append(i)
            vectors.append([float(x) for x in e])

    if not vectors:
        doc.rag_faiss_index = None  # type: ignore[attr-defined]
        doc.rag_faiss_chunk_indices = None  # type: ignore[attr-defined]
        return

    try:
        import faiss
    except ImportError:
        # tr: faiss yoksa cosine taramasi / en: fallback to cosine scan without faiss
        _log.warning("faiss not installed; using cosine scan over stored embeddings (no chunk re-embedding).")
        doc.rag_faiss_index = None  # type: ignore[attr-defined]
        doc.rag_faiss_chunk_indices = row_indices  # type: ignore[attr-defined]
        return

    mat = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms < 1e-12, 1.0, norms)
    mat = mat / norms
    dim = int(mat.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(mat)

    doc.rag_faiss_index = index  # type: ignore[attr-defined]
    doc.rag_faiss_chunk_indices = row_indices  # type: ignore[attr-defined]


# fn: retrieve_top_chunk_texts | tr: ana rag: soruya en uygun chunk metinlerini don / en: main rag: return best chunk texts for query
def retrieve_top_chunk_texts(
    doc: "StoredDocument",
    query: str,
    *,
    top_k: Optional[int] = None,
    retrieval_mode: Optional[str] = None,
) -> List[str]:
    k = top_k if top_k is not None else default_rag_top_k()
    chunks = doc.chunks or []
    topics = getattr(doc, "chunk_topics", None) or []
    if len(topics) != len(chunks):
        topics = ["" for _ in chunks]

    if not chunks:
        return []
    if not (query or "").strip():
        return []

    build_rag_index_if_possible(doc)

    attempts = _query_attempts(query)
    if not attempts:
        return []

    min_sim = _min_embed_similarity()
    min_lex = _min_lexical_score()
    expanded_k = _expanded_limit(max(1, int(k)), len(chunks))

    embs = getattr(doc, "chunk_embeddings", None) or []
    if len(embs) != len(chunks):
        embs = [None] * len(chunks)

    for attempt in attempts:
        # step 1 | tr: topic ile aday daralt / en: narrow candidates by topic
        candidate_indices = _topic_candidate_indices(topics, attempt, max_candidates=max(k * 5, 12))
        if not candidate_indices:
            candidate_indices = list(range(len(chunks)))
        elif _topic_gate_use_all_chunks(topics, attempt, candidate_indices, len(chunks)):
            candidate_indices = list(range(len(chunks)))

        # step 2 | tr: embedding benzerligi ile sirala / en: rank by embedding similarity
        q_emb = embed_text(attempt.strip())
        if q_emb:
            scored: List[tuple[float, int]] = []
            no_emb_fillers: List[tuple[float, int]] = []

            for i in candidate_indices:
                if i >= len(chunks):
                    continue
                emb = embs[i] if i < len(embs) else None

                topic_low = (topics[i] or "").lower()
                topic_bonus = 0.0
                for tok in _query_terms(attempt):
                    if tok in topic_low:
                        topic_bonus += 0.15
                topic_boost = min(0.45, topic_bonus)
                mode_b = _chunk_retrieval_mode_bonus(chunks[i], retrieval_mode)

                nb_pen = notebook_noise_penalty_for_chunk(chunks[i] or "")
                if emb is not None:
                    sim = cosine_similarity(q_emb, list(emb)) + topic_boost + mode_b - nb_pen
                    if sim >= min_sim:
                        scored.append((sim, i))
                else:
                    px = _lexical_proxy_similarity(attempt, chunks[i], topics, i, retrieval_mode)
                    px_adj = px + topic_boost * 0.35 + mode_b - nb_pen
                    no_emb_floor = max(0.05, min_sim * 0.5)
                    if px_adj >= no_emb_floor:
                        no_emb_fillers.append((px_adj, i))

            scored.sort(key=lambda x: (-x[0], x[1]))
            no_emb_fillers.sort(key=lambda x: (-x[0], x[1]))

            selected_idx: List[int] = []
            seen: set[int] = set()
            for _, i in scored:
                if i in seen:
                    continue
                seen.add(i)
                selected_idx.append(i)
                if len(selected_idx) >= k:
                    break

            if len(selected_idx) < k:
                for _, i in no_emb_fillers:
                    if i in seen:
                        continue
                    seen.add(i)
                    selected_idx.append(i)
                    if len(selected_idx) >= k:
                        break

            out_raw = _chunks_from_indices(chunks, selected_idx, limit=expanded_k, query=attempt.strip())
            if out_raw:
                cleaned = clean_retrieved_chunks(out_raw)
                return cleaned if cleaned else out_raw

        # step 3 | tr: embedding yetmezse kelime eslesmesi / en: lexical fallback if embedding fails
        scored_lex = _lexical_scored_indices(chunks, topics, attempt, retrieval_mode)
        selected_lex = [i for sc, i in scored_lex if sc >= min_lex][: max(1, min(k, len(chunks)))]
        out_lex_raw = _chunks_from_indices(chunks, selected_lex, limit=expanded_k, query=attempt.strip())
        if out_lex_raw:
            cleaned_lex = clean_retrieved_chunks(out_lex_raw)
            return cleaned_lex if cleaned_lex else out_lex_raw

    return []

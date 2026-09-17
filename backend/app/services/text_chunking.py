# svc: text_chunking | tr: pdf metnini anlamsal parçalara böl, chunk başına konu çıkar / en: split pdf text into semantic chunks and extract topic per chunk

from __future__ import annotations

import re
from typing import Dict, List, Sequence

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9\"“(])")
_HEADING_NUMBER_RE = re.compile(r"^\d+(\.\d+)*[\)\.]?\s+")
_TOKEN_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,}")
_MAX_TOPIC_WORDS = 6

# tr: konu anahtar kelime çıkarımı için durak kelimeler / en: stop words for topic keyword extraction
_STOP_WORDS = {
    "the", "and", "for", "that", "with", "from", "this", "these", "those", "into", "your",
    "about", "between", "under", "over", "then", "than", "are", "is", "was", "were", "have",
    "has", "had", "using", "used", "use", "also", "can", "could", "should", "would", "will",
    "you", "they", "their", "our", "its", "not", "all", "any", "each", "per",
    "ve", "ile", "için", "olan", "olarak", "bu", "şu", "bir", "çok", "daha", "gibi", "hem",
    "ama", "fakat", "ancak", "veya", "ya", "de", "da", "mi", "mı", "mu", "mü", "ki", "en",
    "her", "tüm", "bazı", "üzerine", "göre", "sonra", "önce", "ise",
}


# fn: _word_count | tr: metindeki kelime sayısı / en: word count in text
def _word_count(text: str) -> int:
    return len((text or "").split())


# fn: word_count | tr: kelime sayısı (dışa açık alias) / en: word count public alias
def word_count(text: str) -> int:
    return _word_count(text)


# fn: _normalize_block | tr: metin bloğundaki boş satırları temizle / en: normalize block by removing empty lines
def _normalize_block(block: str) -> str:
    lines = [ln.rstrip() for ln in (block or "").splitlines()]
    kept = [ln for ln in lines if ln.strip()]
    return "\n".join(kept).strip()


# fn: _is_heading_block | tr: blok başlık satırı mı / en: whether block is a heading line
def _is_heading_block(block: str) -> bool:
    s = _normalize_block(block)
    if not s:
        return False

    lines = s.splitlines()
    if len(lines) > 2:
        return False

    if s.startswith("#"):
        return True
    if _HEADING_NUMBER_RE.match(s):
        return True
    if s.endswith(":"):
        return True
    if s[-1:] in ".!?":
        return False

    words = s.split()
    if not words or len(words) > 12:
        return False

    punct = sum(1 for ch in s if ch in ",;")
    if punct > 1:
        return False

    upperish = sum(1 for w in words if w[:1].isupper())
    return upperish >= max(1, len(words) // 2)


# fn: _extract_heading_candidate | tr: bloktan kısa başlık adayı çıkar / en: extract short heading candidate from block
def _extract_heading_candidate(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""

    head = lines[0]
    if not _is_heading_block(head):
        return ""

    head = re.sub(r"^#+\s*", "", head).strip()
    head = _HEADING_NUMBER_RE.sub("", head).strip()
    head = head.rstrip(":").strip()

    words = head.split()
    if len(words) > _MAX_TOPIC_WORDS:
        words = words[:_MAX_TOPIC_WORDS]

    return " ".join(words).strip()


# fn: _split_paragraph_to_units | tr: paragrafı cümle sınırında kelime limitine göre böl / en: split paragraph at sentence boundaries by word limit
def _split_paragraph_to_units(paragraph: str, max_words: int) -> List[str]:
    p = re.sub(r"\s+", " ", (paragraph or "").strip())
    if not p:
        return []

    if _word_count(p) <= max_words:
        return [p]

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(p) if s.strip()]
    if len(sentences) <= 1:
        return [p]

    units: List[str] = []
    curr: List[str] = []
    curr_words = 0

    for sent in sentences:
        sw = _word_count(sent)
        if curr and curr_words + sw > max_words:
            units.append(" ".join(curr).strip())
            curr = [sent]
            curr_words = sw
            continue

        curr.append(sent)
        curr_words += sw

    if curr:
        units.append(" ".join(curr).strip())

    return [u for u in units if u]


# fn: _build_semantic_units | tr: başlık+paragraf farkında anlamsal birimler oluştur / en: build heading-aware semantic units
def _build_semantic_units(text: str, max_words: int) -> List[str]:
    raw_blocks = re.split(r"\n\s*\n+", (text or "").strip())
    blocks = [_normalize_block(b) for b in raw_blocks]
    blocks = [b for b in blocks if b]

    if not blocks:
        return []

    units: List[str] = []
    i = 0

    while i < len(blocks):
        block = blocks[i]

        # tr: mümkünse başlık + sonraki paragrafı birlikte tut / en: keep heading + following paragraph together when possible
        if _is_heading_block(block) and i + 1 < len(blocks) and not _is_heading_block(blocks[i + 1]):
            combined = f"{block}\n\n{blocks[i + 1]}".strip()
            if _word_count(combined) <= max_words:
                units.append(combined)
            else:
                units.append(block)
                units.extend(_split_paragraph_to_units(blocks[i + 1], max_words))
            i += 2
            continue

        if "\n" in block and _word_count(block) <= max_words:
            units.append(block)
        else:
            units.extend(_split_paragraph_to_units(block, max_words))

        i += 1

    return [u for u in units if u.strip()]


# fn: _topic_keywords | tr: metinden yüksek frekanslı konu anahtar kelimeleri / en: high-frequency topic keywords from text
def _topic_keywords(text: str, limit: int = 15) -> List[str]:
    if not text:
        return []

    toks = [t for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP_WORDS]
    if not toks:
        return []

    freq: Dict[str, int] = {}
    first_idx: Dict[str, int] = {}

    for idx, tok in enumerate(toks):
        key = tok.lower()
        freq[key] = freq.get(key, 0) + 1
        if key not in first_idx:
            first_idx[key] = idx

    ranked = sorted(freq.keys(), key=lambda k: (-freq[k], first_idx[k]))
    return ranked[:max(5, limit)]


# fn: _format_topic_from_keywords | tr: anahtar kelimelerden kısa konu etiketi üret / en: build short topic label from keywords
def _format_topic_from_keywords(keywords: List[str]) -> str:
    if not keywords:
        return "general topic"

    parts: List[str] = []
    for kw in keywords:
        if kw in parts:
            continue
        parts.append(kw)
        if len(parts) >= _MAX_TOPIC_WORDS:
            break

    return " ".join(parts).strip()


# fn: _make_unique_topic | tr: tekrarsız benzersiz konu etiketi oluştur / en: create unique non-repeating topic label
def _make_unique_topic(base: str, keywords: List[str], used: set[str]) -> str:
    topic = re.sub(r"\s+", " ", (base or "").strip()).strip(" -")
    if not topic:
        topic = "general topic"

    if topic not in used:
        used.add(topic)
        return topic

    accum = topic.split()
    for kw in keywords:
        if kw in accum:
            continue
        accum.append(kw)
        cand = " ".join(accum[:_MAX_TOPIC_WORDS]).strip()
        if cand and cand not in used:
            used.add(cand)
            return cand

    suffix = 2
    while True:
        cand = f"{topic} {suffix}"
        if cand not in used:
            used.add(cand)
            return cand
        suffix += 1


# fn: build_chunk_topics | tr: her chunk için kısa benzersiz konu etiketi listesi / en: short unique topic label per chunk
def build_chunk_topics(chunks: List[str]) -> List[str]:
    """
    Create one short, explicit, non-repeating topic string per chunk.
    Priority:
    1. heading text
    2. high-signal keywords from body
    """
    out: List[str] = []
    used: set[str] = set()

    for chunk in chunks or []:
        text = (chunk or "").strip()
        if not text:
            out.append(_make_unique_topic("general topic", [], used))
            continue

        heading = _extract_heading_candidate(text)
        kws = _topic_keywords(text, limit=15)

        if heading:
            base = heading.lower()
            out.append(_make_unique_topic(base, kws, used))
            continue

        base = _format_topic_from_keywords(kws[:5])
        out.append(_make_unique_topic(base, kws, used))

    return out


# fn: chunk_text_by_words | tr: kelime limiti ve overlap ile anlamsal chunk listesi / en: semantic chunks by word limit and overlap
def chunk_text_by_words(text: str, chunk_words: int, overlap_words: int) -> List[str]:
    """
    Build semantic chunks using heading+paragraph / paragraph / sentence groups.
    Tries to preserve sentence and paragraph boundaries.
    """
    t = (text or "").strip()
    if not t:
        return []

    cw = max(100, int(chunk_words))
    ow = max(0, min(int(overlap_words), cw // 2))

    units = _build_semantic_units(t, cw)
    if not units:
        return []

    if len(units) == 1:
        return [units[0]]

    chunks: List[str] = []
    cursor = 0
    guard = 0
    max_steps = max(32, len(units) * 4)

    while cursor < len(units) and guard < max_steps:
        guard += 1
        current_parts: List[str] = []
        current_words = 0
        idx = cursor

        while idx < len(units):
            u = units[idx]
            uw = _word_count(u)

            if current_parts and current_words + uw > cw:
                break

            current_parts.append(u)
            current_words += uw
            idx += 1

            if current_words >= cw:
                break

        if not current_parts:
            current_parts = [units[idx]]
            idx += 1

        chunk = "\n\n".join(part.strip() for part in current_parts if part.strip()).strip()
        if chunk and (not chunks or chunks[-1] != chunk):
            chunks.append(chunk)

        if idx >= len(units):
            break

        # tr: anlamsal overlap tam birimler üzerinden / en: semantic overlap using full units only
        if ow <= 0:
            cursor = idx
            continue

        back_words = 0
        back = idx - 1
        while back >= cursor and back_words < ow:
            back_words += _word_count(units[back])
            back -= 1

        next_cursor = max(cursor + 1, back + 1)
        cursor = min(next_cursor, idx)

    return chunks


_CHUNK_SYMBOL_NOISE = frozenset("[]{}=<>/_\\")


# fn: is_valid_chunk | tr: chunk embedding için yeterli uzunlukta ve kod gürültüsü değil mi / en: chunk valid for embedding not too short or code-like
def is_valid_chunk(
    chunk: str,
    *,
    min_chars: int = 80,
    max_symbol_ratio: float = 0.08,
) -> bool:
    """
    Drop tiny or code-like fragments before embedding / indexing (tutorial-style filter).

    - Too short after strip → invalid
    - High density of ``[]{}=<>/_\\`` → likely code / table noise → invalid
    """
    c = (chunk or "").strip()
    if len(c) < int(min_chars):
        return False
    n = len(c)
    ratio = sum(1 for ch in c if ch in _CHUNK_SYMBOL_NOISE) / max(n, 1)
    return ratio <= float(max_symbol_ratio)


# fn: chunk_text_with_topics | tr: chunk+konu+index yapılandırılmış çıktı (ana giriş) / en: structured chunk topic index output main entry
def chunk_text_with_topics(text: str, chunk_words: int, overlap_words: int) -> List[Dict[str, object]]:
    """
    Main structured output for downstream systems.

    Returns:
    [
        {
            "chunk_index": 0,
            "topic": "conflict of interest",
            "text": "...",
            "word_count": 123,
        },
        ...
    ]
    """
    chunks = chunk_text_by_words(text, chunk_words, overlap_words)
    topics = build_chunk_topics(chunks)

    out: List[Dict[str, object]] = []
    for idx, (chunk, topic) in enumerate(zip(chunks, topics)):
        out.append(
            {
                "chunk_index": idx,
                "topic": topic,
                "text": chunk,
                "word_count": _word_count(chunk),
            }
        )
    return out


# fn: sentence_based_character_chunks | tr: cümle listesini karakter bütçesiyle chunk'lara paketle / en: pack sentences into chunks under char budget
def sentence_based_character_chunks(
    sentences: Sequence[str],
    max_chars: int = 800,
    overlap_sentences: int = 2,
) -> List[str]:
    """
    Pack pre-split sentences into chunks under ``max_chars``, carrying the last
    ``overlap_sentences`` into the next chunk (tutorial-style sentence overlap).

    Prefer :func:`chunk_text_by_words` for PDF ingest: it also respects paragraphs
    and headings. Use this when you already have a sentence list or want a
    character budget with sentence boundaries only.
    """
    mc = max(80, int(max_chars))
    ov = max(0, int(overlap_sentences))

    chunks: List[str] = []
    current: List[str] = []

    for sentence in sentences:
        s = (sentence or "").strip()
        if not s:
            continue

        test = " ".join(current + [s])
        if len(test) <= mc:
            current.append(s)
            continue

        if current:
            chunks.append(" ".join(current).strip())
            tail = current[-ov:] if ov else []
            current = tail + [s]
            continue

        chunks.append(s)

    if current:
        chunks.append(" ".join(current).strip())

    return [c for c in chunks if c]


# fn: chunk_text_sentence_char_budget | tr: metni cümlelere bölüp karakter bütçeli chunk üret / en: split text to sentences then char-budget chunks
def chunk_text_sentence_char_budget(
    text: str,
    max_chars: int = 800,
    overlap_sentences: int = 2,
) -> List[str]:
    """Split text with :data:`_SENTENCE_SPLIT_RE`, then :func:`sentence_based_character_chunks`."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(t) if s.strip()]
    if len(sentences) <= 1:
        return [t]
    return sentence_based_character_chunks(sentences, max_chars, overlap_sentences)


# fn: chunk_text_fixed_character_windows | tr: sabit karakter penceresi ile overlap chunk (yedek yöntem) / en: fixed char window chunks with overlap fallback method
def chunk_text_fixed_character_windows(
    text: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> List[str]:
    """
    Overlapping fixed-size character windows (default ~800 chars, ~120 overlap).

    PDF upload / RAG in this app uses :func:`chunk_text_by_words`, which respects paragraphs
    and sentence groups instead of slicing mid-sentence.

    ``overlap`` is clamped below ``chunk_size`` so ``start`` always advances (naive
    ``chunk_size - overlap`` loops forever when overlap >= chunk_size).
    """
    t = (text or "").strip()
    if not t:
        return []

    cs = max(1, int(chunk_size))
    ov = max(0, min(int(overlap), cs - 1))
    step = cs - ov
    if step <= 0:
        step = cs

    chunks: List[str] = []
    start = 0
    n = len(t)
    guard = 0
    max_steps = max(32, (n // step) + 8)

    while start < n and guard < max_steps:
        guard += 1
        end = min(start + cs, n)
        piece = t[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start += step

    return chunks

# svc: summary_service | tr: pdf metninden mod/seviyeye göre özet üret (quick/explain/exam) / en: generate pdf summaries by mode level quick explain exam
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple, TypedDict

if TYPE_CHECKING:
    from app.services.document_store import StoredDocument


# fn: _summary_looks_like_code_dump | tr: özet metni kod dökümü gibi mi / en: whether summary text looks like a code dump
def _summary_looks_like_code_dump(s: str) -> bool:
    """True if the model echoed code instead of writing a prose summary."""
    if not s or len(s) < 70:
        return False
    low = s.lower()
    if low.count("import ") >= 2:
        return True
    if "def " in low and "(" in s and ")" in s:
        return True
    if re.search(r"\bfrom\s+[a-zA-Z_][\w.]*\s+import\s", low):
        return True
    if s.count("=") >= 14 and len(s) < 8000:
        return True
    if re.search(r"\b\w{75,}\b", s):
        return True
    if low.count("pd.") + low.count("np.") + low.count("plt.") + low.count("sns.") >= 4:
        return True
    return False


# fn: DocumentSummaryBundle | tr: çift dilli belge özeti typedict şeması / en: bilingual document summary typedict schema
class DocumentSummaryBundle(TypedDict):
    """Bilingual document summary for API responses."""

    summary_en: str
    summary_tr: str
    summary: str


# fn: normalize_output_locale | tr: çıktı dilini normalize et / en: normalize output locale
def normalize_output_locale(locale: Optional[str]) -> str:
    """Force study outputs to English regardless of UI locale."""
    return "en"


TR_STOP_WORDS = {
    "ve", "veya", "ya", "ile", "için", "gibi", "kadar", "daha", "çok", "az",
    "bu", "şu", "o", "bir", "iki", "üç", "dört", "beş",
    "de", "da", "ki", "mi", "mı", "mu", "mü",
    "olarak", "üzerine", "göre", "çünkü", "ancak", "fakat", "ama",
    "en", "her", "tüm", "bazı", "şey", "şeyler",
}

EN_STOP_WORDS = {
    "the", "is", "are", "of", "and", "to", "in", "a", "an", "for", "on",
    "with", "at", "by", "from", "as", "it", "this", "that", "these", "those",
    "be", "or", "if", "then", "than", "into", "about", "not", "we", "you", "they",
}

STOP_WORDS = TR_STOP_WORDS.union(EN_STOP_WORDS)

# Last word before final punctuation: headline-style fragments / hard truncation tails.
_INCOMPLETE_TAIL_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "into",
        "to",
        "in",
        "about",
        "over",
        "than",
        "when",
        "where",
        "which",
        "or",
        "and",
        "but",
        "if",
        "including",
        "between",
        "among",
        "upon",
        "via",
        "per",
        "such",
        "as",
        "is",
        "are",
        "was",
        "were",
        "been",
        "being",
        "has",
        "have",
        "had",
        "does",
        "did",
    }
)


# fn: _trim_summary_body | tr: özet gövdesini max karaktere kırp / en: trim summary body to max chars
def _trim_summary_body(body: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body.strip()
    cut = body[:max_chars]
    para = cut.rfind("\n\n")
    if para > max_chars * 0.45:
        return cut[:para].strip()
    return cut.strip()


_SENT_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[A-Za-zÇĞİÖŞÜçğıöşü\"“(0-9])",
)


# fn: _polish_summary_lines | tr: özet satırlarındaki boşlukları düzenle / en: normalize whitespace per summary line
def _polish_summary_lines(text: str) -> str:
    """Normalize spaces per line; keep paragraph breaks."""
    lines = text.split("\n")
    out: List[str] = []
    for line in lines:
        s = re.sub(r"[ \t]{2,}", " ", line.strip())
        out.append(s)
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# Lowercase letters that may begin a sentence (ASCII + Turkish).
_SENTENCE_LOWER = re.compile(r"[a-zçğıöşüı]")


# fn: _upper_sentence_letter | tr: cümle başı harfini büyüt (türkçe i/ı dahil) / en: uppercase sentence-start letter
def _upper_sentence_letter(ch: str) -> str:
    """Uppercase one character at sentence start; Turkish dotted/dotless i."""
    if ch == "i":
        return "İ"
    if ch == "ı":
        return "I"
    return ch.upper()


# fn: _capitalize_sentence_starts | tr: cümle başlarını büyük harfe çevir / en: capitalize sentence starts in summary
def _capitalize_sentence_starts(text: str) -> str:
    """
    Uppercase the first letter after sentence endings, paragraph breaks, list markers,
    and at the very start of the text (ASCII + Turkish i/ı).
    """
    if not (text or "").strip():
        return text or ""

    def _cap_after_punct(m: re.Match) -> str:
        spaces, quotes, letter = m.group(1), m.group(2), m.group(3)
        return spaces + quotes + _upper_sentence_letter(letter)

    # After . ! ? (whitespace may include newlines), optional opening quotes, then a lowercase letter.
    t = re.sub(
        rf"(?<=[.!?])(\s+)((?:[\"“'(\[]+\s*)*)({_SENTENCE_LOWER.pattern})",
        _cap_after_punct,
        text,
    )

    # First alphabetic character in the document (skip leading markdown headings).
    stripped = t.lstrip()
    if stripped and not stripped.startswith("#"):
        m0 = re.match(rf"^(\s*)({_SENTENCE_LOWER.pattern})", t)
        if m0:
            i = m0.end(2) - 1
            ch = t[i]
            t = t[:i] + _upper_sentence_letter(ch) + t[i + 1 :]

    # First letter after a blank line (paragraph or markdown block); skip leading # on that line.
    def _cap_para(m: re.Match) -> str:
        sep, lead, hashes, spaces, quotes, letter = m.group(1, 2, 3, 4, 5, 6)
        return sep + lead + hashes + spaces + quotes + _upper_sentence_letter(letter)

    t = re.sub(
        rf"(\n\n)(\s*)(#{{1,6}}\s+)?(\s*)((?:[\"“'(\[]+\s*)*)({_SENTENCE_LOWER.pattern})",
        _cap_para,
        t,
    )

    # Markdown / plain list lines: "- text", "* text", "+ text", "• text"
    lines = t.split("\n")
    bullet = re.compile(rf"^(\s*[-*+•]\s+)((?:[\"“'(\[]+\s*)*)({_SENTENCE_LOWER.pattern})")
    out_lines: List[str] = []
    for line in lines:
        m = bullet.match(line)
        if m:
            letter = m.group(3)
            i = m.end(3) - 1
            out_lines.append(
                line[:i] + _upper_sentence_letter(letter) + line[i + 1 :]
            )
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


# fn: _break_dense_paragraphs | tr: yoğun paragrafları böl / en: break dense paragraphs for readability
def _break_dense_paragraphs(text: str, min_len: int = 280) -> str:
    """Turn long single paragraphs into several blocks separated by blank lines."""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    out: List[str] = []
    for chunk in chunks:
        first_line = chunk.split("\n", 1)[0].strip()
        if first_line.startswith("-") or first_line.startswith("*") or first_line.startswith("•"):
            out.append(chunk)
            continue
        if len(chunk) < min_len:
            out.append(chunk)
            continue
        parts = _SENT_SPLIT.split(chunk)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            out.append("\n\n".join(parts))
        else:
            out.append(chunk)
    return "\n\n".join(out)


# fn: normalize_summary_format | tr: özet metnini standart biçime normalize et / en: normalize summary text format
def normalize_summary_format(text: str) -> str:
    """
    Readable layout: paragraphs separated by blank lines; break dense one-line walls.
    """
    if not text or not text.strip():
        return text
    t = text.strip().replace("\r\n", "\n")
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    if "\n\n" not in t and "\n" in t:
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        if len(lines) > 1:
            t = "\n\n".join(lines)
    if "\n" not in t:
        parts = _SENT_SPLIT.split(t)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            t = "\n\n".join(parts)
    t = _break_dense_paragraphs(t)
    t = _polish_summary_lines(t)
    from app.services.pdf_text_clean import strip_decorative_dash_runs

    t = strip_decorative_dash_runs(t)
    return _capitalize_sentence_starts(t)


# Back-compat name used in older call sites.
format_summary_text = normalize_summary_format


# fn: _split_into_sentences | tr: metni cümlelere böl / en: split text into sentences
def _split_into_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20:
            continue
        out.append(s)
    return out


# fn: _tokenize | tr: metni token listesine ayır / en: tokenize text into word list
def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]+", text.lower())
    return [t for t in tokens if len(t) > 2 and t not in STOP_WORDS]


# fn: _tfidf_sentence_scores | tr: cümleler için tf-idf benzeri skor / en: tf-idf-like sentence scores
def _tfidf_sentence_scores(sentences: List[str]) -> Tuple[Dict[int, float], List[List[str]]]:
    tokenized = [_tokenize(s) for s in sentences]
    df = Counter()
    for toks in tokenized:
        df.update(set(toks))

    n = max(len(sentences), 1)
    idf = {w: math.log((n + 1) / (df_w + 1)) + 1.0 for w, df_w in df.items()}

    scores: Dict[int, float] = {}
    for i, toks in enumerate(tokenized):
        if not toks:
            scores[i] = 0.0
            continue
        tf = Counter(toks)
        score = 0.0
        for w, c in tf.items():
            score += (c / len(toks)) * idf.get(w, 0.0)
        position_bonus = 1.0 + (0.15 * (1.0 - (i / max(n - 1, 1))))
        scores[i] = score * position_bonus

    return scores, tokenized


# fn: _jaccard | tr: iki token listesi jaccard benzerliği / en: jaccard similarity between token lists
def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa.intersection(sb))
    union = len(sa.union(sb))
    return inter / union if union else 0.0


# fn: _heuristic_input_slice | tr: sezgisel özet için giriş metnini sınırla / en: bound input for heuristic summary
def _heuristic_input_slice(text: str) -> str:
    """Use a large slice of the extract so summaries reflect more of the PDF."""
    raw = os.getenv("SUMMARY_HEURISTIC_MAX_CHARS", "60000").strip()
    try:
        cap = int(raw)
    except ValueError:
        cap = 60_000
    cap = max(24_000, min(cap, 120_000))
    t = (text or "").strip()
    return t[: min(len(t), cap)]


# fn: summarize_heuristic | tr: tf-idf sezgisel çıkarımsal özet / en: extractive heuristic tf-idf summary
def summarize_heuristic(
    text: str,
    max_sentences: int = 4,
    max_chars: int = 1200,
    redundancy_threshold: float = 0.65,
    output_locale: str = "en",
) -> str:
    empty_msg = "No text available to summarize."
    if not text or not text.strip():
        return empty_msg

    sentences = _split_into_sentences(_heuristic_input_slice(text))
    if not sentences:
        return empty_msg

    if len(sentences) <= max_sentences:
        joined = "\n\n".join(s.strip() for s in sentences)
        return _trim_summary_body(joined, max_chars)

    scores, tokenized = _tfidf_sentence_scores(sentences)
    candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    selected: List[int] = []
    current_len = 0
    for idx, _score in candidates:
        if len(selected) >= max_sentences:
            break

        too_similar = False
        for sidx in selected:
            if _jaccard(tokenized[idx], tokenized[sidx]) >= redundancy_threshold:
                too_similar = True
                break
        if too_similar:
            continue

        sentence = sentences[idx]
        if current_len + len(sentence) > max_chars and selected:
            continue

        selected.append(idx)
        current_len += len(sentence) + 1

    if not selected:
        selected = list(range(min(max_sentences, len(sentences))))

    selected_sorted = sorted(selected)
    summary = "\n\n".join(sentences[i].strip() for i in selected_sorted)
    return _trim_summary_body(summary, max_chars)


# fn: summarize_document | tr: pdf metninden çift dilli belge özeti / en: bilingual document summary from pdf
def summarize_document(text: str, output_locale: str = "en") -> DocumentSummaryBundle:
    """
    Clean and normalize PDF text first, then summarize in English.
    Study outputs are forced to English regardless of UI locale.
    """
    from app.services.pdf_text_clean import build_normalized_study_text
    from app.services.ollama_service import (
        ollama_available,
        repair_extracted_text_with_ollama,
        summarize_document_english_with_ollama,
        translate_summary_to_turkish,
    )

    loc = normalize_output_locale(output_locale)
    body = build_normalized_study_text(text)

    if os.getenv("SUMMARY_OLLAMA_REPAIR", "").lower() in ("1", "true", "yes") and ollama_available():
        try:
            body = repair_extracted_text_with_ollama(body)
        except Exception:
            pass

    empty_en = "No text available to summarize."
    empty_tr = empty_en
    if not body:
        primary = empty_en
        return {
            "summary_en": empty_en,
            "summary_tr": empty_tr,
            "summary": primary,
        }

    n = len(body)
    if n > 120_000:
        min_chars, max_chars = 3000, 5600
    elif n > 60_000:
        min_chars, max_chars = 2800, 5200
    else:
        min_chars, max_chars = 2600, 4800

    provider = os.getenv("SUMMARY_PROVIDER", "auto").lower().strip()
    use_ollama = provider in ("ollama", "auto")
    en_summary = ""

    if use_ollama and ollama_available():
        try:
            en_summary = (summarize_document_english_with_ollama(body, max_chars, min_chars, True) or "").strip()
            if not en_summary or _summary_looks_like_code_dump(en_summary):
                en_summary = (
                    summarize_document_english_with_ollama(body, max_chars, min_chars, False) or ""
                ).strip()
            if en_summary and _summary_looks_like_code_dump(en_summary):
                en_summary = ""
        except Exception:
            en_summary = ""

    if not en_summary:
        if provider == "ollama":
            en_summary = (
                "Ollama could not produce a clean English summary. "
                "Check that Ollama is running, try SUMMARY_PROVIDER=auto, or verify OLLAMA_MODEL."
            )
        else:
            max_sent = min(12, max(8, n // 15000))
            en_summary = summarize_heuristic(
                body,
                max_sentences=max_sent,
                max_chars=max_chars,
                redundancy_threshold=0.64,
                output_locale="en",
            )

    en_summary = normalize_summary_format(en_summary.strip())

    tr_summary = ""
    if (
        ollama_available()
        and en_summary
        and "Ollama could not produce" not in en_summary
    ):
        try:
            tr_summary = translate_summary_to_turkish(en_summary, max_chars)
            tr_summary = normalize_summary_format((tr_summary or "").strip())
        except Exception:
            tr_summary = ""

    primary = en_summary
    if loc == "tr" and tr_summary:
        primary = tr_summary
    elif loc == "tr" and not tr_summary:
        primary = en_summary

    return {
        "summary_en": en_summary,
        "summary_tr": tr_summary,
        "summary": primary,
    }


# fn: summarize_text | tr: metni özetle ollama veya sezgisel / en: summarize text via ollama or heuristic
def summarize_text(
    text: str,
    max_sentences: int = 4,
    max_chars: int = 1200,
    redundancy_threshold: float = 0.65,
    document_mode: bool = False,
    document_min_chars: int = 0,
    output_locale: str = "en",
) -> str:
    """
    SUMMARY_PROVIDER:
      - heuristic: TF-IDF style extractive summary only
      - ollama: Ollama only (returns error message to user on failure)
      - auto: try Ollama first, fall back to heuristic
    Skips Ollama when OLLAMA_ENABLED=false.
    """
    loc = normalize_output_locale(output_locale)
    provider = os.getenv("SUMMARY_PROVIDER", "auto").lower().strip()

    use_ollama = provider in ("ollama", "auto")
    if use_ollama:
        try:
            from app.services.ollama_service import ollama_available, summarize_with_ollama

            if ollama_available():
                out = summarize_with_ollama(
                    text,
                    max_sentences=max_sentences,
                    max_chars=max_chars,
                    document_mode=document_mode,
                    min_chars=document_min_chars if document_mode else 0,
                    output_locale=loc,
                    strict_prose=True,
                    temperature=0.2,
                )
                cand = (out or "").strip()
                if cand and document_mode and _summary_looks_like_code_dump(cand):
                    out2 = summarize_with_ollama(
                        text,
                        max_sentences=max_sentences,
                        max_chars=max_chars,
                        document_mode=document_mode,
                        min_chars=document_min_chars if document_mode else 0,
                        output_locale=loc,
                        strict_prose=False,
                        temperature=0.2,
                    )
                    c2 = (out2 or "").strip()
                    if c2 and not _summary_looks_like_code_dump(c2):
                        cand = c2
                if cand and (not document_mode or not _summary_looks_like_code_dump(cand)):
                    clean = normalize_summary_format(cand)
                    if max_sentences <= 4:
                        clean = _enforce_summary_bullet_mode(
                            clean,
                            [_quick_summary_context_clean(text)],
                            "normal",
                            "medium",
                        )
                    return clean
                if provider == "ollama":
                    if cand and document_mode and _summary_looks_like_code_dump(cand):
                        if loc == "tr":
                            return (
                                "Model özeti yerine kod metni döndürdü. "
                                "SUMMARY_PROVIDER=auto veya farklı bir OLLAMA_MODEL deneyin."
                            )
                        return (
                            "The model returned code-like text instead of a prose summary. "
                            "Try SUMMARY_PROVIDER=auto or a different OLLAMA_MODEL."
                        )
                    if loc == "tr":
                        return (
                            "Ollama boş yanıt döndü. OLLAMA_MODEL değerini ve Ollama'nın çalıştığını kontrol edin."
                        )
                    return (
                        "Ollama returned an empty response. Check OLLAMA_MODEL and that Ollama is running."
                    )
        except Exception as e:
            if provider == "ollama":
                if loc == "tr":
                    return (
                        f"Ollama ile özetlenemedi: {e!s}. "
                        "Ollama çalışıyor mu? Geçici çözüm: SUMMARY_PROVIDER=heuristic."
                    )
                return (
                    f"Could not summarize with Ollama: {e!s}. "
                    "Is Ollama running? Try SUMMARY_PROVIDER=heuristic as a fallback."
                )

    raw = summarize_heuristic(
        text,
        max_sentences=max_sentences,
        max_chars=max_chars,
        redundancy_threshold=redundancy_threshold,
        output_locale=loc,
    )
    clean = normalize_summary_format(raw)
    if max_sentences <= 4:
        clean = _enforce_summary_bullet_mode(
            clean,
            [_quick_summary_context_clean(text)],
            "normal",
            "medium",
        )
    return clean


SummaryMode = Literal["summary", "explain", "exam"]
SummaryLevel = Literal["beginner", "normal", "technical"]
INSUFFICIENT_CONTENT_MSG = "Not enough relevant content found in the document."

# Mode-first prompts (PDF study modes). Extended with grounding + shape in _system_prompt_* builders.
QUICK_PROMPT = """
You are skimming excerpted slices of one PDF like a sharp reader in a hurry: infer what the document is for,
who it is written for, and which ideas matter most before any detail work.

Write in professional American English as polished Markdown `-` bullets (no `##` headings, no preamble).
Each bullet is one complete sentence that prioritizes signal over noise: concrete, non-generic, and clearly
grounded in the excerpts.
"""

# Backward-compatible name (same string as ``QUICK_PROMPT``).
SUMMARY_SYSTEM_PROMPT = QUICK_PROMPT

EXPLAIN_PROMPT = """
Explain the PDF material simply for a student who finds the original dense, in professional American English.

Goal: a medium-length, easy-to-read overview of what this document is about and how the pieces fit—not a cram sheet.

Structure (keep these four sections):
- Big Picture: what this document is centrally doing and why it matters.
- How It Works: the main mechanism, argument, or storyline in plain steps (still `-` bullets under the heading).
- Example: one or two grounded illustrations the excerpts support (paraphrase, no long quotes).
- Key Takeaway: what the reader should walk away understanding.

Rules:
- Prefer plain words; avoid jargon unless you define it once in simple terms.
- No code fences; avoid raw equation lines—if a formula is essential, describe it in words.
- No ASCII/Unicode diagram scaffolding in bullets (no `|`, `▼`, arrow glyphs as fake “wiring”).
- Do NOT copy sentences from the PDF; paraphrase into clear, flowing lines.
"""

EXAM_PROMPT = """
Build exam-style revision notes in professional American English: what an instructor could plausibly test.

Rules:
- Stay on ONE coherent main topic from the excerpts; do not stitch unrelated chapters together.
- Ignore noisy OCR fragments; convert what you keep into crisp, testable lines.
- Prefer discriminators, conditions, “do not confuse X with Y”, and recall-ready facts.
- No code fences; if notation matters, state it in short plain-language recall lines (not long derivations).

Structure:
- Definitions
- Key Concepts
- Common Mistakes
- What to Memorize (must include 2–3 plausible exam questions as described in the system contract)
"""


# fn: _study_grounding_block | tr: llm için pdf dayanak kuralları bloğu / en: pdf grounding rules for llm prompt
def _study_grounding_block() -> str:
    return (
        "### GROUNDING (non-negotiable)\n"
        "- The user message contains excerpt blocks separated by a single line that reads exactly `---`.\n"
        "- Treat each block as separate evidence; do not merge unrelated blocks into one invented narrative.\n"
        "- Stray characters inside excerpts (e.g. a bare `##` from PDF extraction) are noise, not your outline—"
        "reason only from the substantive words.\n"
        f"- If the excerpts are too thin to produce the requested mode, output only this exact line:\n  {INSUFFICIENT_CONTENT_MSG}\n"
        "- Every substantive claim must be traceable to the excerpts; do not invent sources, sections, or data.\n\n"
    )


# fn: _mode_output_shape_rules | tr: moda göre çıktı uzunluğu kuralları / en: output length rules per mode
def _mode_output_shape_rules(mode: SummaryMode, summary_length: str = "medium") -> str:
    """How long each mode is vs the others, and what headings are allowed (parser + UX contract)."""
    if mode == "summary":
        depth = {
            "short": "very tight (still the fastest orientation).",
            "medium": "a bit more room: capture purpose, audience, and the main stakes in separate bullets.",
            "long": "still scannable, but you may stretch to the runtime bullet cap to separate distinct priorities.",
        }.get(summary_length, "brief.")
        return (
            "### MODE SHAPE: QUICK SUMMARY\n"
            f"- Quick Summary is the orientation pass—{depth}\n"
            "- It must stay shorter in total than Explain and much shorter than Exam.\n"
            "- No headings or titles at all: no `#`, `##`, `###`, no `Summary:`, `Overview`, chapter labels, or decorative labels.\n"
            "- Start directly with `- ` bullets; no meta preamble (“Here is a summary…”).\n\n"
        )
    if mode == "explain":
        word_band = {
            "short": "about 160–280 words when excerpts are rich.",
            "medium": "about 260–420 words when excerpts are rich (default “medium” PDF length).",
            "long": "about 360–560 words when excerpts are rich—still sectioned, not one wall of text.",
        }.get(summary_length, "about 260–420 words when excerpts are rich.")
        return (
            "### MODE SHAPE: EXPLAIN SIMPLY (medium length)\n"
            "- A clear, gentle explanation of the PDF for a reader who wants the forest before the trees.\n"
            f"- Target {word_band}\n"
            "- Use ONLY the four required `##` headings (exact titles); do not add `###`/`####`, "
            "and do not add extra sections such as Introduction, Overview, Summary, or Conclusion.\n"
            "- No lines before the first `## Big Picture`.\n\n"
        )
    exam_density = {
        "short": "dense but compact—still clearly more bullets than Explain.",
        "medium": "clearly longer than Explain: more bullets per heading, still one idea per line.",
        "long": (
            "the longest revision sheet: many bullets per heading (aim ~28–42 `-` lines total across the four "
            "headings when the excerpts support it), still one idea per line—no essay paragraphs."
        ),
    }.get(summary_length, "clearly longer than Explain.")
    return (
        "### MODE SHAPE: EXAM FOCUS (dense revision sheet)\n"
        f"- {exam_density}\n"
        "- Each bullet stays short and clear (one idea per line); flashcard-ready, not a lecture.\n"
        "- Use ONLY the four required `##` headings (exact titles); no `###` sub-headings, no duplicate topic headers, "
        "no Introduction/Overview/Summary blocks.\n"
        "- No lines before the first `## Definitions`.\n"
        "- Under `## What to Memorize`, include **2 or 3** extra `-` bullets that read like real exam questions "
        "(each line starts with `Exam Q:` then a short stem ending with `?`). They must be answerable from the "
        "excerpts alone—no invented syllabus topics.\n\n"
    )


# fn: _global_summary_quality_rules | tr: genel özet kalite kuralları / en: global summary quality rules
def _global_summary_quality_rules() -> str:
    return (
        "CRITICAL RULES (VERY IMPORTANT):\n"
        "- NEVER copy sentences directly from the PDF.\n"
        "- ALWAYS rewrite the idea in your own words.\n"
        "- EVERY bullet must be a COMPLETE sentence.\n"
        "- NEVER output unfinished or cut sentences.\n"
        "- NEVER start from the middle of a sentence.\n"
        "- NEVER include numbered fragments like `1)` or `2)`.\n"
        "- If a sentence looks incomplete, DISCARD it and rewrite properly.\n"
        "- Your job is NOT extraction, your job is CLEAN summarization.\n\n"
        "HEADINGS AND LABELS:\n"
        "- Do not invent decorative titles or pretend document structure (no filler section names).\n"
        "- Do not echo PDF headings as your own outline unless this mode’s fixed `##` list says so.\n\n"
        "SPELLING AND WORDING:\n"
        "- Proofread: use professional American English spelling; fix obvious typos and OCR glitches while you paraphrase.\n"
        "- Do not leave fused words, random capitals mid-sentence, or garbled fragments.\n\n"
        "QUALITY CHECK BEFORE OUTPUT:\n"
        "- Each bullet must make sense alone.\n"
        "- Each sentence must be grammatically complete.\n"
        "- No sentence should feel cut or missing context.\n"
        "- If any bullet fails this, REWRITE it.\n\n"
    )

_MODE_ALIASES: Dict[str, SummaryMode] = {
    "summary": "summary",
    "quick": "summary",
    "quick_summary": "summary",
    "explain": "explain",
    "explain_mode": "explain",
    "explain_simple": "explain",
    "exam": "exam",
    "exam_mode": "exam",
    "exam_focus": "exam",
}

_STYLE_ALIASES: Dict[str, str] = {
    "concise": "concise",
    "balanced": "balanced",
    "detailed": "detailed",
}

_FORMAT_ALIASES: Dict[str, str] = {
    "mixed": "mixed",
    "bullets": "bullets",
    "prose": "prose",
}

_LENGTH_ALIASES: Dict[str, str] = {
    "short": "short",
    "medium": "medium",
    "long": "long",
}

_LEVEL_ALIASES: Dict[str, SummaryLevel] = {
    "beginner": "beginner",
    "normal": "normal",
    "technical": "technical",
}


@dataclass
# fn: DynamicSummaryResult | tr: dinamik özet sonucu dataclass / en: dynamic summary result dataclass
class DynamicSummaryResult:
    text: str
    mode: SummaryMode
    level: SummaryLevel
    retrieved_chunks: List[str]


# fn: normalize_summary_style | tr: özet stil parametresini normalize et / en: normalize summary style
def normalize_summary_style(raw_style: Optional[str]) -> str:
    key = (raw_style or "balanced").strip().lower()
    return _STYLE_ALIASES.get(key, "balanced")


# fn: normalize_summary_format_enum | tr: özet format enum normalize et / en: normalize summary format enum
def normalize_summary_format_enum(raw_format: Optional[str]) -> str:
    key = (raw_format or "mixed").strip().lower()
    return _FORMAT_ALIASES.get(key, "mixed")


# fn: normalize_summary_length | tr: özet uzunluk parametresini normalize et / en: normalize summary length
def normalize_summary_length(raw_length: Optional[str]) -> str:
    key = (raw_length or "medium").strip().lower()
    return _LENGTH_ALIASES.get(key, "medium")


# fn: normalize_summary_mode | tr: modu summary|explain|exam normalize et / en: normalize mode to summary explain exam
def normalize_summary_mode(raw_mode: Optional[str]) -> SummaryMode:
    key = (raw_mode or "summary").strip().lower()
    return _MODE_ALIASES.get(key, "summary")


# fn: normalize_summary_level | tr: seviyeyi beginner|normal|technical normalize et / en: normalize summary level
def normalize_summary_level(raw_level: Optional[str]) -> SummaryLevel:
    key = (raw_level or "normal").strip().lower()
    return _LEVEL_ALIASES.get(key, "normal")


# fn: _document_filename_stem | tr: dosya adından konu ipucu çıkar / en: topic hint from filename
def _document_filename_stem(document: Optional["StoredDocument"]) -> str:
    """Short lexical hint from upload name (helps RAG match the right chapter/topic)."""
    if document is None:
        return ""
    fn = (getattr(document, "filename", "") or "").strip()
    if not fn:
        return ""
    stem = re.sub(r"\.[^./\\]+$", "", fn, flags=re.IGNORECASE).strip()
    stem = re.sub(r"[_\-]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()[:160]


# fn: _chunk_topic_skim | tr: chunk topic etiketlerinden sözcük topla / en: vocabulary from chunk topic labels
def _chunk_topic_skim(document: Optional["StoredDocument"], *, max_chars: int = 260) -> str:
    """Reuse ingest-time per-chunk topic labels as retrieval vocabulary (closer to PDF wording than meta-prompts)."""
    if document is None:
        return ""
    topics = getattr(document, "chunk_topics", None) or []
    bits: List[str] = []
    seen: set[str] = set()
    for raw in topics[:30]:
        t = re.sub(r"\s+", " ", (raw or "").strip())
        if len(t) < 8:
            continue
        k = t.lower()
        if k in seen or k in ("general topic", "general"):
            continue
        seen.add(k)
        bits.append(t)
    out = " ".join(bits).strip()
    return out[:max_chars] if out else ""


# fn: _build_summary_query | tr: rag arama sorgusu oluştur / en: build rag search query
def _build_summary_query(
    mode: SummaryMode,
    focus_topics: Sequence[str],
    challenge_topics: Sequence[str],
    level: SummaryLevel,
    summary_style: str = "balanced",
    summary_length: str = "medium",
    document: Optional["StoredDocument"] = None,
) -> str:
    """
    Text used ONLY for retrieval (embedding + lexical). Keep it noun-heavy and close to how
    textbook PDFs are written—long meta-instructions like 'extract core ideas' match poorly.
    """
    seed = {
        "summary": (
            "main themes central ideas important definitions examples procedures key results "
            "figures tables discussion summary"
        ),
        "explain": (
            "definitions explanations how why examples steps relationships meaning illustrations "
            "process reasoning because therefore"
        ),
        "exam": (
            "definitions concepts formulas distinctions common mistakes cautions key facts "
            "exercises review problems theorems notation"
        ),
    }[mode]
    parts: List[str] = [seed]
    stem = _document_filename_stem(document)
    if stem:
        parts.append(stem)
    skim = _chunk_topic_skim(document)
    if skim:
        parts.append(skim)
    focus = ", ".join([x.strip() for x in (focus_topics or []) if x.strip()][:8])
    challenge = ", ".join([x.strip() for x in (challenge_topics or []) if x.strip()][:6])
    if focus:
        parts.append(focus)
    if challenge:
        parts.append(challenge)
    if level == "beginner":
        parts.append("introduction basics overview intuitive")
    elif level == "technical":
        parts.append("rigorous detailed notation parameters specifics")
    if summary_style == "concise":
        parts.append("highlights essentials")
    elif summary_style == "detailed":
        parts.append("mechanisms distinctions extended discussion")
    if summary_length == "short":
        parts.append("central material")
    elif summary_length == "long":
        parts.append("supporting context additional examples")
    return " ".join(parts).strip()


# fn: _retrieve_relevant_chunks_from_document | tr: belgeden rag ile ilgili chunk'ları getir / en: retrieve relevant chunks from document via rag
def _retrieve_relevant_chunks_from_document(
    document: "StoredDocument",
    mode: SummaryMode,
    focus_topics: Sequence[str],
    challenge_topics: Sequence[str],
    level: SummaryLevel,
    summary_style: str = "balanced",
    summary_length: str = "medium",
) -> List[str]:
    """Use ingest-time chunks + embeddings + FAISS; embed only the retrieval query."""
    from app.services.document_retrieval import retrieve_top_chunk_texts

    query = _build_summary_query(
        mode,
        focus_topics,
        challenge_topics,
        level,
        summary_style=summary_style,
        summary_length=summary_length,
        document=document,
    )
    # top_k widened slightly so retrieval is less likely to miss the right chapter/section.
    top_k_by_mode_length: Dict[Tuple[SummaryMode, str], int] = {
        ("summary", "short"): 4,
        ("summary", "medium"): 5,
        ("summary", "long"): 7,
        ("explain", "short"): 5,
        ("explain", "medium"): 6,
        ("explain", "long"): 8,
        ("exam", "short"): 5,
        ("exam", "medium"): 6,
        ("exam", "long"): 8,
    }
    top_k = top_k_by_mode_length.get(
        (mode, summary_length),
        {"short": 4, "medium": 4, "long": 5}.get(summary_length, 4),
    )
    return retrieve_top_chunk_texts(document, query, top_k=top_k, retrieval_mode=mode)


# fn: _summary_model | tr: ollama özet model adını döndür / en: return ollama summary model name
def _summary_model() -> str:
    return os.getenv("OLLAMA_SUMMARY_MODEL", "llama3.1:8b").strip() or "llama3.1:8b"


# fn: _summary_fallback_ollama_model | tr: explain onarımı için yedek ollama modeli / en: fallback ollama model for explain repair
def _summary_fallback_ollama_model() -> str:
    """Optional second Ollama tag for Explain repair when the primary draft fails section contracts."""
    return os.getenv("OLLAMA_SUMMARY_FALLBACK_MODEL", "").strip()


# fn: _quick_bullet_budget | tr: quick özet madde hedef sayısı / en: quick summary bullet target count
def _quick_bullet_budget(level: SummaryLevel, summary_length: str = "medium") -> int:
    """Quick summary bullet target; hard cap enforced in enforcement."""
    base = 3 if level == "beginner" else 4
    if summary_length == "long":
        base += 2
    elif summary_length == "medium":
        base += 1
    return min(6, max(3, base))


# fn: _quick_summary_context_clean | tr: quick mod için pdf satırlarını düzleştir / en: flatten pdf lines for quick mode context
def _quick_summary_context_clean(text: str) -> str:
    """Mini clean for Quick mode only: flatten broken PDF lines so the model sees steadier prose."""
    t = (text or "").replace("\r", "\n")
    t = re.sub(r"\n+", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


# fn: _quick_bullet_max_words | tr: madde başına maksimum kelime sayısı / en: max words per quick bullet
def _quick_bullet_max_words(level: SummaryLevel, summary_length: str = "medium") -> int:
    """Soft cap per bullet (post-process may trim)."""
    w = 10
    if summary_length == "medium":
        w = 15
    elif summary_length == "long":
        w = 20
    if level == "technical":
        w += 4
    return min(26, w)


# fn: _quick_bullet_max_chars | tr: madde başına maksimum karakter sayısı / en: max characters per quick bullet
def _quick_bullet_max_chars(level: SummaryLevel, summary_length: str = "medium") -> int:
    """Character cap per bullet (Quick Summary)."""
    base = 100
    if summary_length == "medium":
        base = 130
    elif summary_length == "long":
        base = 165
    if level == "beginner":
        return min(200, base - 10)
    if level == "technical":
        return min(220, base + 20)
    return min(210, base)


# fn: _truncate_bullet_words | tr: madde metnini kelime sınırına kırp / en: truncate bullet text to word limit
def _truncate_bullet_words(text: str, *, max_words: int) -> str:
    """Hard cap word count for Quick bullets (post-LLM and fallback paths)."""
    s = (text or "").strip()
    if not s or max_words <= 0:
        return s
    parts = s.split()
    if len(parts) <= max_words:
        return s
    return " ".join(parts[:max_words]).rstrip(" ,;:-")


# fn: _level_block_quick | tr: quick mod seviye kuralları şablonu / en: quick mode level rules prompt block
def _level_block_quick(level: SummaryLevel) -> str:
    if level == "beginner":
        return (
            "LEVEL: BEGINNER (must be obvious)\n"
            "- Very simple English.\n"
            "- Short sentences.\n"
            "- No technical words.\n"
            "- Must look noticeably simpler than Normal.\n"
        )
    if level == "technical":
        return (
            "LEVEL: TECHNICAL (must be obvious)\n"
            "- Use slightly more precise and informative wording.\n"
            "- Minor technical terms are allowed when supported by the PDF.\n"
            "- Keep each bullet short, but more exact than Normal.\n"
        )
    return (
        "LEVEL: NORMAL (must be obvious)\n"
        "- Use clear and slightly more detailed sentences.\n"
        "- Still keep the bullets concise.\n"
        "- Must read more mature than Beginner, but less dense than Technical.\n"
    )


# fn: _level_block_explain | tr: explain mod seviye kuralları şablonu / en: explain mode level rules prompt block
def _level_block_explain(level: SummaryLevel) -> str:
    if level == "beginner":
        return (
            "LEVEL: BEGINNER (must be obvious)\n"
            "- Very simple explanation.\n"
            "- Use everyday language.\n"
            "- Keep the structure easy to follow.\n"
        )
    if level == "technical":
        return (
            "LEVEL: TECHNICAL (must be obvious)\n"
            "- Use a more precise explanation.\n"
            "- Structured reasoning is allowed.\n"
            "- Keep it clear, but more exact than Normal.\n"
        )
    return (
        "LEVEL: NORMAL (must be obvious)\n"
        "- Use a clear explanation with some detail.\n"
        "- Keep the flow simple and understandable.\n"
        "- More detail than Beginner, less precision than Technical.\n"
    )


# fn: _level_block_exam | tr: exam mod seviye kuralları şablonu / en: exam mode level rules prompt block
def _level_block_exam(level: SummaryLevel) -> str:
    if level == "beginner":
        return (
            "LEVEL: BEGINNER (must be obvious)\n"
            "- Very simple definitions and concepts.\n"
            "- Keep wording easy and direct.\n"
        )
    if level == "technical":
        return (
            "LEVEL: TECHNICAL (must be obvious)\n"
            "- Use more precise and structured concepts.\n"
            "- Keep the notes compact but more exact than Normal.\n"
        )
    return (
        "LEVEL: NORMAL (must be obvious)\n"
        "- Use a balanced level of detail.\n"
        "- Keep the notes concise and study-friendly.\n"
    )


# fn: _explain_section_budget | tr: explain bölüm başına madde bütçesi / en: per-section bullet budget for explain mode
def _explain_section_budget(level: SummaryLevel, summary_length: str = "medium") -> Dict[str, int]:
    """Per-section bullet budgets for Explain Simply (enforcement uses these caps)."""
    if level == "beginner":
        base = {"Big Picture": 1, "How It Works": 3, "Example": 1, "Key Takeaway": 1}
    elif level == "technical":
        base = {"Big Picture": 2, "How It Works": 4, "Example": 1, "Key Takeaway": 2}
    else:
        base = {"Big Picture": 1, "How It Works": 3, "Example": 1, "Key Takeaway": 1}
    if summary_length == "long":
        base = {
            **base,
            "Big Picture": min(3, base["Big Picture"] + 1),
            "How It Works": base["How It Works"] + 2,
            "Example": min(2, base["Example"] + 1),
            "Key Takeaway": min(3, base["Key Takeaway"] + 1),
        }
    elif summary_length == "medium":
        base = {
            **base,
            "How It Works": base["How It Works"] + 1,
            "Key Takeaway": min(2, base["Key Takeaway"] + 1),
        }
    return base


# fn: _explain_line_cap | tr: explain satırı maksimum karakter sınırı / en: max character cap per explain line
def _explain_line_cap(level: SummaryLevel, summary_length: str = "medium") -> int:
    cap = 175
    if level == "beginner":
        cap = 145
    elif level == "technical":
        cap = 210
    if summary_length == "long":
        cap += 45
    elif summary_length == "medium":
        cap += 25
    return min(320, cap)


# fn: _exam_line_cap | tr: exam satırı maksimum karakter sınırı / en: max character cap per exam line
def _exam_line_cap(level: SummaryLevel, summary_length: str = "medium") -> int:
    """Exam Focus is intentionally longer than Quick / Explain (denser revision sheet)."""
    cap = 285
    if level == "beginner":
        cap = 230
    elif level == "technical":
        cap = 310
    if summary_length == "long":
        cap += 55
    elif summary_length == "medium":
        cap += 30
    return min(400, cap)


# fn: _exam_section_max_bullets | tr: exam bölümü maksimum madde sayısı / en: max bullets per exam section
def _exam_section_max_bullets(level: SummaryLevel, summary_length: str = "medium") -> int:
    cap = 8
    if level == "beginner":
        cap = 6
    elif level == "technical":
        cap = 10
    if summary_length == "long":
        cap += 6
    elif summary_length == "medium":
        cap += 3
    return min(22, cap)


EXPLAIN_SECTIONS: List[str] = ["Big Picture", "How It Works", "Example", "Key Takeaway"]

EXAM_SECTIONS: List[str] = [
    "Definitions",
    "Key Concepts",
    "Common Mistakes",
    "What to Memorize",
]

GENERIC_LINE_PATTERNS = (
    "this section",
    "this document",
    "the excerpt highlights",
    "the excerpt emphasizes",
    "the passage emphasizes",
    "the passage discusses",
    "the notes connect",
    "the context underlines",
    "the text explains",
    "the material explains",
    "important concept",
    "key concept",
    "core concept",
    "main idea",
)


# fn: _explain_heading_aliases | tr: explain başlık varyantlarını normalize et / en: normalize explain heading variants
def _explain_heading_aliases() -> Dict[str, str]:
    """Normalize model heading variants to canonical EXPLAIN_SECTIONS titles."""
    canon = {s.lower(): s for s in EXPLAIN_SECTIONS}
    extra = {
        "steps / how it works": "How It Works",
        "how it works (steps)": "How It Works",
        "step-by-step": "How It Works",
        "steps": "How It Works",
        "short example": "Example",
        "key takeaway": "Key Takeaway",
        "takeaway": "Key Takeaway",
        "big picture": "Big Picture",
        "example": "Example",
    }
    out = dict(canon)
    for k, v in extra.items():
        out[k] = v
    return out


# fn: _exam_heading_aliases | tr: exam başlık varyantlarını normalize et / en: normalize exam heading variants
def _exam_heading_aliases() -> Dict[str, str]:
    canon = {s.lower(): s for s in EXAM_SECTIONS}
    extra = {
        "common traps / mistakes": "Common Mistakes",
        "common mistakes / traps": "Common Mistakes",
        "common mistakes": "Common Mistakes",
        "traps / mistakes": "Common Mistakes",
        "traps": "Common Mistakes",
        "mistakes": "Common Mistakes",
        "pitfalls": "Common Mistakes",
        "memorize": "What to Memorize",
        "what to memorize": "What to Memorize",
        "memorization": "What to Memorize",
        "memory checklist": "What to Memorize",
        "definition": "Definitions",
        "definitions": "Definitions",
        "key terms": "Definitions",
        "key concepts": "Key Concepts",
        "key ideas": "Key Concepts",
        "core ideas": "Key Concepts",
        "main concepts": "Key Concepts",
        "important ideas": "Key Concepts",
    }
    out = dict(canon)
    for k, v in extra.items():
        out[k] = v
    return out


# fn: _normalize_section_heading_markdown | tr: bölüm başlığı markdown hatalarını düzelt / en: fix section heading markdown typos
def _normalize_section_heading_markdown(text: str) -> str:
    """Fix common LLM heading typos so _collect_section_lines can see sections (##Definitions -> ## Definitions)."""
    t = (text or "").replace("\r\n", "\n")
    t = re.sub(r"^(#{1,6})([^\s#])", r"\1 \2", t, flags=re.MULTILINE)
    return t


# fn: _strip_markdown_list_prefix | tr: madde/numaralı liste önekini kaldır / en: strip bullet or numbered list prefix
def _strip_markdown_list_prefix(text: str) -> str:
    """Remove bullet/numbered list prefixes like '- ', '1-', '1)', '(1)', '1.'."""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[\-\*\+\u2022]\s*", "", s).strip()
    s = re.sub(r"^\(?\d{1,2}\)?\s*[-.)]\s*", "", s).strip()
    s = re.sub(r"^\d{1,2}\s+\-\s*", "", s).strip()
    return s


# fn: _collect_section_lines | tr: markdown metninden bölüm satırlarını topla / en: collect section lines from markdown text
def _collect_section_lines(
    text: str,
    section_titles: Sequence[str],
    *,
    heading_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, List[str]]:
    text = _normalize_section_heading_markdown(text)
    results: Dict[str, List[str]] = {title: [] for title in section_titles}
    current: Optional[str] = None
    title_map: Dict[str, str] = {t.lower(): t for t in section_titles}
    if heading_aliases:
        for k, v in heading_aliases.items():
            title_map[k.strip().lower()] = v
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("### "):
            head = s[4:].strip().lower()
        elif s.startswith("## "):
            head = s[3:].strip().lower()
        else:
            head = ""
        if head:
            head = re.sub(r"\*+", "", head).strip()
            head = head.rstrip(".:•–-").strip()
            current = title_map.get(head)
            continue
        if current is None:
            continue
        s = _strip_markdown_list_prefix(s)
        if s:
            results[current].append(s)
    return results


# fn: _system_prompt_quick_summary | tr: quick özet sistem prompt'unu üret / en: build quick summary system prompt
def _system_prompt_quick_summary(level: SummaryLevel, locale: str, summary_length: str = "medium") -> str:
    _ = locale
    lang = "Write in English."
    n = _quick_bullet_budget(level, summary_length)
    w = _quick_bullet_max_words(level, summary_length)
    return (
        QUICK_PROMPT.strip()
        + "\n\n"
        + f"RUNTIME CAPS FOR THIS REQUEST: at most {n} bullets (never more than 6); "
        f"aim for fluent sentences of roughly {w} words or fewer per bullet (complete sentences, not fragments).\n\n"
        + _study_grounding_block()
        + _mode_output_shape_rules("summary", summary_length)
        + "Output format:\n"
        + "- Each line: `- ` or `•` then one complete sentence (newline between bullets only).\n"
        + "- No inner `•` markers inside a bullet; no merged bullets on one line.\n"
        + "- Avoid long equation lines; if one number or date is essential to the gist, you may keep it in a rewritten phrase.\n"
        + "- No flowchart symbols (`|`, `▼`, `→` chains) used as fake diagrams.\n"
        + "- Say the idea in plain English; finish each thought (no half-phrases).\n\n"
        + f"- If the excerpts are insufficient, output exactly: `{INSUFFICIENT_CONTENT_MSG}`\n\n"
        + _global_summary_quality_rules()
        + "LEVEL RULES:\n"
        + _level_block_quick(level)
        + "\nFINAL CHECK:\n"
        + "- If the text is unclear, rewrite it into a clear idea instead of copying it.\n"
        + "- Proofread every bullet for spelling and normal English.\n\n"
        f"{lang}\n"
    )


# fn: _explain_bullet_contract | tr: explain bölüm madde sözleşmesi şablonu / en: explain section bullet contract template
def _explain_bullet_contract(level: SummaryLevel, summary_length: str) -> str:
    b = _explain_section_budget(level, summary_length)
    bp = b.get("Big Picture", 1)
    hiw = b.get("How It Works", 3)
    ex = b.get("Example", 1)
    kt = b.get("Key Takeaway", 1)
    return (
        f"## Big Picture\n- Use {bp} `-` line(s): what this PDF is centrally about and why it matters.\n\n"
        f"## How It Works\n- Use {hiw} `-` lines: ordered, plain-language steps or claims (one idea per line).\n\n"
        f"## Example\n- Use {ex} `-` line(s): excerpt-grounded mini scenario or contrast (no long quotes).\n\n"
        f"## Key Takeaway\n- Use {kt} `-` line(s): the clearest closing idea the reader should remember.\n\n"
    )


# fn: _system_prompt_explain_simple | tr: explain simply sistem prompt'unu üret / en: build explain simply system prompt
def _system_prompt_explain_simple(level: SummaryLevel, locale: str, summary_length: str = "medium") -> str:
    _ = locale
    lang = "Write in English."
    return (
        EXPLAIN_PROMPT.strip()
        + "\n\n"
        + _study_grounding_block()
        + _mode_output_shape_rules("explain", summary_length)
        + "Map the structure above to Markdown EXACTLY (spelling and order matter):\n\n"
        + _explain_bullet_contract(level, summary_length)
        + "Important:\n"
        + "- Never omit a section and never output an empty heading: all four blocks must contain at least one `- ` line.\n"
        + "- If the text is messy or unclear, FIX it and explain properly—do not paste raw or broken lines.\n"
        + "- Do NOT include broken sentences, half-lines, or notebook noise.\n"
        + "- No code fences; avoid raw equation lines—describe formulas in words when needed.\n"
        + "- Only the four `##` headings above plus `- ` bullets; no extra sections, no `###`/`####` lines, "
        "no decorative titles.\n"
        + f"- If the excerpts are insufficient for a fair explanation, output exactly: `{INSUFFICIENT_CONTENT_MSG}`\n\n"
        + _global_summary_quality_rules()
        + "LEVEL RULES:\n"
        + _level_block_explain(level)
        + "\nOUTPUT:\n"
        + "- Clean four-section Markdown as described.\n"
        + "- Proofread spelling and grammar on every line.\n\n"
        f"{lang}\n"
    )


# fn: _system_prompt_exam_focus | tr: exam focus sistem prompt'unu üret / en: build exam focus system prompt
def _system_prompt_exam_focus(level: SummaryLevel, locale: str, summary_length: str = "medium") -> str:
    _ = locale
    lang = "Write in English."
    lo = _exam_section_lo_targets(level, summary_length)
    hi = _exam_section_hi_targets(level, summary_length)
    return (
        EXAM_PROMPT.strip()
        + "\n\n"
        + _study_grounding_block()
        + _mode_output_shape_rules("exam", summary_length)
        + "### OUTPUT CONTRACT (parsed strictly — headings must match exactly)\n"
        + "Use these four Markdown headings IN THIS ORDER, each on its own line:\n"
        + "## Definitions\n"
        + "## Key Concepts\n"
        + "## Common Mistakes\n"
        + "## What to Memorize\n\n"
        + f"Under the first three headings, aim for roughly {lo}–{hi} short `- ` lines each when the excerpts support it "
        f"(adjust by level: {level}).\n"
        + "Under `## What to Memorize`, first write recall-ready fact bullets, then add **2 or 3** lines that start "
        "with `Exam Q:` followed by a plausible short-answer or MCQ-style stem ending with `?`—each must be answerable "
        "from the excerpts only.\n"
        + "- Definitions: terms and meanings (paraphrased).\n"
        + "- Key Concepts: ideas likely to be tested.\n"
        + "- Common Mistakes: traps or misunderstandings implied by the text.\n"
        + "- What to Memorize: memorize points plus the `Exam Q:` lines described above.\n\n"
        + "Important:\n"
        + "- Stay on ONE coherent main topic from the excerpts; do not mix unrelated concepts in one sheet.\n"
        + "- Ignore messy or incomplete excerpt lines; do not paste them.\n"
        + "- Convert everything you keep into clean, sharp factual lines.\n"
        + "- Do NOT put text before the first `##` line. Do NOT skip a heading. Do NOT add extra sections, "
        "`###` sub-headings, or decorative titles.\n"
        + f"- If the excerpts truly cannot support exam notes, output only this exact line: {INSUFFICIENT_CONTENT_MSG}\n\n"
        + _global_summary_quality_rules()
        + "LEVEL RULES:\n"
        + _level_block_exam(level)
        + "\nOUTPUT:\n"
        + "- Only the four `##` headings and `- ` bullets as described.\n"
        + "- Proofread spelling and grammar on every line.\n\n"
        f"{lang}\n"
    )


# fn: _exam_section_lo_targets | tr: exam bölümü alt madde hedefi / en: exam section lower bullet target
def _exam_section_lo_targets(level: SummaryLevel, summary_length: str) -> int:
    base = {"beginner": 4, "normal": 5, "technical": 6}[level]
    if summary_length == "long":
        return base + 2
    if summary_length == "medium":
        return base + 1
    return max(3, base - 1)


# fn: _exam_section_hi_targets | tr: exam bölümü üst madde hedefi / en: exam section upper bullet target
def _exam_section_hi_targets(level: SummaryLevel, summary_length: str) -> int:
    base = {"beginner": 7, "normal": 9, "technical": 11}[level]
    if summary_length == "long":
        return base + 4
    if summary_length == "medium":
        return base + 2
    return base


# fn: _prompt_for_mode | tr: moda göre sistem prompt'unu seç / en: select system prompt for summary mode
def _prompt_for_mode(mode: SummaryMode, level: SummaryLevel, locale: str, summary_length: str = "medium") -> str:
    """Maps summary→QUICK_PROMPT, explain→EXPLAIN_PROMPT, exam→EXAM_PROMPT (via _system_prompt_* builders)."""
    if mode == "summary":
        return _system_prompt_quick_summary(level, locale, summary_length)
    if mode == "explain":
        return _system_prompt_explain_simple(level, locale, summary_length)
    if mode == "exam":
        return _system_prompt_exam_focus(level, locale, summary_length)
    return _system_prompt_quick_summary(level, locale, summary_length)


# fn: clean_text | tr: özet girişi için gürültülü satırları temizle / en: clean noisy lines from summary input
def clean_text(text: str) -> str:
    lines = text.split("\n")
    clean = []

    for l in lines:
        l = l.strip()

        # boş satır
        if not l:
            continue

        # kod satırlarını sil
        if any(x in l for x in ["=", "plt.", "fig", "ax", "[:,", "()", "def ", "import "]):
            continue

        # çok kısa satır
        if len(l) < 20:
            continue

        clean.append(l)

    return "\n".join(clean)


# fn: clean_output | tr: final özetten kod benzeri satırları ayıkla / en: strip code-like lines from final summary
def clean_output(text: str) -> str:
    """
    Strip obvious code-like lines from final study Markdown.

    Important: do NOT drop normal prose bullets that contain ``=`` (e.g. ``MSE = 2.1``) — those are common
    in Explain/Exam Key Takeaway lines and were being removed entirely, leaving headings with no body.
    """
    lines = text.split("\n")
    result: List[str] = []

    for raw in lines:
        l = raw.strip()
        if not l:
            continue
        is_heading = bool(re.match(r"^#{1,6}\s+\S", l))
        # Models sometimes emit ``-item`` without a space after the marker.
        is_bullet = bool(re.match(r"^[\-*+•]\s*\S", l))
        if is_heading:
            result.append(l)
            continue
        if is_bullet:
            low = l.lower()
            if "import " in low or re.match(r"^\s*def\s+", low) or "plt." in low or "pd." in low or "np." in low:
                continue
            if len(l) < 8:
                continue
            result.append(l)
            continue
        if any(x in l for x in ("=", "plt.", "fig", "ax", "[:" + ",",)):
            continue
        if len(l) < 10:
            continue
        result.append(l)

    return "\n".join(result)


# fn: _explain_repair_if_sections_empty | tr: boş explain bölümlerini yeniden uygula / en: re-enforce explain if sections empty
def _explain_repair_if_sections_empty(
    text: str,
    context_chunks: Sequence[str],
    level: SummaryLevel,
    summary_length: str = "medium",
) -> str:
    """Re-run Explain enforcement if any canonical section lost all bullets (e.g. after ``clean_output``)."""
    stripped = (text or "").strip()
    if not stripped or stripped == INSUFFICIENT_CONTENT_MSG:
        return stripped
    sections = _collect_section_lines(
        stripped, EXPLAIN_SECTIONS, heading_aliases=_explain_heading_aliases()
    )
    if not any(not sections.get(title) for title in EXPLAIN_SECTIONS):
        return stripped
    return _enforce_explain_mode(stripped, context_chunks, level, summary_length)


# fn: _normalize_context_chunk_for_prompt | tr: prompt için chunk başlık gürültüsünü temizle / en: clean chunk heading noise for prompt
def _normalize_context_chunk_for_prompt(chunk: str) -> str:
    """Drop orphan Markdown-only heading lines from PDF extraction noise (keeps real heading+text lines)."""
    out: List[str] = []
    for line in (chunk or "").splitlines():
        if re.match(r"^\s*#+\s*$", line):
            continue
        out.append(line)
    t = "\n".join(out).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


# fn: _retrieved_context_format_rules | tr: alınan bağlam biçim kuralları metni / en: retrieved context format rules text
def _retrieved_context_format_rules() -> str:
    """Short user-side reminder; full contract lives in the system prompt + _study_grounding_block."""
    return (
        "### EXCERPT LAYOUT\n"
        "- Blocks below are separated by a line containing only `---`.\n"
        "- Read each block on its own; combine ideas across blocks only when the text itself links them.\n\n"
    )


# fn: _user_message_for_summary | tr: özet için kullanıcı mesajını oluştur / en: build user message for summary llm
def _user_message_for_summary(joined: str, *, mode: SummaryMode) -> str:
    """User turn: strict grounding on retrieved excerpts (see system prompt for mode contract)."""
    cap = 24000
    body = (joined or "").strip()[:cap]
    mode_hint = ""
    if mode == "summary":
        mode_hint = (
            "MODE: QUICK SUMMARY — shortest mode: up to 4 bullets (`- ` or `•`), about 8–10 words per line, "
            "main ideas only, no digits or formula symbols, no `#`/`##` headings, no decorative titles; proofread spelling.\n\n"
        )
    elif mode == "exam":
        mode_hint = (
            "MODE: EXAM FOCUS — longest/densest mode: exam prep only; paraphrase; exactly four `##` titles in order, "
            "each with `- ` bullets; no `###` or extra section names; proofread spelling.\n\n"
        )
    elif mode == "explain":
        mode_hint = (
            "MODE: EXPLAIN SIMPLY — medium length between Quick and Exam; very simple tutor voice; "
            "exactly four `##` sections in order (Big Picture → How It Works → Example → Key Takeaway), "
            "`- ` bullets only; no formulas, code, or math symbols; no `###` or extra titles; proofread spelling.\n\n"
        )
    return (
        mode_hint
        + "Use ONLY the retrieved context below.\n"
        + "If information is missing, do not invent.\n"
        + f"If the retrieved context is not sufficient, output exactly: {INSUFFICIENT_CONTENT_MSG}\n\n"
        + _retrieved_context_format_rules()
        + body
    )


# fn: _temperature_for_mode | tr: moda göre llm sıcaklık değeri / en: llm temperature value per summary mode
def _temperature_for_mode(mode: SummaryMode) -> float:
    if mode == "explain":
        return float(os.getenv("SUMMARY_TEMPERATURE_EXPLAIN", "0.2").strip() or "0.2")
    if mode == "summary":
        return float(os.getenv("SUMMARY_TEMPERATURE_QUICK", "0.2").strip() or "0.2")
    return float(os.getenv("SUMMARY_TEMPERATURE_EXAM", "0.2").strip() or "0.2")


# fn: _temperature_for_mode_and_level | tr: mod ve seviyeye göre sıcaklık ayarla / en: adjust temperature by mode and level
def _temperature_for_mode_and_level(mode: SummaryMode, level: SummaryLevel) -> float:
    # Quick summary: stable decoding at 0.2 (tutorial default); level tweaks were hurting consistency.
    if mode == "summary":
        t = float(os.getenv("SUMMARY_TEMPERATURE_QUICK", "0.2").strip() or "0.2")
        return max(0.05, min(t, 0.9))
    base = _temperature_for_mode(mode)
    adj = {"beginner": -0.05, "normal": 0.0, "technical": 0.07}[level]
    return max(0.05, min(base + adj, 0.88))


# fn: _effective_cloud_max_tokens | tr: bulut llm için etkin max token / en: effective max tokens for cloud llm
def _effective_cloud_max_tokens(mode: SummaryMode, override: Optional[int]) -> int:
    if override is not None:
        try:
            return max(256, min(int(override), 4096))
        except (TypeError, ValueError):
            pass
    raw = os.getenv("STUDY_MODES_CLOUD_MAX_TOKENS", "900").strip()
    try:
        base = max(256, min(int(raw), 4096))
    except ValueError:
        base = 900
    if mode == "exam":
        ex = os.getenv("STUDY_MODES_CLOUD_MAX_TOKENS_EXAM", "").strip()
        if ex:
            try:
                return max(256, min(int(ex), 4096))
            except ValueError:
                return max(base, 1200)
        return max(base, 1200)
    if mode == "explain":
        exp = os.getenv("STUDY_MODES_CLOUD_MAX_TOKENS_EXPLAIN", "").strip()
        if exp:
            try:
                return max(256, min(int(exp), 4096))
            except ValueError:
                return max(base, 1000)
        return max(base, 1000)
    return base


# fn: _ollama_num_predict_for_mode | tr: ollama num_predict limitini moda göre belirle / en: set ollama num_predict by mode
def _ollama_num_predict_for_mode(mode: SummaryMode, summary_length: str = "medium") -> int:
    if mode == "exam":
        raw = os.getenv("OLLAMA_SUMMARY_NUM_PREDICT_EXAM", "").strip() or os.getenv(
            "OLLAMA_SUMMARY_NUM_PREDICT", "1024"
        ).strip()
    elif mode == "explain":
        raw = os.getenv("OLLAMA_SUMMARY_NUM_PREDICT_EXPLAIN", "").strip() or os.getenv(
            "OLLAMA_SUMMARY_NUM_PREDICT", "1024"
        ).strip()
    else:
        raw = os.getenv("OLLAMA_SUMMARY_NUM_PREDICT", "1024").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 1024
    if mode == "exam":
        n = max(n, 1400)
        if summary_length == "long":
            n = max(n, 1900)
        elif summary_length == "medium":
            n = max(n, 1600)
    elif mode == "explain":
        n = max(n, 1100)
        if summary_length == "long":
            n = max(n, 1500)
        elif summary_length == "medium":
            n = max(n, 1250)
    else:
        if summary_length == "long":
            n = max(n, 1100)
    return max(256, min(n, 4096))


# fn: _llm_generate_summary | tr: bağlamdan llm ile özet üret / en: generate summary via llm from context
def _llm_generate_summary(
    system_prompt: str,
    context_chunks: Sequence[str],
    *,
    mode: SummaryMode,
    level: SummaryLevel,
    summary_length: str = "medium",
    ollama_model: Optional[str] = None,
    local_only: bool = False,
    cloud_only: bool = False,
    cloud_max_tokens: Optional[int] = None,
) -> str:
    parts: List[str] = []
    for ch in context_chunks:
        raw = clean_text(str(ch or "").strip())
        if not raw:
            continue
        if mode == "summary":
            raw = _quick_summary_context_clean(raw)
        parts.append(_normalize_context_chunk_for_prompt(raw))
    joined = "\n\n---\n\n".join(parts)
    if not joined.strip():
        return ""
    user_payload = _user_message_for_summary(joined, mode=mode)

    temp = max(0.0, min(_temperature_for_mode_and_level(mode, level), 0.9))
    cloud_first = os.getenv("STUDY_MODES_CLOUD_FIRST", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    cloud_timeout_raw = os.getenv("STUDY_MODES_CLOUD_TIMEOUT_SECONDS", "75").strip()
    try:
        cloud_timeout = float(cloud_timeout_raw)
    except ValueError:
        cloud_timeout = 75.0
    cloud_timeout = max(20.0, min(cloud_timeout, 180.0))

    cloud_max_tokens_resolved = _effective_cloud_max_tokens(mode, cloud_max_tokens)

    from app.services.ollama_service import ollama_available, ollama_chat_messages

    if cloud_only:
        try:
            from app.services.hybrid_llm_service import cloud_chat_completion, cloud_enabled

            if cloud_enabled():
                cloud_out = cloud_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_payload,
                    temperature=temp,
                    timeout_seconds=cloud_timeout,
                    max_tokens=cloud_max_tokens_resolved,
                )
                if cloud_out:
                    return cloud_out.strip()
        except Exception:
            pass
        return ""

    if local_only:
        if ollama_available():
            num_predict = _ollama_num_predict_for_mode(mode, summary_length)
            model_use = (ollama_model or "").strip() or _summary_model()
            local_out = (
                ollama_chat_messages(
                    [{"role": "user", "content": user_payload}],
                    system=system_prompt,
                    options={"temperature": temp, "num_predict": num_predict},
                    model=model_use,
                )
                or ""
            ).strip()
            if local_out:
                return local_out
        return ""

    try:
        from app.services.hybrid_llm_service import (
            cloud_chat_completion,
            cloud_enabled,
        )

        if cloud_first and cloud_enabled():
            cloud_out = cloud_chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_payload,
                temperature=temp,
                timeout_seconds=cloud_timeout,
                max_tokens=cloud_max_tokens_resolved,
            )
            if cloud_out:
                return cloud_out.strip()
    except Exception:
        pass

    if ollama_available():
        num_predict = _ollama_num_predict_for_mode(mode, summary_length)
        model_use = (ollama_model or "").strip() or _summary_model()
        local_out = (
            ollama_chat_messages(
                [{"role": "user", "content": user_payload}],
                system=system_prompt,
                options={"temperature": temp, "num_predict": num_predict},
                model=model_use,
            )
            or ""
        ).strip()
        if local_out:
            return local_out

    try:
        from app.services.hybrid_llm_service import cloud_chat_completion, cloud_enabled

        if cloud_enabled():
            cloud_out = cloud_chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_payload,
                temperature=temp,
                timeout_seconds=cloud_timeout,
                max_tokens=cloud_max_tokens_resolved,
            )
            if cloud_out:
                return cloud_out.strip()
    except Exception:
        pass
    return ""


# fn: _extract_bullets | tr: metinden madde gövdelerini çıkar / en: extract bullet bodies from text
def _extract_bullets(text: str) -> List[str]:
    out: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        s = re.sub(r"^[\-\*\+\u2022]\s*", "", s)
        if not s:
            continue
        if s.startswith("#"):
            continue
        out.append(s)
    return out


# High-confidence PDF / OCR glues for study summaries (avoid generic "or" splits).
_QUICK_GLUE_TYPO_FIXES: Tuple[Tuple[str, str], ...] = (
    (r"\bimmorl\b", "immoral"),
    (r"\bmorl\b", "moral"),
    (r"\balread\b", "already"),
    (r"\billegalor\b", "illegal or"),
    (r"\blegalor\b", "legal or"),
    (r"\bmoralor\b", "moral or"),
    (r"\bethicalor\b", "ethical or"),
    (r"\btechnicalor\b", "technical or"),
    (r"\bfinancialor\b", "financial or"),
    (r"\bcompany'sbusinessor\b", "company's business or"),
    (r"\bbusinessor\b", "business or"),
    (r"\binformationmeans\b", "information means"),
    (r"\binformationis\b", "information is"),
    (r"\baccordedan\b", "accorded an"),
    (r"\bconflictsofinterest\b", "conflicts of interest"),
    (r"\boverwhich\b", "over which"),
    (r"\binwhich\b", "in which"),
    (r"\bforwhich\b", "for which"),
    (r"\bonwhich\b", "on which"),
    (r"\batwhich\b", "at which"),
    (r"\bwithwhich\b", "with which"),
    (r"\bfromwhich\b", "from which"),
    (r"\bbywhich\b", "by which"),
    (r"\btowhich\b", "to which"),
    (r"\bbefore once can\b", "before one can"),
    (r"\bbefore once\b", "before one"),
)


# Junk partial-line starters often produced before repair / enforcement.
_FORCE_CLEAN_BAD_PREFIXES = (
    "and ",
    "are ",
    "but ",
    "is ",
    "or ",
    "that ",
    "this ",
    "to ",
)


# fn: force_clean_summary | tr: quick özet için agresif satır temizliği / en: aggressive line clean for quick summary
def force_clean_summary(text: str) -> str:
    """
    Aggressive pre-pass on Quick-summary LLM text (same intent as a strict "brutal" clean):
    drop short lines, half-sentence starters, lines without sentence-final punctuation,
    and obvious junk repeats. Keeps at most 8 candidate lines (enforcement still caps at 4).
    If nothing survives, returns empty string so the caller can fall back to the unfiltered text.
    """
    if not (text or "").strip():
        return ""
    clean: List[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[\-\*\+\u2022]\s*", "", line).strip()
        line = re.sub(r"^\d+\.\s*", "", line).strip()
        if len(line) < 25:
            continue
        low = line.lower()
        if any(low.startswith(p) for p in _FORCE_CLEAN_BAD_PREFIXES):
            continue
        if "information information" in low:
            continue
        tail = line.rstrip()
        for _ in range(5):
            if not tail:
                break
            if tail[-1] in "\"'“”":
                tail = tail[:-1].rstrip()
            else:
                break
        if not tail.endswith((".", "!", "?")):
            continue
        clean.append(line)
        if len(clean) >= 8:
            break
    return "\n".join(clean)


# fn: final_filter | tr: quick özet son satır filtresi ve sınırı / en: final line filter and cap for quick summary
def final_filter(text: str, *, max_lines: int = 4) -> str:
    """Post-pass for quick summary: drop short or vague starters; cap bullet lines."""
    lines = (text or "").split("\n")
    clean: List[str] = []
    for l in lines:
        l = l.strip()
        if len(l) < 20:
            continue
        probe = re.sub(r"^[\-\*\+\u2022]\s*", "", l).strip()
        if probe.lower().startswith(("this", "that", "it", "is", "are")):
            continue
        clean.append(l)
    cap = max(3, min(8, int(max_lines)))
    return "\n".join(clean[:cap])


# fn: _split_merged_quick_summary_lines | tr: birleşik quick maddelerini satırlara ayır / en: split merged quick bullets into lines
def _split_merged_quick_summary_lines(text: str) -> str:
    """Turn merged `- a - B` / missing newlines into one Markdown bullet per line for the HTML pipe."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not t:
        return t
    t = re.sub(r"([.!?])\s+-\s+", r"\1\n- ", t)
    t = re.sub(r"([a-z0-9%)])(\s+)-\s+([A-Z][a-z])", r"\1\n- \3", t)
    out: List[str] = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("-") and not s.startswith("- "):
            s = "- " + s[1:].lstrip()
        elif re.match(r"^[*+]\s*\S", s) and not re.match(r"^[*+]\s+\S", s):
            s = s[0] + " " + s[1:].lstrip()
        out.append(s)
    return "\n".join(out)


# fn: _extract_quick_bullet_bodies | tr: quick özet madde gövdelerini ayıkla / en: extract quick summary bullet bodies
def _extract_quick_bullet_bodies(text: str) -> List[str]:
    """Bullet bodies for Quick summary; splits slide-style `Topic • Body` into separate items."""
    bodies: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        s = re.sub(r"^[\-\*\+\u2022]\s*", "", s).strip()
        if not s or s.startswith("#"):
            continue
        inner = re.split(r"\s+[•\u2022]\s+", s)
        if len(inner) >= 2 and all(len(x.strip()) > 15 for x in inner):
            for part in inner:
                p = part.strip()
                if p and not p.startswith("#"):
                    bodies.append(p)
        else:
            bodies.append(s)
    return bodies


# fn: _repair_pdf_word_glue | tr: pdf kelime yapışma hatalarını düzelt / en: repair pdf word glue extraction glitches
def _repair_pdf_word_glue(text: str) -> str:
    """Fix common PDF extraction glitches (possessive stuck to next word, tight punctuation)."""
    t = text or ""
    for pat, rep in _QUICK_GLUE_TYPO_FIXES:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    for _ in range(4):
        prev = t
        t = re.sub(r"([A-Za-z])'s([a-z])", r"\1's \2", t)
        t = re.sub(r"([a-z]),([a-z])", r"\1, \2", t)
        t = re.sub(r"([a-z])\.([A-Z])", r"\1. \2", t)
        if t == prev:
            break
    return t


# fn: _polish_bullet_diagram_prose | tr: madde içi diyagram sembollerini düz metne çevir / en: convert diagram symbols in bullets to prose
def _polish_bullet_diagram_prose(t: str) -> str:
    """
    Replace slide-style diagram scaffolding (pipes, arrows, triangles) with readable prose
    and fix a common run-on (topic label + sentence) missing a colon.
    """
    s = (t or "").strip()
    if not s:
        return s
    # "… Fail on Images Consider a …" (missing colon before the sentence) → "… on Images: Consider a …"
    s = re.sub(
        r"(?i)((?:[A-Z][A-Za-z0-9]*\s+)+on\s+[A-Z][a-z]+)\s+(Consider\s+(?:a|an|the)\b)",
        r"\1: \2 ",
        s,
    )
    # Order: arrows / triangles first, then column pipes (avoids stray "|" next to arrows).
    s = re.sub(r"\s*→\s*", ", then ", s)
    s = re.sub(r"\s*⇒\s*", ", then ", s)
    s = re.sub(r"\s*➜\s*", ", then ", s)
    s = re.sub(r"\s*[▼▲►◄↓↑]\s*", ", then ", s)
    s = re.sub(r"\s*\|\s*", ", ", s)
    for _ in range(5):
        prev = s
        s = re.sub(r",\s*,+", ", ", s)
        s = re.sub(r",\s*then\s*,", ", then", s, flags=re.IGNORECASE)
        s = re.sub(r",\s*then\s*,\s*then", ", then", s, flags=re.IGNORECASE)
        s = re.sub(r"\bthen\s*,\s*then\b", "then", s, flags=re.IGNORECASE)
        if s == prev:
            break
    s = re.sub(r"\s{2,}", " ", s).strip(" ,")
    return s


# fn: _normalize_line | tr: özet satırını biçimlendir ve uzunluğa kırp / en: normalize and truncate summary line
def _normalize_line(s: str, *, max_len: int = 220) -> str:
    t = _polish_bullet_diagram_prose(_repair_pdf_word_glue(s or ""))
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    # Repair split decimals / metrics ("0. 7350", "1. 19") before sentence-punctuation spacing.
    for _ in range(6):
        nt = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", t)
        if nt == t:
            break
        t = nt
    # Space after punctuation only when it is not a decimal separator (digit . digit).
    t = re.sub(r"(?<![0-9])\.(?![0-9])([^\s.])", r". \1", t)
    t = re.sub(r"([;:!?])([^\s])", r"\1 \2", t)
    # Comma: avoid breaking thousand-style "1,234" (comma followed by digit).
    t = re.sub(r",(?![0-9])([^\s])", r", \1", t)
    t = t.strip(" -")
    was_truncated = False
    if len(t) > max_len:
        was_truncated = True
        cut = t[:max_len].rstrip()
        # Prefer a complete sentence boundary over hard truncation.
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("; "))
        if stop > int(max_len * 0.55):
            cut = cut[: stop + 1].rstrip()
        else:
            last_space = cut.rfind(" ")
            if last_space > int(max_len * 0.6):
                cut = cut[:last_space].rstrip()
            elif max_len < len(t) and cut and cut[-1].isalpha() and t[max_len].isalpha():
                # Prevent clipped words like "organizatio" when the hard cap lands mid-word.
                idx = len(cut) - 1
                while idx >= 0 and cut[idx].isalpha():
                    idx -= 1
                if idx >= 0:
                    cut = cut[: idx + 1].rstrip()
        t = cut.rstrip(" ,;:")
    if t and t[-1].isalnum() and not was_truncated:
        t += "."
    return t


# fn: _strip_enumeration_prefix | tr: satır başı numaralandırma önekini kaldır / en: strip leading enumeration prefix
def _strip_enumeration_prefix(s: str) -> str:
    t = (s or "").strip()
    return re.sub(r"^\d+\.\s*", "", t).strip()


# fn: _scrub_quick_summary_literals | tr: quick maddelerden denklem noktalama gürültüsünü sil / en: scrub equation punctuation from quick bullets
def _scrub_quick_summary_literals(text: str) -> str:
    """Remove heavy equation punctuation from Quick bullets; keep occasional numerals when meaningful."""
    t = (text or "").strip()
    t = re.sub(r"[+=×÷≤≥≠]", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.;:-")
    return t


# fn: _format_quick_bullet | tr: quick madde metnini seviyeye göre biçimlendir / en: format quick bullet text for level
def _format_quick_bullet(body: str, level: SummaryLevel, summary_length: str = "medium") -> str:
    cap = _quick_bullet_max_chars(level, summary_length)
    plain = _strip_enumeration_prefix(body)
    plain = re.sub(r"^[\-\*\+\u2022]\s*", "", plain).strip()
    plain = re.sub(r"^([^:]{2,40}):\s+(.+)$", r"\2", plain)
    plain = re.sub(r"\*+", "", plain).strip()
    plain = re.sub(r",\s*\d+\.\s*$", "", plain).strip()
    plain = _scrub_quick_summary_literals(plain)
    plain = _truncate_bullet_words(plain, max_words=_quick_bullet_max_words(level, summary_length))
    out = _normalize_line(plain, max_len=min(cap + 40, 420))
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


# fn: _rewrite_quick_bullet_for_level | tr: quick maddeyi seviyeye göre yeniden yaz / en: rewrite quick bullet for summary level
def _rewrite_quick_bullet_for_level(text: str, level: SummaryLevel, summary_length: str = "medium") -> str:
    t = _strip_enumeration_prefix((text or "").strip())
    t = re.sub(r"^[\-\*\+\u2022]\s*", "", t).strip()
    t = re.sub(r"^(Is|Are)\s+to\s+use\s+", "Use ", t, flags=re.IGNORECASE)
    t = re.sub(r"^([^:]{2,40}):\s+(.+)$", r"\2", t)
    t = re.sub(r"^(this|the)\s+(document|text|passage|section|excerpt)\s+(shows|explains|describes|discusses|highlights|emphasizes)\s+",
               "",
               t,
               flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*\)", "", t)
    t = re.sub(r"\b([A-Za-z][A-Za-z \-]{2,30}):\s+\1\b", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" .;:-")
    if not t:
        return ""
    if _line_looks_incomplete(t):
        return ""
    if level == "beginner":
        t = re.sub(r"\bconcept\b", "idea", t, flags=re.IGNORECASE)
        t = re.sub(r"\bmethod\b", "way", t, flags=re.IGNORECASE)
        t = re.sub(r"\bprocess\b", "way", t, flags=re.IGNORECASE)
        t = re.sub(r"\bfunction\b", "job", t, flags=re.IGNORECASE)
        t = re.sub(r"\bresponse\b", "result", t, flags=re.IGNORECASE)
        t = re.sub(r"\bstructure\b", "form", t, flags=re.IGNORECASE)
        t = re.sub(r"\brelationship\b", "link", t, flags=re.IGNORECASE)
        t = re.sub(r"\bmechanism\b", "way", t, flags=re.IGNORECASE)
        t = re.sub(r"\butilize\b", "use", t, flags=re.IGNORECASE)
        t = re.sub(r"\bapproximately\b", "about", t, flags=re.IGNORECASE)
        t = re.sub(r"\btherefore\b", "so", t, flags=re.IGNORECASE)
        t = re.sub(r"\bhowever\b", "but", t, flags=re.IGNORECASE)
        t = re.sub(r"\bwhile\b", "and", t, flags=re.IGNORECASE)
        t = re.sub(r"\bregulates\b", "controls", t, flags=re.IGNORECASE)
        t = re.sub(r"\bmaintains\b", "keeps", t, flags=re.IGNORECASE)
        t = re.sub(r"\bdetermines\b", "sets", t, flags=re.IGNORECASE)
        t = re.sub(r"\binformed consent\b", "informed consent (clear permission)", t, flags=re.IGNORECASE)
        t = re.sub(r"\bengineering\b", "engineering", t, flags=re.IGNORECASE)
        t = re.sub(r"\bexperimentation\b", "testing", t, flags=re.IGNORECASE)
    elif level == "technical":
        t = re.sub(r"\bkind of\b", "type of", t, flags=re.IGNORECASE)
        t = re.sub(r"\bpart\b", "component", t, flags=re.IGNORECASE)
    return _format_quick_bullet(t, level, summary_length)


# fn: _polish_markdown_syntax | tr: özet markdown sözdizimini düzenle / en: polish summary markdown syntax
def _polish_markdown_syntax(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if not t:
        return ""
    lines: List[str] = []
    for ln in t.split("\n"):
        s = ln.rstrip()
        if not s:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if re.match(r"^\s*#{3,6}\s", s):
            continue
        if re.match(r"^\s*#\s+[^#]", s) or re.match(r"^\s*#\s*$", s):
            continue
        if re.match(r"^[\*\+]\s+", s):
            s = "- " + s[2:].lstrip()
        lines.append(s)
    out = "\n".join(lines)
    out = re.sub(r"(?<!\n)\n(##\s)", r"\n\n\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# fn: _polish_study_syntax | tr: tüm modlar için hafif noktalama düzenlemesi / en: light punctuation polish for all study modes
def _polish_study_syntax(text: str) -> str:
    """Light punctuation/spacing pass on final Markdown (all study modes)."""
    if not (text or "").strip():
        return text or ""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: List[str] = []
    for raw in t.split("\n"):
        s = raw.rstrip()
        if not s.strip():
            out.append("")
            continue
        x = re.sub(r"[ \t]{2,}", " ", s)
        for _ in range(5):
            nx = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", x)
            if nx == x:
                break
            x = nx
        x = re.sub(r"\s+,", ", ", x)
        x = re.sub(r",([A-Za-z])", r", \1", x)
        x = re.sub(r"\.([A-Za-z])", r". \1", x)
        x = re.sub(r"\?([A-Za-z])", r"? \1", x)
        x = re.sub(r"!([A-Za-z])", r"! \1", x)
        x = re.sub(r";([A-Za-z])", r"; \1", x)
        out.append(x)
    joined = "\n".join(out)
    return re.sub(r"\n{4,}", "\n\n\n", joined).strip()


# fn: _sanitize_summary_unicode | tr: özet unicode ve kontrol karakterlerini temizle / en: sanitize summary unicode and control chars
def _sanitize_summary_unicode(text: str) -> str:
    """
    Final guard for API-safe summary text: normalize Unicode and strip glitchy control chars.
    """
    if not (text or "").strip():
        return text or ""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = unicodedata.normalize("NFKC", t)
    t = (
        t.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\ufffd", "")
    )
    t = t.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u2212": "-",
                "\u2026": "...",
            }
        )
    )
    out: List[str] = []
    for ch in t:
        if ch in "\n\t":
            out.append(ch)
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        out.append(ch)
    t = "".join(out)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{4,}", "\n\n\n", t)
    return t.strip()


# fn: _polish_study_spelling | tr: özet metninde yaygın yazım hatalarını düzelt / en: fix common spelling typos in summary text
def _polish_study_spelling(text: str) -> str:
    """Conservative English typo pass on final study-mode Markdown (all modes)."""
    if not (text or "").strip():
        return text or ""
    fixes: Tuple[Tuple[str, str], ...] = (
        (r"\brecieve\b", "receive"),
        (r"\bseperate\b", "separate"),
        (r"\boccured\b", "occurred"),
        (r"\boccurence\b", "occurrence"),
        (r"\bdefinately\b", "definitely"),
        (r"\bacheive\b", "achieve"),
        (r"\bbeleive\b", "believe"),
        (r"\bwierd\b", "weird"),
        (r"\bgoverment\b", "government"),
        (r"\bneccessary\b", "necessary"),
        (r"\baccomodate\b", "accommodate"),
        (r"\boccassion\b", "occasion"),
        (r"\bpublically\b", "publicly"),
        (r"\bproffesional\b", "professional"),
        (r"\bprofesional\b", "professional"),
        (r"\benvironement\b", "environment"),
        (r"\benviroment\b", "environment"),
        (r"\bknowlege\b", "knowledge"),
        (r"\bpersue\b", "pursue"),
        (r"\bsucess\b", "success"),
        (r"\bsuccessfull\b", "successful"),
        (r"\bindependant\b", "independent"),
        (r"\brefered\b", "referred"),
        (r"\bcalender\b", "calendar"),
        (r"\bacheivement\b", "achievement"),
        (r"\barguement\b", "argument"),
        (r"\boccassionally\b", "occasionally"),
        (r"\bwich\b", "which"),
        (r"\bthier\b", "their"),
        (r"\boccuring\b", "occurring"),
        (r"\bpenalised\b", "penalized"),
        (r"\bpenalisation\b", "penalization"),
        (r"\boptimised\b", "optimized"),
        (r"\brecognised\b", "recognized"),
        (r"\bbehaviour\b", "behavior"),
        (r"\b1 features\b", "1 feature"),
        (r"\bone features\b", "one feature"),
        (r"\ba features\b", "a feature"),
        (r"\btestsor\b", "tests or"),
        (r"\btrating\b", "treating"),
        (r"\bcan not\b", "cannot"),
        (r"\bgeneralisati\s+on\b", "generalization"),
        (r"\bperword\b", "per word"),
        (r"\bperclass\b", "per class"),
    )
    t = text
    for pat, rep in fixes:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", t)
    return t


# fn: _dedupe_lines | tr: tekrarlayan özet satırlarını birleştir / en: deduplicate repeated summary lines
def _dedupe_lines(lines: Sequence[str], *, max_items: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in lines:
        s = _normalize_line(raw, max_len=480)
        if not s:
            continue
        key = re.sub(r"[^a-z0-9çğıöşü]+", "", s.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
        if max_items is not None and len(out) >= max_items:
            break
    return out


# fn: _enforce_summary_bullet_mode | tr: quick özet madde sözleşmesini uygula / en: enforce quick summary bullet contract
def _enforce_summary_bullet_mode(
    text: str,
    context_chunks: Sequence[str],
    level: SummaryLevel,
    summary_length: str = "medium",
) -> str:
    stripped = (text or "").strip()
    if stripped == INSUFFICIENT_CONTENT_MSG and not context_chunks:
        return INSUFFICIENT_CONTENT_MSG
    merged = _split_merged_quick_summary_lines(stripped)
    if merged.strip() == INSUFFICIENT_CONTENT_MSG:
        raw = merged
    else:
        scrubbed = force_clean_summary(merged)
        raw = scrubbed if scrubbed.strip() else merged
    target = _quick_bullet_budget(level, summary_length)
    max_out = min(max(target, 3), 6)
    bullets: List[str] = []
    for b in _extract_quick_bullet_bodies(raw):
        clean = _rewrite_quick_bullet_for_level(b, level, summary_length)
        if not clean or _line_looks_incomplete(clean):
            continue
        bullets.append(clean)
        if len(bullets) >= max_out:
            break

    if len(bullets) < target:
        ranked = _rank_context_sentences(
            context_chunks,
            max_items=max(10, target * 4),
            mode="summary",
        )
        for sent in ranked:
            clean = _rewrite_quick_bullet_for_level(sent, level, summary_length)
            if not clean or _line_looks_incomplete(clean):
                continue
            bullets.append(clean)
            if len(bullets) >= max_out:
                break

    if len(bullets) < target:
        for sent in _fallback_lines(context_chunks, target * 3):
            clean = _rewrite_quick_bullet_for_level(sent, level, summary_length)
            if not clean or _line_looks_incomplete(clean):
                continue
            bullets.append(clean)
            if len(bullets) >= max_out:
                break

    min_words = 4 if level == "beginner" else 5
    bullets = [b for b in _dedupe_lines(bullets, max_items=max_out) if len(b.split()) >= min_words]
    if not bullets:
        return INSUFFICIENT_CONTENT_MSG
    line_cap = min(_quick_bullet_max_chars(level, summary_length) + 100, 440)
    pretty = _dedupe_lines(bullets, max_items=max_out)
    joined = "\n".join(f"- {_normalize_line(p, max_len=line_cap)}" for p in pretty)
    return _capitalize_sentence_starts(joined)


# fn: _fallback_lines | tr: bağlamdan yedek cümle satırları çıkar / en: extract fallback sentence lines from context
def _fallback_lines(context_chunks: Sequence[str], target: int) -> List[str]:
    out: List[str] = []
    for ch in context_chunks:
        for sent in re.split(r"(?<=[.!?])\s+", ch):
            s = sent.strip()
            if len(s) >= 30 and not _line_looks_incomplete(s):
                out.append(s)
            if len(out) >= target:
                return out
    if len(out) < target:
        for ch in context_chunks:
            for sent in re.split(r"(?<=[.!?])\s+", ch):
                s = sent.strip()
                if len(s) < 26 or s in out:
                    continue
                if _line_looks_like_metric_or_results_line(s) and not _line_looks_incomplete(s):
                    out.append(s)
                if len(out) >= target:
                    return out
    return out


# fn: _is_generic_line | tr: satır jenerik veya boş mu kontrol et / en: check whether line is generic or empty
def _is_generic_line(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not low:
        return True
    if low in {
        INSUFFICIENT_CONTENT_MSG.lower(),
    }:
        return True
    return any(pat in low for pat in GENERIC_LINE_PATTERNS)


_METRIC_OR_RESULTS_LINE = re.compile(
    r"\b("
    r"r\^?2|r\s*²|mse|rmse|mae|rss|sse|aic|bic|auc|cv\b|std\.?\s*dev|variance|coefficient|intercept|slope|"
    r"penalized|penalised|adjusted|feature|features|train|test|overfit|underfit|lambda|"
    r"residual|degrees?\s+of\s+freedom"
    r")\b",
    re.IGNORECASE,
)


# fn: _line_looks_like_metric_or_results_line | tr: satır metrik/sonuç satırı gibi mi / en: whether line looks like metric or results
def _line_looks_like_metric_or_results_line(s: str) -> bool:
    """True for compact model-metric lines that often have fewer 'words' but are still complete."""
    t = (s or "").strip()
    if len(t) < 16:
        return False
    if not re.search(r"\d", t):
        return False
    if _METRIC_OR_RESULTS_LINE.search(t):
        return True
    return len(re.findall(r"\d+(?:\.\d+)?", t)) >= 3 and len(t) >= 28


# fn: _line_looks_incomplete | tr: satır eksik veya yarım cümle mi / en: whether line looks incomplete or truncated
def _line_looks_incomplete(text: str) -> bool:
    s = _normalize_line(text, max_len=420).strip()
    if not s:
        return True
    low = s.lower()
    if re.match(r"^\d+\)", low):
        return True
    if re.match(r"^\(?[a-d]\)", low):
        return True
    if re.match(r"^(and|or|but|because|so|which|that|with|including|such as)\b", low):
        return True
    metricish = _line_looks_like_metric_or_results_line(s)
    if not metricish:
        if s.count("(") != s.count(")"):
            return True
        if s.count('"') % 2 == 1:
            return True
        if s.endswith((":", ",", ";", "-", "(", "/")):
            return True
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]+", s)
    if len(words) < 4 and not metricish:
        return True
    tail = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+(?:'[A-Za-z]+)?", s.rstrip('.!?"\'“”').strip())
    if tail and tail[-1].lower() in _INCOMPLETE_TAIL_WORDS:
        return True
    if metricish and re.search(r":\s*\d+\.\s*$", s):
        return True
    return False


# fn: _context_has_enough_signal | tr: bağlam özet için yeterli sinyal içeriyor mu / en: whether context has enough signal for summary
def _context_has_enough_signal(context_chunks: Sequence[str]) -> bool:
    ranked = _rank_context_sentences(context_chunks, max_items=6)
    if len(ranked) >= 3:
        return True
    total_chars = sum(len((chunk or "").strip()) for chunk in context_chunks)
    return total_chars >= 450 and len(ranked) >= 2


# fn: _sentence_priority_score | tr: cümle öncelik skorunu hesapla / en: compute sentence priority score
def _sentence_priority_score(text: str, *, mode: Optional[SummaryMode] = None) -> float:
    s = _normalize_line(text, max_len=260)
    low = s.lower()
    score = 0.0
    if _is_generic_line(s):
        score -= 6.0
    ln = len(s)
    if 45 <= ln <= 185:
        score += 1.8
    elif ln < 28:
        score -= 2.0
    elif ln > 235:
        score -= 0.8
    score += 1.8 * len(re.findall(r"\b(is|are|means|refers to|defined as|called)\b", low))
    score += 1.5 * len(re.findall(r"\b(because|therefore|thus|so that|leads to|results in|causes)\b", low))
    score += 1.5 * len(re.findall(r"\b(unlike|whereas|compared to|instead of|rather than|versus|vs)\b", low))
    score += 1.2 * len(re.findall(r"\b(must|should|important|key|core|main|critical|essential)\b", low))
    score += 1.2 * len(re.findall(r"\b(first|second|third|step|stage|process|method|procedure)\b", low))
    score += 0.8 * len(re.findall(r"\d", s))
    if ":" in s:
        score += 0.4
    if s.endswith("?"):
        score -= 1.5
    if mode == "summary":
        score += 1.5 * len(
            re.findall(
                r"\b(purpose|aim|goal|overview|introduces|addresses|focuses on|describes|presents)\b",
                low,
            )
        )
        score -= 1.0 * len(re.findall(r"\b(unlike|versus|vs\.|common mistake|trap|memorize|rote)\b", low))
    elif mode == "explain":
        score += 2.0 * len(re.findall(r"\b(because|therefore|thus|hence|as a result|in order to)\b", low))
        score += 1.3 * len(re.findall(r"\b(example|for instance|such as|illustration|analogy)\b", low))
        score += 1.1 * len(re.findall(r"\b(if|then|when|while|after|before)\b", low))
    elif mode == "exam":
        score += 2.0 * len(re.findall(r"\b(defined as|denoted|notation|symbol|formula|law|theorem)\b", low))
        score += 1.8 * len(re.findall(r"\b(unlike|whereas|versus|vs\.|not the same|distinguish)\b", low))
        score += 1.6 * len(re.findall(r"\b(mistake|confuse|misconception|incorrect|false|avoid|except)\b", low))
        score -= 1.0 * len(re.findall(r"\b(intuitively|in other words|think of it|gentle|friendly)\b", low))
    return score


# fn: _rank_context_sentences | tr: bağlam cümlelerini önceliğe göre sırala / en: rank context sentences by priority
def _rank_context_sentences(
    context_chunks: Sequence[str],
    *,
    max_items: int = 16,
    pattern: Optional[str] = None,
    mode: Optional[SummaryMode] = None,
) -> List[str]:
    candidates: List[Tuple[float, str]] = []
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    for ch in context_chunks:
        for sent in re.split(r"(?<=[.!?])\s+", ch):
            s = _normalize_line(sent, max_len=260)
            if _line_looks_incomplete(s):
                continue
            if re.match(r"^\d+\)", s):
                continue
            if mode != "exam" and ":" in s and len(s.split()) < 6:
                continue
            if len(s) < 30:
                continue
            if rx and not rx.search(s):
                continue
            candidates.append((_sentence_priority_score(s, mode=mode), s))
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    return _dedupe_lines([text for _, text in candidates], max_items=max_items)


# fn: _quality_retry_suffix | tr: kalite yeniden deneme prompt eki / en: quality retry prompt suffix
def _quality_retry_suffix(mode: SummaryMode, level: SummaryLevel, summary_length: str = "medium") -> str:
    if mode == "summary":
        return (
            "\n### QUALITY RETRY\n"
            "- Previous draft was too generic or too broad.\n"
            "- Rebuild it by selecting only the top-priority concepts.\n"
            "- Keep the mode visibly different from Explain mode: bullets only, no teaching, no examples unless central.\n"
            "- No headings or decorative titles; fix spelling and fused words.\n"
            f"- Keep level distinction obvious for `{level}`.\n"
        )
    if mode == "explain":
        return (
            "\n### QUALITY RETRY\n"
            "- Previous draft was too generic, had empty `## Example` / `## Key Takeaway`, or was not tutor-like enough.\n"
            "- Rebuild it around only the most important ideas.\n"
            "- Make mode distinction obvious: teach with why/how reasoning, include grounded example(s), "
            "and keep ONLY the four required `##` sections (no `###`, no extra titles).\n"
            "- Use professional American English; avoid raw code fences—describe formulas in words when needed.\n"
            f"- Keep level distinction obvious for `{level}`.\n"
        )
    exam_extra = ""
    if summary_length == "long":
        exam_extra = (
            "- For **long** length: stretch bullet counts per heading and keep every line testable.\n"
        )
    return (
        "\n### QUALITY RETRY\n"
        "- Previous draft was too generic, too short for Exam Focus, or too similar to Explain mode.\n"
        "- Rebuild it as a true revision sheet with definitions, discriminators, traps, and memorize points.\n"
        "- Make mode distinction obvious: clearly MORE bullets per heading than Explain mode—dense but still bullet-based.\n"
        "- Under `## What to Memorize`, you MUST include **2 or 3** lines starting with `Exam Q:` (question stems ending with `?`).\n"
        f"{exam_extra}"
        "- Use only the four required `##` headings; no `###` or filler section names; proofread spelling.\n"
        f"- Keep level distinction obvious for `{level}`.\n"
    )


# fn: _needs_quality_retry | tr: özet kalitesi yeniden deneme gerektiriyor mu / en: whether summary quality needs retry
def _needs_quality_retry(
    text: str,
    mode: SummaryMode,
    _level: SummaryLevel,
    summary_length: str = "medium",
) -> bool:
    if not (text or "").strip():
        return True
    if (text or "").strip() == INSUFFICIENT_CONTENT_MSG:
        return False
    if mode == "summary":
        bullets = _extract_bullets(text)
        if len(bullets) < 3:
            return True
        generic = sum(1 for b in bullets if _is_generic_line(b))
        incomplete = sum(1 for b in bullets if _line_looks_incomplete(b))
        return generic >= max(2, len(bullets) // 2) or incomplete > 0
    if mode == "explain":
        sections = _collect_section_lines(
            text, EXPLAIN_SECTIONS, heading_aliases=_explain_heading_aliases()
        )
        if any(not sections.get(title) for title in EXPLAIN_SECTIONS):
            return True
        all_lines = [ln for title in EXPLAIN_SECTIONS for ln in sections.get(title, [])]
        generic = sum(1 for ln in all_lines if _is_generic_line(ln))
        incomplete = sum(1 for ln in all_lines if _line_looks_incomplete(ln))
        example_count = len(sections.get("Example", []))
        return generic >= max(2, len(all_lines) // 3) or example_count == 0 or incomplete > 0
    sections = _collect_section_lines(
        text, EXAM_SECTIONS, heading_aliases=_exam_heading_aliases()
    )
    if any(not sections.get(title) for title in EXAM_SECTIONS):
        return True
    all_lines = [ln for title in EXAM_SECTIONS for ln in sections.get(title, [])]
    generic = sum(1 for ln in all_lines if _is_generic_line(ln))
    incomplete = sum(1 for ln in all_lines if _line_looks_incomplete(ln))
    total_lines = len(all_lines)
    min_sec = min(len(sections.get(title, [])) for title in EXAM_SECTIONS)
    min_total = 12
    if summary_length == "long":
        min_total = 18
    elif summary_length == "short":
        min_total = 10
    if min_sec < 2:
        return True
    if total_lines < min_total:
        return True
    if incomplete > 0:
        return True
    mem = sections.get("What to Memorize", []) or []
    exam_qs = [ln for ln in mem if "exam q:" in (ln or "").lower()]
    min_exam_q = 1 if summary_length == "short" else 2
    if len(exam_qs) < min_exam_q:
        return True
    if total_lines < 24 and generic >= max(4, (total_lines + 1) // 2):
        return True
    return False


_EXPLAIN_STUB_HINT = "no additional distinct line remained"


# fn: _explain_requires_alt_generation | tr: explain için alternatif üretim gerekli mi / en: whether explain needs alternate generation
def _explain_requires_alt_generation(text: str, level: SummaryLevel, summary_length: str = "medium") -> bool:
    """True when Explain output still fails contract after primary generation + quality retry + enforcement."""
    t = (text or "").strip()
    if not t or t == INSUFFICIENT_CONTENT_MSG:
        return True
    if _EXPLAIN_STUB_HINT in t.lower():
        return True
    sections = _collect_section_lines(
        t, EXPLAIN_SECTIONS, heading_aliases=_explain_heading_aliases()
    )
    if any(not sections.get(title) for title in EXPLAIN_SECTIONS):
        return True
    return _needs_quality_retry(t, "explain", level, summary_length)


# fn: _explain_alt_pass_prompt_suffix | tr: explain alternatif geçiş prompt eki / en: explain alternate pass prompt suffix
def _explain_alt_pass_prompt_suffix(level: SummaryLevel, summary_length: str = "medium") -> str:
    return (
        "\n### ALTERNATE MODEL / CLOUD REPAIR PASS\n"
        "- Prior output failed the Explain contract (empty section, placeholder stub, or weak structure).\n"
        "- Regenerate from scratch: exactly four `##` headings in order (Big Picture, How It Works, Example, Key Takeaway).\n"
        "- Every heading MUST have at least one substantive `- ` bullet grounded in the excerpts.\n"
    ) + _quality_retry_suffix("explain", level, summary_length)


_EXPLAIN_FALLBACK_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "Big Picture": (
        "overall",
        "goal",
        "purpose",
        "introduces",
        "addresses",
        "focus",
        "main idea",
        "models",
        "learning",
        "regression",
        "prediction",
        "data",
    ),
    "How It Works": (
        "because",
        "therefore",
        "fit",
        "minimize",
        "optimize",
        "gradient",
        "train",
        "test",
        "error",
        "penalty",
        "complex",
        "degree",
        "overfit",
        "underfit",
        "bias",
        "variance",
    ),
    "Example": (
        "for example",
        "for instance",
        "such as",
        "suppose",
        "imagine",
        "figure",
        "plot",
        "when we",
        "if we",
        "dataset",
        "sales",
        "predict",
        "compare",
        "versus",
        "vs",
    ),
    "Key Takeaway": (
        "takeaway",
        "remember",
        "important",
        "best practice",
        "in practice",
        "overall",
        "therefore",
        "avoid",
        "should",
        "must",
        "trade-off",
        "tradeoff",
        "keep in mind",
    ),
}


# fn: _explain_fallback_score | tr: explain bölümü için yedek cümle skoru / en: fallback sentence score for explain section
def _explain_fallback_score(title: str, sentence: str) -> float:
    low = (sentence or "").lower()
    return float(sum(1 for kw in _EXPLAIN_FALLBACK_KEYWORDS.get(title, ()) if kw in low))


# fn: _pick_explain_fallback_lines | tr: explain bölümü için yedek satırları seç / en: pick fallback lines for explain section
def _pick_explain_fallback_lines(
    title: str,
    pool: Sequence[str],
    used_norm: set[str],
    need: int,
) -> List[str]:
    if need <= 0:
        return []
    scored: List[Tuple[float, int, str]] = []
    for s in pool:
        t = (s or "").strip()
        if not t or _line_looks_incomplete(t):
            continue
        nk = re.sub(r"\s+", " ", t.lower())
        if nk in used_norm:
            continue
        scored.append((_explain_fallback_score(title, t), len(t), t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: List[str] = []
    for _, _, t in scored:
        nk = re.sub(r"\s+", " ", t.lower())
        if nk in used_norm:
            continue
        out.append(t)
        used_norm.add(nk)
        if len(out) >= need:
            break
    return out


# fn: _explain_emergency_line | tr: boş explain bölümü için acil satır üret / en: produce emergency line for empty explain section
def _explain_emergency_line(
    context_chunks: Sequence[str],
    *,
    cap: int,
    title: str,
    salt: int = 0,
    used_norm: Optional[set[str]] = None,
) -> str:
    """Last-resort single line from raw excerpts so no Explain section stays empty."""
    parts: List[str] = []
    for ch in context_chunks:
        s = re.sub(r"\s+", " ", str(ch or "").strip())
        if len(s) >= 40:
            parts.append(s[:800])
        if sum(len(x) for x in parts) >= 900:
            break
    blob = " ".join(parts).strip()
    if not blob:
        return (
            "The retrieved excerpts support the points above, but no additional distinct line "
            "remained for this section without repeating earlier bullets."
        )
    un = used_norm or set()
    default = (
        "The retrieved excerpts support the points above, but no additional distinct line "
        "remained for this section without repeating earlier bullets."
    )
    for attempt in range(8):
        stride = max(7, ((sum(ord(c) for c in title) + (salt + attempt) * 23) % 53) + 1)
        start = min(stride * 9 + attempt * 71, max(0, len(blob) - 120))
        frag = blob[start : start + min(len(blob), 720)].strip()
        frag = _normalize_line(frag, max_len=min(cap, 360))
        if len(frag) < 28:
            frag = _normalize_line(blob[: min(len(blob), 720)], max_len=min(cap, 360))
        nk = re.sub(r"\s+", " ", frag.lower())
        if nk not in un:
            return frag
    return default


# fn: _enforce_explain_mode | tr: explain simply çıktı sözleşmesini uygula / en: enforce explain simply output contract
def _enforce_explain_mode(
    text: str,
    context_chunks: Sequence[str],
    level: SummaryLevel,
    summary_length: str = "medium",
) -> str:
    if (text or "").strip() == INSUFFICIENT_CONTENT_MSG and not context_chunks:
        return INSUFFICIENT_CONTENT_MSG
    sections = list(EXPLAIN_SECTIONS)
    budgets = _explain_section_budget(level, summary_length)
    collected = _collect_section_lines(
        text, sections, heading_aliases=_explain_heading_aliases()
    )
    fallback_pool = _rank_context_sentences(context_chunks, max_items=44, mode="explain")
    if not fallback_pool:
        fallback_pool = _fallback_lines(context_chunks, 36)
    cap = _explain_line_cap(level, summary_length)
    used_norm: set[str] = set()
    out: List[str] = []
    for sec_ix, title in enumerate(sections):
        raw_lines = list(collected.get(title, []))
        lines = [ln for ln in raw_lines if not _line_looks_incomplete(ln)]
        for ln in lines:
            used_norm.add(re.sub(r"\s+", " ", ln.strip().lower()))
        budget = budgets.get(title, 2)
        shortfall = max(0, budget - len(lines))
        if shortfall > 0 and fallback_pool:
            lines.extend(
                _pick_explain_fallback_lines(
                    title,
                    fallback_pool,
                    used_norm,
                    min(shortfall, budget),
                )
            )
        if len(lines) < budget and fallback_pool:
            more = _pick_explain_fallback_lines(
                title,
                fallback_pool,
                used_norm,
                max(0, budget - len(lines)),
            )
            lines.extend(more)
        if not lines and fallback_pool:
            for cand in fallback_pool:
                nk = re.sub(r"\s+", " ", cand.strip().lower())
                if nk in used_norm:
                    continue
                if _line_looks_incomplete(cand) and len(cand.strip()) < 42:
                    continue
                lines.append(cand)
                used_norm.add(nk)
                break
        max_items = budget
        if title == "Example":
            max_items = 2 if summary_length == "long" else 1
        elif title == "Key Takeaway":
            max_items = min(3, max(1, budget))
        lines = _dedupe_lines(lines, max_items=max_items)
        if not lines:
            if context_chunks:
                emerg = _explain_emergency_line(
                    context_chunks,
                    cap=cap,
                    title=title,
                    salt=sec_ix,
                    used_norm=used_norm,
                )
                nk_em = re.sub(r"\s+", " ", emerg.lower())
                lines = [emerg]
                used_norm.add(nk_em)
            else:
                return INSUFFICIENT_CONTENT_MSG
        out.append(f"## {title}")
        for ln in lines:
            out.append(f"- {_normalize_line(ln, max_len=cap)}")
        out.append("")
    return "\n".join(out).strip()


_EXAM_FALLBACK_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "Definitions": ("defined as", "means", "denotes", "refers to", "is called", "notation", "symbol"),
    "Key Concepts": ("therefore", "because", "implies", "if ", "when ", "relationship", "compared", "whereas"),
    "Common Mistakes": ("wrong", "incorrect", "avoid", "misconception", "must not", "should not", "confuse", "trap"),
    "What to Memorize": ("remember", "notation", "threshold", "always", "must ", "law", "theorem", "exam q"),
}


# fn: _exam_fallback_score | tr: exam bölümü için yedek cümle skoru / en: fallback sentence score for exam section
def _exam_fallback_score(title: str, sentence: str) -> float:
    low = (sentence or "").lower()
    return float(sum(1 for kw in _EXAM_FALLBACK_KEYWORDS.get(title, ()) if kw in low))


# fn: _pick_exam_fallback_lines | tr: exam bölümü için yedek satırları seç / en: pick fallback lines for exam section
def _pick_exam_fallback_lines(
    title: str,
    pool: Sequence[str],
    used_norm: set[str],
    need: int,
) -> List[str]:
    if need <= 0:
        return []
    scored: List[Tuple[float, int, str]] = []
    for s in pool:
        t = (s or "").strip()
        if not t or _line_looks_incomplete(t):
            continue
        nk = re.sub(r"\s+", " ", t.lower())
        if nk in used_norm:
            continue
        scored.append((_exam_fallback_score(title, t), len(t), t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: List[str] = []
    for _, _, t in scored:
        nk = re.sub(r"\s+", " ", t.lower())
        if nk in used_norm:
            continue
        out.append(t)
        used_norm.add(nk)
        if len(out) >= need:
            break
    return out


# fn: _enforce_exam_mode | tr: exam focus çıktı sözleşmesini uygula / en: enforce exam focus output contract
def _enforce_exam_mode(
    text: str,
    context_chunks: Sequence[str],
    level: SummaryLevel,
    summary_length: str = "medium",
) -> str:
    raw = _normalize_section_heading_markdown((text or "").strip())
    if raw == INSUFFICIENT_CONTENT_MSG and not context_chunks:
        return INSUFFICIENT_CONTENT_MSG
    sections = list(EXAM_SECTIONS)
    collected = _collect_section_lines(
        raw, sections, heading_aliases=_exam_heading_aliases()
    )
    fallback_pool = _rank_context_sentences(context_chunks, max_items=64, mode="exam")
    if not fallback_pool:
        fallback_pool = _fallback_lines(context_chunks, 48)
    cap = _exam_line_cap(level, summary_length)
    max_b = _exam_section_max_bullets(level, summary_length)
    used_norm: set[str] = set()
    out: List[str] = []
    target_fill = max(2, min(4, max(2, (max_b + 1) // 2)))
    for title in sections:
        lines = [ln for ln in list(collected.get(title, [])) if not _line_looks_incomplete(ln)]
        for ln in lines:
            used_norm.add(re.sub(r"\s+", " ", ln.strip().lower()))
        shortfall = max(0, target_fill - len(lines))
        if shortfall > 0 and fallback_pool:
            lines.extend(
                _pick_exam_fallback_lines(
                    title,
                    fallback_pool,
                    used_norm,
                    min(shortfall, max(0, max_b - len(lines))),
                )
            )
        if not lines and fallback_pool:
            lines.extend(_pick_exam_fallback_lines(title, fallback_pool, used_norm, min(2, max_b)))
        if not lines:
            lines = [
                "No separate points for this subsection are clearly supported by the retrieved excerpts alone."
            ]
        lines = _dedupe_lines(lines, max_items=max_b)
        out.append(f"## {title}")
        for ln in lines:
            out.append(f"- {_normalize_line(ln, max_len=cap)}")
        out.append("")
    return "\n".join(out).strip()


# fn: generate_summary | tr: belge için tam dinamik özet hattını çalıştır / en: run full dynamic summary pipeline for document
def generate_summary(
    document: "StoredDocument",
    *,
    mode: str,
    level: str,
    output_locale: str = "en",
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
    summary_style: Optional[str] = None,
    summary_format: Optional[str] = None,
    summary_length: Optional[str] = None,
) -> DynamicSummaryResult:
    """
    Pipeline aligned with: ``clean_text`` on excerpt input, mode-specific system prompts
    (``QUICK_PROMPT`` / ``EXPLAIN_PROMPT`` / ``EXAM_PROMPT``), LLM generation, format enforcement,
    then ``clean_output`` on the final string.

    ``summary_format`` is accepted for API compatibility. ``summary_style`` and ``summary_length``
    steer the retrieval query, top-k, and per-mode chunk scoring so outputs differ by mode/settings.
    Client ``mode`` values like ``quick``, ``quick_summary``, ``explain_simple``, ``exam_focus`` are
    normalized to ``summary`` / ``explain`` / ``exam`` before prompt selection.
    """
    normalized_style = normalize_summary_style(summary_style)
    normalized_length = normalize_summary_length(summary_length)
    normalized_mode = normalize_summary_mode(mode)
    normalized_level = normalize_summary_level(level)

    context_chunks = _retrieve_relevant_chunks_from_document(
        document,
        normalized_mode,
        focus_topics or [],
        challenge_topics or [],
        normalized_level,
        summary_style=normalized_style,
        summary_length=normalized_length,
    )

    if not _context_has_enough_signal(context_chunks):
        fallback_chunks = [str(ch).strip() for ch in context_chunks if str(ch).strip()]
        raw_chunks = getattr(document, "chunks", None) or []
        for ch in raw_chunks:
            s = str(ch).strip()
            if not s:
                continue
            if s not in fallback_chunks:
                fallback_chunks.append(s)
            if len(fallback_chunks) >= 8:
                break
        context_chunks = fallback_chunks[:8]
        if not context_chunks:
            return DynamicSummaryResult(
                text=INSUFFICIENT_CONTENT_MSG,
                mode=normalized_mode,
                level=normalized_level,
                retrieved_chunks=[],
            )

    cleaned_chunks = [clean_text(str(ch or "").strip()) for ch in context_chunks]
    cleaned_chunks = [c for c in cleaned_chunks if c.strip()]
    if cleaned_chunks:
        context_chunks = cleaned_chunks

    system_prompt = _prompt_for_mode(
        normalized_mode, normalized_level, output_locale, normalized_length
    )

    raw = _llm_generate_summary(
        system_prompt,
        context_chunks,
        mode=normalized_mode,
        level=normalized_level,
        summary_length=normalized_length,
    )

    if not raw:
        raw = "\n".join(context_chunks[:4])

    if normalized_mode == "summary":
        final_text = _enforce_summary_bullet_mode(
            raw, context_chunks, normalized_level, normalized_length
        )
    elif normalized_mode == "explain":
        final_text = _enforce_explain_mode(
            raw, context_chunks, normalized_level, normalized_length
        )
    else:
        final_text = _enforce_exam_mode(
            raw, context_chunks, normalized_level, normalized_length
        )

    if normalized_mode == "summary" and _needs_quality_retry(
        final_text, normalized_mode, normalized_level, normalized_length
    ):
        retry_raw = _llm_generate_summary(
            system_prompt + _quality_retry_suffix(
                normalized_mode, normalized_level, normalized_length
            ),
            context_chunks,
            mode=normalized_mode,
            level=normalized_level,
            summary_length=normalized_length,
        )
        if retry_raw:
            retry_text = _enforce_summary_bullet_mode(
                retry_raw, context_chunks, normalized_level, normalized_length
            )
            if not _needs_quality_retry(
                retry_text, normalized_mode, normalized_level, normalized_length
            ):
                final_text = retry_text
    elif normalized_mode in ("explain", "exam") and (
        _needs_quality_retry(
            final_text, normalized_mode, normalized_level, normalized_length
        )
        or (final_text or "").strip() == INSUFFICIENT_CONTENT_MSG
    ):
        retry_raw = _llm_generate_summary(
            system_prompt + _quality_retry_suffix(
                normalized_mode, normalized_level, normalized_length
            ),
            context_chunks,
            mode=normalized_mode,
            level=normalized_level,
            summary_length=normalized_length,
        )
        if retry_raw:
            if normalized_mode == "explain":
                retry_text = _enforce_explain_mode(
                    retry_raw, context_chunks, normalized_level, normalized_length
                )
            else:
                retry_text = _enforce_exam_mode(
                    retry_raw, context_chunks, normalized_level, normalized_length
                )
            if (retry_text or "").strip() and (retry_text or "").strip() != INSUFFICIENT_CONTENT_MSG:
                if not _needs_quality_retry(
                    retry_text, normalized_mode, normalized_level, normalized_length
                ):
                    final_text = retry_text

    if normalized_mode == "explain" and _explain_requires_alt_generation(
        final_text, normalized_level, normalized_length
    ):
        alt_sys = system_prompt + _explain_alt_pass_prompt_suffix(
            normalized_level, normalized_length
        )
        fb = _summary_fallback_ollama_model()
        primary = _summary_model()
        from app.services.ollama_service import ollama_available

        alt_raw = ""
        cand = ""
        if fb and fb.lower() != primary.lower() and ollama_available():
            alt_raw = _llm_generate_summary(
                alt_sys,
                context_chunks,
                mode="explain",
                level=normalized_level,
                summary_length=normalized_length,
                local_only=True,
                ollama_model=fb,
            )
        if alt_raw.strip():
            cand = _enforce_explain_mode(
                alt_raw, context_chunks, normalized_level, normalized_length
            )
        if (
            not cand.strip()
            or cand.strip() == INSUFFICIENT_CONTENT_MSG
            or _explain_requires_alt_generation(cand, normalized_level, normalized_length)
        ):
            try:
                from app.services.hybrid_llm_service import cloud_enabled

                if cloud_enabled():
                    boost = min(4096, _effective_cloud_max_tokens("explain", None) + 500)
                    cloud_raw = _llm_generate_summary(
                        alt_sys,
                        context_chunks,
                        mode="explain",
                        level=normalized_level,
                        summary_length=normalized_length,
                        cloud_only=True,
                        cloud_max_tokens=boost,
                    )
                    if cloud_raw.strip():
                        cand2 = _enforce_explain_mode(
                            cloud_raw, context_chunks, normalized_level, normalized_length
                        )
                        if cand2.strip() and cand2.strip() != INSUFFICIENT_CONTENT_MSG:
                            cand = cand2
            except Exception:
                pass
        if cand.strip() and cand.strip() != INSUFFICIENT_CONTENT_MSG:
            if not _explain_requires_alt_generation(cand, normalized_level, normalized_length):
                final_text = cand
            elif _explain_requires_alt_generation(final_text, normalized_level, normalized_length):
                final_text = cand

    if (
        normalized_mode == "summary"
        and (final_text or "").strip() != INSUFFICIENT_CONTENT_MSG
    ):
        quick_cap = {"short": 4, "medium": 5, "long": 6}.get(normalized_length, 5)
        ff = final_filter(final_text, max_lines=quick_cap)
        if ff.strip():
            final_text = ff

    final_text = _polish_markdown_syntax(final_text)
    from app.services.pdf_text_clean import dedupe_similar_markdown_bullets, final_clean_markdown_output

    final_text = final_clean_markdown_output(final_text)
    if normalized_mode in ("explain", "exam"):
        final_text = dedupe_similar_markdown_bullets(final_text)
    final_text = _polish_study_syntax(final_text)
    final_text = _polish_study_spelling(final_text)
    final_text = _repair_pdf_word_glue(final_text)

    if (final_text or "").strip() != INSUFFICIENT_CONTENT_MSG:
        out_clean = clean_output(final_text)
        if out_clean.strip():
            final_text = out_clean
    if normalized_mode == "explain" and (final_text or "").strip() not in (
        "",
        INSUFFICIENT_CONTENT_MSG,
    ):
        final_text = _explain_repair_if_sections_empty(
            final_text, context_chunks, normalized_level, normalized_length
        )

    final_text = _capitalize_sentence_starts(final_text)
    final_text = _sanitize_summary_unicode(final_text)

    return DynamicSummaryResult(
        text=final_text.strip(),
        mode=normalized_mode,
        level=normalized_level,
        retrieved_chunks=context_chunks,
    )


# fn: build_dynamic_summary | tr: generate_summary için geriye dönük alias / en: backward-compatible alias for generate_summary
def build_dynamic_summary(
    document: "StoredDocument",
    *,
    mode: str,
    level: str,
    output_locale: str = "en",
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
) -> DynamicSummaryResult:
    """Backward-compatible alias for :func:`generate_summary`."""
    return generate_summary(
        document,
        mode=mode,
        level=level,
        output_locale=output_locale,
        focus_topics=focus_topics,
        challenge_topics=challenge_topics,
    )

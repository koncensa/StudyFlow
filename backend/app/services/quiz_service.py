# svc: quiz_service | tr: pdf metninden llm + yedek kurallarla quiz sorusu üret / en: generate quiz questions from pdf text via llm + fallback rules

import hashlib
import json
import logging
import os
import random
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.schemas.quiz_schema import QuizQuestion
from app.services.pdf_text_clean import prepare_text_for_document_summary
from app.services.quiz_prompt_constants import QUIZ_MCQ_SYSTEM_RULES
from app.services.quiz_rule_based import (
    bootstrap_pairs_from_sentences as _bootstrap_pairs_from_sentences,
    build_rule_based_mcq as _build_rule_based_mcq,
    extract_concept_pairs as _extract_concept_pairs,
)
from app.services.quiz_style import (
    polish_quiz_question_style as _polish_quiz_question_style,
    sanitize_quiz_topic_label as _sanitize_quiz_topic_label,
)
from app.services.quiz_validation import (
    ALLOWED_QUESTION_TYPES,
    QuizGenerationError,
    STOP_WORDS,
    TOPIC_QUESTION_TYPES,
    bad_option_text as _bad_option_text,
    clamp_quiz_count as _clamp_quiz_count,
    clip_quiz_visible_text,
    min_stem_key_len as _min_stem_key_len,
    option_len_max,
    normalize_question_type as _normalize_question_type,
    normalize_quiz_options as _normalize_quiz_options,
    normalize_session_difficulty as _normalize_difficulty,
    strip_leading_enumeration_prefix as _strip_leading_enumeration_prefix,
    question_redundant_with_prior_quiz as _question_redundant_with_prior_quiz,
    survivor_collides_with_prior_quiz as _survivor_collides_with_prior_quiz,
    question_stem_dedupe_key as _question_stem_dedupe_key,
    question_stem_key as _question_stem_key,
    resolve_correct_answer_text as _resolve_correct_answer_text,
    validate_quiz_question_complete,
    validate_quiz_question_count_fill,
    validate_quiz_question_strict,
    validate_quiz_question_structural,
    validate_quiz_question_survivor,
    looks_like_broken_math_notation,
    looks_like_code_or_tooling_noise,
)

log = logging.getLogger(__name__)


# fn: _layout_signals_prefix | tr: pdf başlık/vurgu satırlarını llm promptuna ekle / en: add pdf heading/emphasis lines to llm prompt
def _layout_signals_prefix(full_document_text: str) -> str:
    """
    Short block listing likely heading / emphasis lines + repeated slide-like patterns.
    Disabled with QUIZ_LAYOUT_SIGNALS=0|false|off.
    """
    if os.getenv("QUIZ_LAYOUT_SIGNALS", "1").strip().lower() in ("0", "false", "no", "off"):
        return ""
    try:
        from app.services.study_layout_signals import format_layout_signals_for_llm

        mx = int(os.getenv("QUIZ_LAYOUT_SIGNALS_MAX_CHARS", "2600").strip() or "2600")
    except ValueError:
        mx = 2600
    mx = max(600, min(mx, 6000))
    block = format_layout_signals_for_llm(full_document_text or "", max_chars=mx)
    return (block.strip() + "\n\n") if block.strip() else ""


# tr: geriye uyumlu sabit adlar / en: backward-compatible constant aliases
# Backward-compatible names used throughout this module
_ALLOWED_QTYPES = ALLOWED_QUESTION_TYPES
_TOPIC_QUESTION_TYPES = TOPIC_QUESTION_TYPES


# fn: _question_type_cycle_for_difficulty | tr: zorluğa göre soru türü döngüsü / en: question type cycle by difficulty
def _question_type_cycle_for_difficulty(diff: str) -> Tuple[str, ...]:
    if diff == "beginner":
        return ("definition", "concept", "definition", "application", "concept")
    if diff == "technical":
        return ("comparison", "application", "concept", "comparison", "application", "definition")
    return _TOPIC_QUESTION_TYPES


# fn: _difficulty_quiz_contract | tr: zorluk seviyesi talimat metni üret / en: build difficulty level instruction text
def _difficulty_quiz_contract(diff: str) -> str:
    if diff == "beginner":
        return (
            "DIFFICULTY CONTRACT (BEGINNER):\n"
            "- Keep stems simple and direct (no dense wording).\n"
            "- Most items should be definition/recognition or basic concept understanding.\n"
            "- Avoid multi-hop logic chains and subtle trap wording.\n"
        )
    if diff == "technical":
        return (
            "DIFFICULTY CONTRACT (TECHNICAL):\n"
            "- Use deeper reasoning: comparison, application, and condition-based interpretation.\n"
            "- At least some items should require distinguishing close concepts from the text.\n"
            "- Distractors should reflect realistic advanced misconceptions, not obvious wrong facts.\n"
        )
    return (
        "DIFFICULTY CONTRACT (NORMAL):\n"
        "- Use balanced exam difficulty: mix definition, concept, comparison, and light application.\n"
        "- Questions should require understanding, not just keyword recall.\n"
    )


# fn: _difficulty_type_mix_instruction | tr: soru türü karışım hedefi talimatı / en: question type mix target instruction
def _difficulty_type_mix_instruction(diff: str, n: int) -> str:
    total = max(1, int(n))
    if diff == "beginner":
        min_basic = max(2, total // 2)
        return (
            "TYPE MIX TARGET:\n"
            f"- At least {min_basic} items should be definition/concept understanding.\n"
            "- Use at most 1 comparison item unless the text is strongly comparison-heavy.\n"
        )
    if diff == "technical":
        min_advanced = max(2, (total + 1) // 2)
        return (
            "TYPE MIX TARGET:\n"
            f"- At least {min_advanced} items should be comparison or application style.\n"
            "- Include at least 1 question that requires careful distinction between close ideas.\n"
        )
    return (
        "TYPE MIX TARGET:\n"
        "- Keep a balanced spread across definition, concept, comparison, and application.\n"
    )


# fn: _single_question_difficulty_note | tr: tek soru için zorluk notu / en: difficulty note for single question
def _single_question_difficulty_note(diff: str) -> str:
    if diff == "beginner":
        return "Short stem; basic understanding; avoid layered reasoning."
    if diff == "technical":
        return "Requires careful reading and concept distinction; still grounded in one topic."
    return "Fair exam-style difficulty with one clear best answer."


# fn: _quiz_gen_deadline_seconds | tr: quiz üretim zaman aşımı (saniye) / en: quiz generation deadline seconds
def _quiz_gen_deadline_seconds(num_questions: int) -> float:
    """
    Wall-clock budget for one quiz request.
    Scales with requested question count so larger quizzes do not timeout too early.
    """
    try:
        base = float(os.getenv("QUIZ_GEN_DEADLINE_SEC", "240").strip() or "240")
    except ValueError:
        base = 240.0
    n = _clamp_quiz_count(num_questions)
    scaled = base + max(0, n - 5) * 12.0
    return max(base, min(scaled, 900.0))


# fn: _quiz_deadline_check | tr: üretim süresi doldu mu kontrol et / en: check if generation deadline expired
def _quiz_deadline_check(deadline: Optional[float]) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise QuizGenerationError(
            "Quiz üretimi zaman aşımına uğradı. Soru sayısını azaltıp tekrar deneyin."
        )


# fn: _quiz_rulebased_fallback_enabled | tr: kural tabanlı yedek açık mı / en: is rule-based fallback enabled
def _quiz_rulebased_fallback_enabled() -> bool:
    """
    Legacy heuristic MCQs as a resilience fallback when strict LLM output fails.
    Enabled by default as a resilience fallback when strict LLM output fails.
    """
    return os.getenv("QUIZ_ALLOW_RULEBASED_FALLBACK", "1").strip().lower() in ("1", "true", "yes", "on")


_QUIZ_TOPIC_CARD_CACHE: OrderedDict[str, List[Dict[str, str]]] = OrderedDict()
_QUIZ_TOPIC_CACHE_MAX = 48


# fn: _quiz_topic_cache_key | tr: konu kartı önbellek anahtarı / en: topic card cache key
def _quiz_topic_cache_key(document_id: Optional[str], content: str, locale: str) -> Optional[str]:
    if not document_id or not str(document_id).strip():
        return None
    h = hashlib.sha256((content or "").encode("utf-8", errors="ignore")).hexdigest()
    return f"{str(document_id).strip()}|{(locale or 'en').strip().lower()[:8]}|{h}"


# fn: _slim_topic_cards_for_cache | tr: önbellek için konu kartlarını sadeleştir / en: slim topic cards for cache
def _slim_topic_cards_for_cache(cards: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for c in cards:
        out.append(
            {
                "topic": str(c.get("topic") or "").strip()[:120],
                "short_explanation": str(c.get("short_explanation") or "").strip()[:500],
                "key_point": str(c.get("key_point") or "").strip()[:320],
                "formula": "",
                "comparison": "",
            }
        )
    return out


# fn: _quiz_topic_cache_get | tr: önbellekten konu kartlarını al / en: get topic cards from cache
def _quiz_topic_cache_get(key: str) -> Optional[List[Dict[str, str]]]:
    if key not in _QUIZ_TOPIC_CARD_CACHE:
        return None
    blob = _QUIZ_TOPIC_CARD_CACHE.pop(key)
    _QUIZ_TOPIC_CARD_CACHE[key] = blob
    return [dict(c) for c in blob]


# fn: _quiz_topic_cache_set | tr: konu kartlarını önbelleğe yaz / en: set topic cards in cache
def _quiz_topic_cache_set(key: str, cards: List[Dict[str, str]]) -> None:
    if key in _QUIZ_TOPIC_CARD_CACHE:
        del _QUIZ_TOPIC_CARD_CACHE[key]
    _QUIZ_TOPIC_CARD_CACHE[key] = [dict(c) for c in cards]
    while len(_QUIZ_TOPIC_CARD_CACHE) > _QUIZ_TOPIC_CACHE_MAX:
        _QUIZ_TOPIC_CARD_CACHE.popitem(last=False)


# fn: _ollama_quiz_chat | tr: quiz llm tamamlama çağrısı / en: quiz llm completion call
def _ollama_quiz_chat(
    user_prompt: str,
    system: Optional[str] = None,
    *,
    options: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    provider_override: Optional[str] = None,
) -> str:
    from app.services.quiz_langchain_client import quiz_model_generate

    temp = float((options or {}).get("temperature", 0.25))
    to = float(timeout_seconds if timeout_seconds is not None else _quiz_ollama_timeout())
    return quiz_model_generate(
        user_prompt,
        system=system,
        temperature=temp,
        timeout_seconds=to,
        provider_override=provider_override,
    )


# fn: _excerpt_chunk_for_topic | tr: konuya göre metin parçası seç / en: pick text excerpt for topic
def _excerpt_chunk_for_topic(source: str, topic: str, max_chars: int = 2200) -> str:
    """
    Pick a short passage from cleaned source that best matches the topic label (no full PDF in prompts).
    """
    raw = (source or "").strip()
    if not raw:
        return ""
    lab = (topic or "").strip().lower()
    toks = {w for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ0-9]{3,}", lab) if w.lower() not in STOP_WORDS}
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", raw) if len(c.strip()) > 40]
    if not chunks:
        chunks = [raw[:max_chars]]
    scored: List[Tuple[int, str]] = []
    for ch in chunks:
        low = ch.lower()
        score = sum(1 for t in toks if t.lower() in low)
        scored.append((score, ch))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    out_parts: List[str] = []
    total = 0
    for _sc, block in scored[:6]:
        if total >= max_chars:
            break
        take = block[: max_chars - total]
        out_parts.append(take)
        total += len(take)
    joined = "\n\n".join(out_parts).strip()
    if not joined:
        joined = raw[:max_chars]
    return joined[:max_chars]


# fn: _retrieve_topic_chunks | tr: faiss/rag ile konu parçası bul / en: retrieve topic chunks via faiss/rag
def _retrieve_topic_chunks(
    document_id: Optional[str],
    doc_chunks: Optional[List[str]],
    topic_label: str,
    source_fallback: str,
    max_chars: int,
) -> str:
    tid = (document_id or "").strip()
    ch = doc_chunks or []
    if tid and ch:
        try:
            from app.services import quiz_faiss_retrieval

            got = quiz_faiss_retrieval.retrieve_for_topic(
                tid, ch, topic_label, top_k=4, max_chars=max_chars
            )
            if got.strip():
                return got
        except Exception:
            log.debug("quiz FAISS retrieve failed; keyword excerpt fallback", exc_info=True)
    return _excerpt_chunk_for_topic(source_fallback, topic_label, max_chars=max_chars)


# fn: _stratified_pdf_excerpt | tr: pdf baş/orta/son parçalarını birleştir / en: merge pdf head/mid/tail excerpts
def _stratified_pdf_excerpt(source: str, excerpt_cap: int, salt: int) -> str:
    """
    For long PDFs, avoid feeding only the first window to the quiz pipeline: stitch head / mid / tail
    so stems are grounded across the document, not just the opening pages.
    """
    s = (source or "").strip()
    if not s or len(s) <= excerpt_cap:
        return s[:excerpt_cap] if excerpt_cap > 0 else s
    third = max(500, excerpt_cap // 3)
    rem = max(200, excerpt_cap - 2 * third)
    head = s[:third]
    tail = s[-third:]
    n = len(s)
    mid_span = max(1, n - 2 * third)
    start_lo = third
    start_hi = max(start_lo, n - third - rem)
    span = max(1, start_hi - start_lo)
    off = (int(salt) * 4999) % span
    mid_start = start_lo + off
    mid = s[mid_start : mid_start + rem]
    glue = "\n\n[…]\n\n"
    out = (head + glue + mid + glue + tail).strip()
    return out[:excerpt_cap]


# fn: _quiz_ollama_timeout | tr: ollama zaman aşımı süresi / en: ollama timeout duration
def _quiz_ollama_timeout() -> float:
    try:
        return float(os.getenv("OLLAMA_QUIZ_TIMEOUT", "120").strip() or "120")
    except ValueError:
        return 120.0


# fn: _quiz_cloud_ready | tr: bulut llm kullanıma hazır mı / en: is cloud llm ready
def _quiz_cloud_ready() -> bool:
    """True when OpenAI-compatible HYBRID_* env is enabled and API key is present."""
    try:
        from app.services.hybrid_llm_service import cloud_enabled, provider_status

        if not cloud_enabled():
            return False
        return provider_status().get("status") == "ready"
    except Exception:
        return False


# fn: _quiz_quality_cloud_fallback_enabled | tr: kalite için bulut yedeği açık mı / en: cloud fallback for quality enabled
def _quiz_quality_cloud_fallback_enabled() -> bool:
    raw = os.getenv("QUIZ_QUALITY_CLOUD_FALLBACK", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


# fn: _quiz_cloud_fallback_threshold | tr: bulut yedeği tetikleme eşiği / en: cloud fallback trigger threshold
def _quiz_cloud_fallback_threshold(num_questions: int) -> int:
    """
    Minimum accepted LLM yield before asking cloud to rescue quiz quality.
    For small quizzes we still expect most slots from the cheaper local path.
    """
    n = _clamp_quiz_count(num_questions)
    raw = os.getenv("QUIZ_QUALITY_MIN_VALID_RATIO", "0.6").strip()
    try:
        ratio = float(raw)
    except ValueError:
        ratio = 0.6
    ratio = max(0.3, min(ratio, 0.95))
    return max(1, min(n, int(round(n * ratio))))


# fn: _quiz_question_from_dict | tr: json sözlükten QuizQuestion oluştur / en: build QuizQuestion from json dict
def _quiz_question_from_dict(item: dict, *, expected_topic: Optional[str] = None) -> Optional[QuizQuestion]:
    if not isinstance(item, dict):
        return None
    q = item.get("question_text") or item.get("question")
    opts_raw = item.get("options")
    ans_raw = item.get("correct_answer") or item.get("correctAnswer")
    raw_id = item.get("id") or item.get("question_id")
    qid = str(raw_id).strip()[:48] if raw_id is not None else ""
    topic = _sanitize_quiz_topic_label(str(item.get("topic") or expected_topic or "general").strip())
    exp = item.get("explanation") or item.get("short_explanation")
    qtype = _normalize_question_type(item.get("type") or item.get("question_type"))
    diff_item = item.get("difficulty")
    diff_norm = None
    if diff_item is not None and str(diff_item).strip():
        d = str(diff_item).strip().lower()
        if d in ("beginner", "easy", "simple"):
            diff_norm = "beginner"
        elif d in ("technical", "advanced", "hard"):
            diff_norm = "technical"
        elif d in ("normal", "medium", "moderate"):
            diff_norm = "normal"
    if expected_topic:
        topic = _sanitize_quiz_topic_label(str(expected_topic).strip() or topic)
    if len(topic) < 2:
        topic = "general"
    if not isinstance(q, str) or len(q.strip()) < 12:
        return None
    if not isinstance(opts_raw, list) or len(opts_raw) != 4:
        return None
    opts = [re.sub(r"\s+", " ", _strip_leading_enumeration_prefix(str(o).strip())) for o in opts_raw]
    if any(_bad_option_text(o) for o in opts):
        return None
    if len({o.casefold() for o in opts}) != 4:
        return None
    ans_st = _resolve_correct_answer_text(ans_raw, opts)
    if not ans_st or ans_st not in opts:
        return None
    order = opts[:]
    rnd = random.Random(hash(q.strip()) & 0xFFFFFFFF)
    rnd.shuffle(order)
    if ans_st not in order:
        return None
    return QuizQuestion(
        id=qid,
        question_text=q.strip(),
        options=order,
        correct_answer=ans_st,
        topic=str(topic)[:120],
        explanation=(str(exp).strip()[:400] if isinstance(exp, str) and exp.strip() else None),
        source_section=None,
        question_type=qtype,
        difficulty=diff_norm,
    )


# fn: _strip_json_fenced_block | tr: markdown json fence temizle / en: strip markdown json code fence
def _strip_json_fenced_block(text: str) -> str:
    t = (text or "").strip()
    if "```" in t:
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I | re.MULTILINE)
        t = re.sub(r"\s*```\s*$", "", t).strip()
    return t


# fn: _parse_strict_json_value | tr: katı json değerini ayrıştır / en: parse strict json value
def _parse_strict_json_value(raw: str) -> Any:
    """
    Parse one JSON value from model output (after optional ``` fences).

    1) ``json.loads`` on the whole string (strict, best case).
    2) If that fails, ``JSONDecoder.raw_decode`` from each ``{`` / ``[`` — picks the first
       well-formed object or array and ignores leading/trailing prose. This is not the old
       unsafe ``text[first_bracket:last_bracket]`` hack; the decoder validates structure.
    """
    t = _strip_json_fenced_block(raw or "")
    if not t:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch not in "{[":
            continue
        try:
            val, _end = dec.raw_decode(t, i)
            return val
        except json.JSONDecodeError:
            continue
    return None


# fn: _unwrap_quiz_items | tr: json içinden soru listesini çıkar / en: unwrap quiz item list from json
def _unwrap_quiz_items(data: Any) -> Optional[list]:
    """Normalize to a list of question dicts from strict JSON (array or common wrapper keys)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("questions", "quiz", "items", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return inner
    return None


# fn: _parse_conceptual_quiz_json | tr: çoklu soru quiz json ayrıştır / en: parse multi-question quiz json
def _parse_conceptual_quiz_json(raw: str) -> Optional[List[QuizQuestion]]:
    data = _parse_strict_json_value(raw or "")
    items = _unwrap_quiz_items(data)
    if not isinstance(items, list):
        return None
    out: List[QuizQuestion] = []
    for item in items:
        qq = _quiz_question_from_dict(item if isinstance(item, dict) else {}, expected_topic=None)
        if qq:
            out.append(qq)
    return out if out else None


# fn: _parse_single_quiz_question_json | tr: tek soru quiz json ayrıştır / en: parse single quiz question json
def _parse_single_quiz_question_json(raw: str, *, expected_topic: Optional[str] = None) -> Optional[QuizQuestion]:
    data = _parse_strict_json_value(raw or "")
    if data is None:
        return None
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if isinstance(data, dict):
        return _quiz_question_from_dict(data, expected_topic=expected_topic)
    return None


# fn: _prepare_quiz_source_text | tr: quiz kaynak metnini temizle ve kırp / en: clean and clip quiz source text
def _prepare_quiz_source_text(content: str, max_chars: int, *, from_pdf_document: bool = False) -> str:
    """
    Text fed into quiz LLM prompts.

    PDF sessions: ``StoredDocument.study_text`` is already normalized at upload
    (``build_normalized_study_text``). Re-running ``prepare_text_for_document_summary`` plus
    aggressive per-line filters strips real paragraphs and makes questions feel like they were
    written from a thin «summary» slice — so for ``from_pdf_document=True`` we only normalize
    whitespace and cap length.

    Pasted notes / non-document flows still use the summary-oriented cleaner to drop notebook noise.
    """
    raw = (content or "").strip()
    if not raw:
        return ""

    if from_pdf_document:
        t = raw.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"\n{5,}", "\n\n\n\n", t)
        return t[:max_chars]

    cleaned = prepare_text_for_document_summary(raw)
    if not cleaned:
        cleaned = raw
    lines: List[str] = []
    for raw_line in cleaned.split("\n"):
        s = raw_line.strip()
        if not s:
            continue
        sl = s.lower()
        if "[notebook/code sections omitted" in sl:
            continue
        if re.search(r"\b(import|from)\s+[a-zA-Z_][a-zA-Z0-9_.]*", sl):
            continue
        if re.search(r"\b(print|plot|plt\.|sns\.|np\.|pd\.)\b", sl):
            continue
        # Avoid tiny noisy fragments.
        if len(s) < 24 and not re.search(r"[.!?:]$", s):
            continue
        # Avoid very long, likely broken/raw extraction lines.
        if len(s) > 320:
            continue
        # Drop lines dominated by symbols/digits (often broken tables/logs).
        alpha = sum(ch.isalpha() for ch in s)
        if alpha < max(8, int(len(s) * 0.35)):
            continue
        lines.append(s)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out[:max_chars]


# fn: _ollama_conceptual_quiz | tr: llm ile toplu kavramsal quiz üret / en: generate bulk conceptual quiz via llm
def _ollama_conceptual_quiz(
    content: str,
    num_questions: int,
    focus_topics: Optional[Sequence[str]] = None,
    *,
    challenge_topics: Optional[Sequence[str]] = None,
    locale: str = "en",
    difficulty: str = "normal",
    forbidden_stems: Optional[Sequence[str]] = None,
    provider_override: Optional[str] = None,
    from_pdf_document: bool = False,
) -> Optional[List[QuizQuestion]]:
    n = _clamp_quiz_count(num_questions)
    cap = int(os.getenv("QUIZ_OLLAMA_MAX_CHARS", "14000").strip() or "14000")
    cap = max(4000, min(cap, 32000))
    excerpt = _prepare_quiz_source_text(content, cap, from_pdf_document=from_pdf_document)
    if not excerpt.strip():
        excerpt = (content or "")[:cap]
    lang = (locale or "en").strip().lower()
    use_tr = lang.startswith("tr")
    lang_rule = (
        "All question_text and all four options must be in Turkish (Türkçe)."
        if use_tr
        else "All question_text and all four options must be in English."
    )
    diff = _normalize_difficulty(difficulty)
    diff_rule = _difficulty_quiz_contract(diff)
    mix_rule = _difficulty_type_mix_instruction(diff, n)

    system = (
        QUIZ_MCQ_SYSTEM_RULES
        + "You are generating a QUIZ based on a PDF.\n"
        "Your goal is to test UNDERSTANDING, not memorization.\n"
        "Use only the PDF content. Do not invent information.\n"
        "If one idea is too weakly supported, skip it and choose a better-supported concept from the PDF.\n"
        "RULES:\n"
        f"- Generate exactly {n} questions.\n"
        "- Each question must have 4 options: A, B, C, D.\n"
        "- Only ONE answer can be correct.\n"
        "- Avoid obvious or trivial questions.\n"
        "- Do NOT copy sentences directly from the PDF.\n"
        "- Questions must be meaningful and concept-based.\n"
        "- Do NOT break sentences.\n"
        "- Keep grammar clean and natural.\n"
        "- Do NOT reference visuals or UI context like 'the figure/plot below', 'as shown above', 'click Next/Back'.\n"
        "- Each option must be a complete sentence.\n"
        "- Wrong options must be plausible, not random.\n"
        "- Across the whole quiz, do NOT reuse the same correct-answer «shape» (e.g. every item ending the same way "
        "or four options that all start with the same 8-word prefix).\n"
        "- For each question, the four options must start with **different** wording (no copy-paste stem where "
        "only the last few words change).\n"
        "- Each question must test a different fact or angle from the material.\n"
        "- Questions must feel different for each level.\n"
        "- Do NOT generate the same type of questions across levels.\n"
        f"{lang_rule}\n"
        f"{diff_rule}\n"
        "topic field: a short label (2–6 words) naming the concept tested — used to personalize future quizzes."
    )
    focus_block = ""
    if focus_topics:
        cleaned = [str(t).strip() for t in focus_topics if str(t).strip()][:10]
        if cleaned:
            focus_block = (
                "\nREMEDIATION: The learner needs extra practice on these areas — devote most questions here "
                "(spread across items, vary angle): "
                + ", ".join(cleaned)
                + "\n"
            )
    challenge_block = ""
    if challenge_topics:
        ch = [str(t).strip() for t in challenge_topics if str(t).strip()][:8]
        if ch:
            challenge_block = (
                "\nEXTENSION: For concepts related to these stronger areas, include 1–2 harder synthesis or "
                "application questions (still grounded in the material): "
                + ", ".join(ch)
                + "\n"
            )
    forbid = ""
    if forbidden_stems:
        stems = [str(s).strip() for s in forbidden_stems if str(s).strip()][:32]
        if stems:
            forbid = (
                "\n\nSTRICT: Do NOT repeat or trivially paraphrase any of these existing question stems "
                "(each new item must test a distinct idea):\n"
                + "\n".join(f"- {s[:200]}" for s in stems)
            )
    json_syntax = (
        "JSON SYNTAX (machine-parseable; failure invalidates the whole response):\n"
        "- Output one JSON array only — no markdown fences (no ```), no text before or after.\n"
        "- Use straight ASCII double quotes for all keys and string delimiters; no «smart»/curly quotes.\n"
        "- Escape any literal double quote inside Turkish (or other) text as \\\".\n"
        "- No trailing commas; no comments; no NaN/Infinity; no single-quoted strings.\n"
    )
    user = (
        f"Create exactly {n} quiz questions from the material below.\n"
        "Output must follow this structure conceptually for each item: Question, A, B, C, D, Answer.\n"
        "Make the questions concept-based and meaningful.\n"
        "Do not mention figures/plots/tables/images that are above or below the text.\n"
        "Beginner: easy questions and basic understanding.\n"
        "Normal: medium difficulty and concept understanding.\n"
        "Technical: harder questions and deeper thinking.\n"
        f"{mix_rule}\n"
        f"{json_syntax}\n"
        "Return ONLY valid JSON: one JSON array (no markdown fences, no commentary). Each object MUST have exactly these keys:\n"
        'question_text (string), options (array of exactly 4 strings), correctAnswer ("A"|"B"|"C"|"D" only), '
        "explanation (short string), topic (2–6 words).\n"
        "Keep sentences complete, clear, and natural.\n"
        f"{focus_block}{challenge_block}{forbid}\n{_layout_signals_prefix(content)}Material:\n{excerpt}"
    )
    temp = 0.35 if diff == "beginner" else (0.45 if diff == "technical" else 0.4)
    raw = _ollama_quiz_chat(
        user,
        system=system,
        options={"temperature": temp},
        timeout_seconds=_quiz_ollama_timeout(),
        provider_override=provider_override,
    )
    return _parse_conceptual_quiz_json(raw)


# fn: _parse_topic_map_json | tr: konu haritası json ayrıştır / en: parse topic map json
def _parse_topic_map_json(raw: str) -> Optional[List[Dict[str, Any]]]:
    t = (raw or "").strip()
    if not t:
        return None
    if "```" in t:
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I | re.MULTILINE)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return None
    topics = data.get("topics") if isinstance(data, dict) else data
    if not isinstance(topics, list):
        return None
    out: List[Dict[str, Any]] = []
    for it in topics:
        if not isinstance(it, dict):
            continue
        name = _sanitize_quiz_topic_label(str(it.get("topic") or "").strip())
        if len(name) < 2:
            continue
        definition = str(it.get("definition") or "").strip()
        importance = str(it.get("importance") or "").strip()
        relations = it.get("relations") if isinstance(it.get("relations"), list) else []
        formula = str(it.get("formula") or "").strip()[:260]
        comparison = str(it.get("comparison") or it.get("comparison_note") or "").strip()[:260]
        key_point = str(it.get("key_point") or it.get("important_point") or "").strip()[:260]
        if not key_point:
            key_point = importance[:220] if importance else definition[:220]
        out.append(
            {
                "topic": name[:100],
                "definition": definition[:400],
                "importance": importance[:220],
                "key_point": key_point,
                "formula": formula,
                "comparison": comparison,
                "relations": [str(x).strip()[:100] for x in relations if str(x).strip()][:6],
            }
        )
    return out or None


# fn: _parse_topic_labels_json | tr: konu etiketleri json ayrıştır / en: parse topic labels json
def _parse_topic_labels_json(raw: str) -> Optional[List[str]]:
    t = (raw or "").strip()
    if not t:
        return None
    if "```" in t:
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I | re.MULTILINE)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return None
    topics_raw = None
    if isinstance(data, dict):
        topics_raw = data.get("topics")
    elif isinstance(data, list):
        topics_raw = data
    if not isinstance(topics_raw, list):
        return None
    out: List[str] = []
    seen: set[str] = set()
    for it in topics_raw:
        if isinstance(it, str):
            name = it.strip()
        elif isinstance(it, dict):
            name = str(it.get("topic") or it.get("name") or it.get("label") or "").strip()
        else:
            continue
        name = _sanitize_quiz_topic_label(name)
        if len(name) < 2 or len(name) > 90:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(name)
        if len(out) >= 18:
            break
    return out or None


# fn: _ollama_extract_topic_labels | tr: llm ile konu etiketleri çıkar / en: extract topic labels via llm
def _ollama_extract_topic_labels(
    content: str,
    *,
    locale: str,
    max_topics: int,
    from_pdf_document: bool = False,
) -> Optional[List[str]]:
    cap = int(os.getenv("QUIZ_TOPIC_LABELS_MAX_CHARS", "8000").strip() or "8000")
    cap = max(4000, min(cap, 14000))
    excerpt = _prepare_quiz_source_text(content, cap, from_pdf_document=from_pdf_document)
    if not excerpt.strip():
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "Topic names and any glosses must be in Turkish."
        if use_tr
        else "Topic names and any glosses must be in English."
    )
    mt = max(6, min(max_topics, 14))
    system = (
        "You extract exam-relevant CONCEPT TOPICS from one study document (like section headings in a good cheat sheet).\n"
        "Ignore: code blocks, imports, notebook cells, stack traces, plotting calls, CLI commands, URLs, page numbers, "
        "bibliography noise, and anything that is not teachable subject matter.\n"
        "Each topic name must be a clean 2–6 word concept label (e.g. «Logistic regression», «L1 regularisation»). "
        "Do NOT copy slide titles that end with «This», «We create», or dangling «…on»; do NOT paste «Part A — …» lines.\n"
        "Return JSON ONLY: an object {\"topics\": [ ... ]} where each item is either a string (the topic name) or "
        "{\"topic\": \"Name\", \"note\": \"optional 6-12 words why it matters\"}.\n"
        f"Extract {mt} to {min(mt + 4, 14)} distinct topics — short noun phrases (1–5 words), no duplicates.\n"
        f"{lang_rule}\n"
    )
    lay = _layout_signals_prefix(content)
    user = (
        "Read the material and list the main concepts a student would need for an exam.\n"
        "Examples of good topic names: Linear Regression, Gradient Descent, Mean Squared Error, Correlation.\n"
        "Do NOT output topics that are only about Python/pandas/matplotlib unless the document is explicitly a programming tutorial.\n\n"
        f"{lay}"
        f"Material:\n{excerpt}"
    )
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": 0.18}, timeout_seconds=_quiz_ollama_timeout())
    return _parse_topic_labels_json(raw)


# fn: _ollama_extract_topic_map | tr: llm ile zengin konu haritası çıkar / en: extract rich topic map via llm
def _ollama_extract_topic_map(
    content: str,
    *,
    locale: str,
    max_topics: int,
    from_pdf_document: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    cap = int(os.getenv("QUIZ_TOPICMAP_MAX_CHARS", "12000").strip() or "12000")
    cap = max(4000, min(cap, 16000))
    excerpt = _prepare_quiz_source_text(content, cap, from_pdf_document=from_pdf_document)
    if not excerpt.strip():
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "Write all fields in Turkish."
        if use_tr
        else "Write all fields in English."
    )
    system = (
        "You are extracting a concept map from one study document.\n"
        "Remove code/notebook noise mentally and keep only meaningful learning content.\n"
        "Return JSON ONLY.\n"
        f"{lang_rule}"
    )
    user = (
        f"Analyze the full material and extract up to {max(6, min(max_topics, 16))} core topics.\n"
        "Output JSON object with key `topics` as array of objects:\n"
        "{topic, definition, importance, relations}\n"
        "- topic: short concept label\n"
        "- definition: 1 sentence\n"
        "- importance: why exam-relevant\n"
        "- relations: list of related topic labels\n"
        "No markdown, no extra text.\n\n"
        f"{_layout_signals_prefix(content)}"
        f"Material:\n{excerpt}"
    )
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": 0.2}, timeout_seconds=_quiz_ollama_timeout())
    return _parse_topic_map_json(raw)


# fn: _normalize_study_card | tr: çalışma kartını normalize et / en: normalize study card dict
def _normalize_study_card(it: dict) -> Optional[Dict[str, str]]:
    topic = _sanitize_quiz_topic_label(str(it.get("topic") or it.get("title") or "").strip())
    if len(topic) < 2:
        return None
    se = str(it.get("short_explanation") or it.get("definition") or "").strip()
    kp = str(it.get("key_point") or it.get("important_point") or it.get("importance") or "").strip()
    if not kp and se:
        kp = se[:320]
    if not se and kp:
        se = kp[:500]
    formula = str(it.get("formula") or "").strip()
    comp = str(it.get("comparison") or it.get("comparison_note") or "").strip()
    return {
        "topic": topic[:120],
        "short_explanation": se[:500],
        "key_point": kp[:320],
        "formula": formula[:260],
        "comparison": comp[:320],
    }


# fn: _parse_study_cards_json | tr: çalışma kartları json ayrıştır / en: parse study cards json
def _parse_study_cards_json(raw: str) -> Optional[List[Dict[str, str]]]:
    t = _strip_json_fenced_block(raw)
    if not t:
        return None
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return None
    arr = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(arr, list):
        return None
    out: List[Dict[str, str]] = []
    for it in arr:
        if isinstance(it, str):
            s = it.strip()
            if len(s) >= 2:
                out.append(
                    {
                        "topic": s[:120],
                        "short_explanation": "",
                        "key_point": "",
                        "formula": "",
                        "comparison": "",
                    }
                )
            continue
        if isinstance(it, dict):
            card = _normalize_study_card(it)
            if card:
                out.append(card)
    return out or None


# fn: _topic_map_rows_to_rich_cards | tr: konu haritası satırlarını karta çevir / en: convert topic map rows to cards
def _topic_map_rows_to_rich_cards(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for r in rows or []:
        topic = str(r.get("topic") or "").strip()
        if len(topic) < 2:
            continue
        defin = str(r.get("definition") or "").strip()
        kp = str(r.get("key_point") or "").strip()
        imp = str(r.get("importance") or "").strip()
        if not kp:
            kp = imp[:320] if imp else defin[:320]
        se = defin[:500] if defin else (imp[:500] if imp else kp[:500])
        formula = str(r.get("formula") or "").strip()[:260]
        comp = str(r.get("comparison") or "").strip()[:320]
        rels = r.get("relations")
        if not comp and isinstance(rels, list) and len(rels) >= 2:
            a, b = str(rels[0]).strip(), str(rels[1]).strip()
            if a and b:
                comp = f"{a} vs {b}"[:320]
        card = _normalize_study_card(
            {
                "topic": topic,
                "short_explanation": se,
                "key_point": kp or se,
                "formula": formula,
                "comparison": comp,
            }
        )
        if card:
            out.append(card)
    return out


# fn: _merge_study_cards_by_topic | tr: aynı konudaki kartları birleştir / en: merge study cards by topic
def _merge_study_cards_by_topic(
    primary: List[Dict[str, str]],
    secondary: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    by_k: Dict[str, Dict[str, str]] = {}
    for c in primary + secondary:
        t = str(c.get("topic") or "").strip()
        if len(t) < 2:
            continue
        k = t.lower()
        if k not in by_k:
            by_k[k] = dict(c)
            continue
        old = by_k[k]
        for fld in ("short_explanation", "key_point", "formula", "comparison"):
            if not str(old.get(fld) or "").strip():
                v = str(c.get(fld) or "").strip()
                if v:
                    old[fld] = v
    return list(by_k.values())


# fn: _order_rich_topic_cards | tr: konu kartlarını önceliklendirerek sırala / en: order rich topic cards by priority
def _order_rich_topic_cards(
    cards: List[Dict[str, str]],
    focus: Optional[Sequence[str]],
    challenge: Optional[Sequence[str]],
) -> List[Dict[str, str]]:
    if not cards:
        return []
    fl = [str(x).strip().lower() for x in (focus or []) if str(x).strip()]
    ch = [str(x).strip().lower() for x in (challenge or []) if str(x).strip()]

    def score(c: Dict[str, str]) -> Tuple[int, str]:
        t = str(c.get("topic") or "").strip().lower()
        sc = 0
        if any(t == f or f in t or t in f for f in fl):
            sc -= 10
        if any(t == h or h in t or t in h for h in ch):
            sc += 2
        return (sc, t)

    return sorted(cards, key=score)


# fn: _ollama_fill_study_topic_cards | tr: eksik konu kartlarını llm ile doldur / en: fill missing topic cards via llm
def _ollama_fill_study_topic_cards(
    topic_labels: Sequence[str],
    content: str,
    *,
    locale: str,
    from_pdf_document: bool = False,
) -> Optional[List[Dict[str, str]]]:
    labels = [str(x).strip() for x in topic_labels if str(x).strip()][:16]
    if not labels:
        return None
    cap = int(os.getenv("QUIZ_STUDY_CARD_MAX_CHARS", "6500").strip() or "6500")
    cap = max(3500, min(cap, 12000))
    excerpt = _prepare_quiz_source_text(content, cap, from_pdf_document=from_pdf_document)
    if not excerpt.strip():
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = "All user-visible strings must be Turkish." if use_tr else "All user-visible strings must be English."
    system = (
        "You build minimal study cards from a SHORT excerpt. JSON only.\n"
        "For each topic name, one object with keys: topic (same name), short_explanation (1 short sentence), "
        "key_point (one exam-critical sentence). Nothing else.\n"
        "The `topic` string must match one of the input labels (same short concept name) — do not append «This», "
        "«We create», or notebook/slide fragments.\n"
        "Stay inside the excerpt; do not invent facts.\n"
        f"{lang_rule}\n"
        'Return: {"topics": [ ... ]}\n'
    )
    user = (
        "Topics to expand:\n"
        + json.dumps(labels, ensure_ascii=False)
        + "\n\n"
        + _layout_signals_prefix(content)
        + "### Material\n"
        + excerpt
    )
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": 0.2}, timeout_seconds=_quiz_ollama_timeout())
    return _parse_study_cards_json(raw)


# fn: _ollama_extract_rich_topics_one_shot | tr: tek çağrıda zengin konu kartları çıkar / en: extract rich topic cards one shot
def _ollama_extract_rich_topics_one_shot(
    content: str,
    *,
    locale: str,
    max_topics: int,
    from_pdf_document: bool = False,
) -> Optional[List[Dict[str, str]]]:
    cap = int(os.getenv("QUIZ_RICH_TOPICS_MAX_CHARS", "8000").strip() or "8000")
    cap = max(4000, min(cap, 12000))
    excerpt = _prepare_quiz_source_text(content, cap, from_pdf_document=from_pdf_document)
    if not excerpt.strip():
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = "Turkish." if use_tr else "English."
    mt = max(6, min(max_topics, 14))
    system = (
        "Extract exam-relevant topics from a short document excerpt. JSON only.\n"
        f"Return {{\"topics\": [...]}} with {mt} to {min(mt + 2, 12)} objects.\n"
        "Each object: topic, short_explanation (1 sentence), key_point (1 sentence).\n"
        f"Write in {lang_rule}\n"
    )
    user = _layout_signals_prefix(content) + "### Material\n" + excerpt
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": 0.2}, timeout_seconds=_quiz_ollama_timeout())
    return _parse_study_cards_json(raw)


# fn: _ollama_quiz_from_topic_map | tr: konu haritasından quiz üret / en: generate quiz from topic map
def _ollama_quiz_from_topic_map(
    topic_map: List[Dict[str, Any]],
    *,
    num_questions: int,
    locale: str,
    difficulty: str,
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
) -> Optional[List[QuizQuestion]]:
    if not topic_map:
        return None
    n = _clamp_quiz_count(num_questions)
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "All questions/options/explanations must be Turkish."
        if use_tr
        else "All questions/options/explanations must be English."
    )
    diff = _normalize_difficulty(difficulty)
    diff_rule = _difficulty_quiz_contract(diff)
    mix_rule = _difficulty_type_mix_instruction(diff, n)
    focus = [str(x).strip() for x in (focus_topics or []) if str(x).strip()][:8]
    challenge = [str(x).strip() for x in (challenge_topics or []) if str(x).strip()][:8]
    system = (
        QUIZ_MCQ_SYSTEM_RULES
        + "You generate high-quality MCQs from a concept map.\n"
        "No cloze sentence-completion. No placeholder options like 'Option 4'.\n"
        "Each question: one clear stem; exactly **4 complete-sentence** options; exactly one correct answer.\n"
        "Each option must be a **full, natural sentence** — do not compress or truncate wording to hit a short character target; clarity comes first.\n"
        "Each question stem must read as a **complete, unambiguous** sentence (no clipped endings).\n"
        "No code-like syntax in stems/options (e.g., `.loc[...]`, assignments, API calls).\n"
        "Do not reference external visuals/UI context (e.g., figure/plot above-below, click Next/Back).\n"
        "Wrong options: plausible misconceptions in the **same** concept area — never sentence fragments split from one idea.\n"
        f"{lang_rule}\n{diff_rule}\n"
    )
    user = (
        f"Create exactly {n} questions so the full topic map is represented.\n"
        "Use these question types: definition, concept understanding, comparison (A vs B), and application scenario.\n"
        f"{mix_rule}"
        "Each question_text: **one clear complete sentence**; stems must be unambiguous (no clipped or telegraphic stems).\n"
        "Each option: a **full sentence** (not a half-clause); four **distinct** claims; no option may be a substring of another.\n"
        "Do not output code snippets, notebook syntax, or variable-like expressions inside options.\n"
        "explanation: one short sentence tied to the topic map.\n"
        f"Prioritize weak topics when present: {', '.join(focus) if focus else '(none)'}\n"
        f"Include 1-2 harder questions on strong topics when present: {', '.join(challenge) if challenge else '(none)'}\n"
        "Output JSON ONLY: one array of objects with keys:\n"
        "question_text, options (exactly 4 strings), correctAnswer (\"A\"|\"B\"|\"C\"|\"D\" only), explanation, topic\n\n"
        f"Topic map:\n{json.dumps(topic_map, ensure_ascii=False)}"
    )
    raw = _ollama_quiz_chat(
        user, system=system, options={"temperature": 0.3 if diff != "technical" else 0.4}, timeout_seconds=_quiz_ollama_timeout()
    )
    return _parse_conceptual_quiz_json(raw)


# fn: _ollama_quiz_from_topic_labels | tr: konu etiketlerinden quiz üret / en: generate quiz from topic labels
def _ollama_quiz_from_topic_labels(
    topic_labels: Sequence[str],
    excerpt: str,
    *,
    num_questions: int,
    locale: str,
    difficulty: str,
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
    layout_signal_source: str = "",
) -> Optional[List[QuizQuestion]]:
    """
    Quiz pipeline step 2: generate MCQs anchored to an explicit topic list + study text.
    Targets 1–2 questions per topic, capped by num_questions (quality over quantity).
    """
    labels = [str(t).strip() for t in topic_labels if str(t).strip()]
    if not labels:
        return None
    n = _clamp_quiz_count(num_questions)
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "All question_text, options, explanations must be Turkish."
        if use_tr
        else "All question_text, options, explanations must be English."
    )
    diff = _normalize_difficulty(difficulty)
    diff_rule = _difficulty_quiz_contract(diff)
    mix_rule = _difficulty_type_mix_instruction(diff, n)
    focus = [str(x).strip() for x in (focus_topics or []) if str(x).strip()][:8]
    challenge = [str(x).strip() for x in (challenge_topics or []) if str(x).strip()][:8]
    topics_block = "\n".join(f"{i+1}. {lab}" for i, lab in enumerate(labels[:16]))
    system = (
        QUIZ_MCQ_SYSTEM_RULES
        + "### QUIZ GENERATION (topic-driven)\n"
        "You write **professional exam-style** multiple-choice items: clear stems, unambiguous wording, plausible distractors, no chatty filler.\n"
        "Rules:\n"
        "- Each question must clearly test ONE topic from the TOPIC LIST (set `topic` to that exact label when possible).\n"
        "- Cover as many different topics as you can; **at most 2 questions per topic**; **no duplicate stems**.\n"
        "- Do NOT ask about code, imports, notebooks, `print`, plotting, seeds, or syntax unless the document is purely a coding lab.\n"
        "- Do NOT reference visuals/UI around the text (e.g., 'the figure below', 'as shown above', 'click Next').\n"
        "- Every option must be a **complete sentence** (no 'Option A', no half-lines, no comma-splice fragments).\n"
        "- Stems and options must be **complete sentences** with enough words for nuance (roughly 8–28 words per stem, 6–26 per option); avoid padding but **never** truncate a thought for brevity.\n"
        "- Do not include variable-like/code syntax in question_text or options (e.g., `.loc[...]`, `x = ...`, function calls).\n"
        "- Exactly 4 options; one clearly best answer; **correctAnswer** is only \"A\", \"B\", \"C\", or \"D\" (index into options).\n"
        f"- {lang_rule}\n"
        f"- {diff_rule}\n"
    )
    user = (
        f"TOPIC LIST (canonical labels):\n{topics_block}\n\n"
        f"Create exactly {n} questions.\n"
        f"(At most 2 questions per topic; you have {len(labels)} topics — spread coverage, avoid repeats.)\n"
        "Output ONLY valid JSON: one array of objects with keys "
        "question_text, options (array of exactly 4 full-sentence strings), correctAnswer (A–D only), explanation (short), topic.\n"
        "Vary question style using these types: definition, concept understanding, comparison (A vs B), application scenario.\n"
        f"{mix_rule}"
        "Do not aim for artificially short stems or options — keep them readable and **fully stated** (no mid-clause cuts).\n"
        f"Prioritize weak areas when relevant: {', '.join(focus) if focus else '(none)'}\n"
        f"Include 1–2 harder items related to strong areas when relevant: {', '.join(challenge) if challenge else '(none)'}\n\n"
        f"{_layout_signals_prefix(layout_signal_source or excerpt)}"
        "### STUDY TEXT (only source of truth)\n"
        f"{excerpt}"
    )
    cap_ex = int(os.getenv("QUIZ_FROM_TOPICS_MAX_CHARS", "12000").strip() or "12000")
    cap_ex = max(4000, min(cap_ex, 16000))
    if len(user) > cap_ex + 2000:
        user = user[: cap_ex + 2000]
    raw = _ollama_quiz_chat(
        user, system=system, options={"temperature": 0.28 if diff != "technical" else 0.38}, timeout_seconds=_quiz_ollama_timeout()
    )
    return _parse_conceptual_quiz_json(raw)


# fn: _dedupe_questions | tr: tekrarlayan soruları ele / en: remove duplicate questions
def _dedupe_questions(questions: List[QuizQuestion]) -> List[QuizQuestion]:
    out: List[QuizQuestion] = []
    seen_stems: set[str] = set()
    mink = _min_stem_key_len()
    for q in questions:
        stem_raw = _question_stem_key(q.question_text or "")
        if len(stem_raw) < mink:
            continue
        stem = _question_stem_dedupe_key(q.question_text or "")
        if stem in seen_stems:
            continue
        if len(q.options) != 4:
            continue
        if any(_bad_option_text(o) for o in q.options):
            continue
        if _bad_option_text(q.correct_answer):
            continue
        if q.correct_answer not in q.options:
            alt = next((o for o in q.options if o.lower() == (q.correct_answer or "").lower()), None)
            if alt is None:
                continue
            q = q.model_copy(update={"correct_answer": alt})
        seen_stems.add(stem)
        out.append(q)
    return out


# fn: _finalize_quiz_output | tr: quiz çıktısını son haline getir / en: finalize quiz output list
def _finalize_quiz_output(questions: List[QuizQuestion], max_n: int) -> List[QuizQuestion]:
    """Deduplicate, validate, and cap — never pad with low-quality filler."""
    cap = _clamp_quiz_count(max_n)
    cleaned = _dedupe_questions(questions)
    return cleaned[:cap]


# fn: _assign_unique_question_ids | tr: sorulara benzersiz id ata / en: assign unique ids to questions
def _assign_unique_question_ids(questions: List[QuizQuestion]) -> List[QuizQuestion]:
    """Guarantee unique ``id`` values within one quiz payload (stable for UI + submit round-trip)."""
    seen: set[str] = set()
    out: List[QuizQuestion] = []
    for i, q in enumerate(questions):
        qid = (q.id or "").strip()
        if qid and len(qid) >= 8 and qid not in seen:
            seen.add(qid)
            out.append(q.model_copy(update={"id": qid}))
            continue
        salt = 0
        while True:
            base = f"{i}|{salt}|{q.topic}|{q.question_text}|{q.correct_answer}"
            cand = "q_" + hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()[:20]
            salt += 1
            if cand not in seen:
                seen.add(cand)
                out.append(q.model_copy(update={"id": cand}))
                break
    return out


# fn: _ollama_mcq_from_study_card | tr: çalışma kartından tek mcq üret / en: generate one mcq from study card
def _ollama_mcq_from_study_card(
    card: Dict[str, str],
    *,
    qtype: str,
    locale: str,
    difficulty: str,
    forbidden_options: Sequence[str],
    grounding_excerpt: str = "",
    prior_question_stems: Sequence[str] = (),
    from_pdf_document: bool = False,
    layout_signal_block: str = "",
) -> Optional[QuizQuestion]:
    lab = str(card.get("topic") or "").strip()
    if len(lab) < 2:
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "Turkish (Türkçe) for question_text, all four options, short_explanation. "
        "Use complete, grammatical sentences (subject–verb agreement; no half-phrases or broken punctuation)."
        if use_tr
        else "English for question_text, all four options, short_explanation."
    )
    diff = _normalize_difficulty(difficulty)
    diff_note = _single_question_difficulty_note(diff)
    qt = _normalize_question_type(qtype) or "concept"
    type_rules = {
        "definition": "Ask what the term means or identify the best definition for THIS topic only.",
        "concept": "Ask how the idea works, what it implies, or how it relates to itself — not other chapters.",
        "application": "Use a short realistic scenario and ask for the best application of THIS topic only.",
        "comparison": (
            "Use the comparison field when it helps; both ideas must belong to THIS topic. "
            "All four options stay inside this topic."
        ),
    }
    forbid_lines = [f"- {str(x).strip()[:200]}" for x in forbidden_options if str(x).strip()][:24]
    forbid_block = "\n".join(forbid_lines) if forbid_lines else "- (none)"
    prior_lines = [f"- {str(x).strip()[:180]}" for x in prior_question_stems if str(x).strip()][:14]
    prior_block = (
        "\n\nPRIOR_QUESTION_STEMS (learner already saw these; write a clearly different stem and angle):\n"
        + "\n".join(prior_lines)
        if prior_lines
        else ""
    )
    topic_hints = (
        "TOPIC_SCOPE (label + optional hints only — hints may be incomplete):\n"
        f"label: {lab}\n"
        f"hint_short: {card.get('short_explanation') or '(none)'}\n"
        f"hint_key: {card.get('key_point') or '(none)'}\n"
    )
    ground = (grounding_excerpt or "").strip()
    if ground:
        raw_cap = os.getenv("QUIZ_MCQ_GROUNDING_CHARS", "").strip()
        if raw_cap.isdigit():
            cap_g = int(raw_cap)
        else:
            cap_g = 3400 if from_pdf_document else 2200
        cap_g = max(400, min(cap_g, 5200))
        ground = ground[:cap_g]
    type_rule = type_rules[qt]
    system = (
        QUIZ_MCQ_SYSTEM_RULES
        + "You write ONE multiple-choice question for an exam.\n"
        f"STRICT TOPIC LOCK: «{lab}». All four options must be plausible distractors about THIS topic only "
        "(wrong but believable claims, common misconceptions — **not** fragments of one long sentence split across choices).\n"
        f"QUESTION STYLE: {qt}. {type_rule}\n"
        "Exactly 4 options; each option is a **complete sentence**; exactly one correct.\n"
        "Option quality rule: each option must be meaningfully different from the others. "
        "Do not produce four paraphrases of one sentence.\n"
        "Under STRICT TOPIC LOCK, a common failure is four options that are tiny edits of one definition line — "
        "each option must advance a different claim (wrong answers wrong for different reasons), not the same clause with «değil» / «not» toggled.\n"
        f"For QUESTION STYLE «{qt}», still vary how you frame question_text (do not reuse one generic «Which statement…» "
        "opening for every type).\n"
        "Option length rule: each option is a **complete sentence** — clear and self-contained; do not shorten by chopping tails or splitting words.\n"
        "Question length rule: question_text must be a **complete sentence** (no clipped endings).\n"
        "No code-like syntax in question_text/options (e.g., `.loc[...]`, assignments, function calls).\n"
        "Do NOT reveal course structure: no «Part A/B/C», no «Scenario:», no slide or notebook section labels, "
        "no Markdown bold markers — plain sentences only.\n"
        "**correctAnswer** must be exactly \"A\", \"B\", \"C\", or \"D\" — the letter of the correct choice in your **options** array order.\n"
        "No duplicate options; no option may be a substring of another option.\n"
        "OPTION OPENERS: The first 6–12 words of each option must **not** be identical across A–D (no template where "
        "only the tail changes); vary subject and verb so each line is visibly different.\n"
        "PDF TIE: When a PDF passage is provided below, it is the authoritative factual source — verify every stem and "
        "option against it. Topic hints are secondary; if hints conflict with the passage, follow the passage. "
        "Do not invent names, dates, or institutions absent from the passage.\n"
        f"{lang_rule}\n"
        f"{diff_note}\n"
        "JSON SYNTAX: one JSON object only; straight ASCII double quotes; escape internal quotes as \\\"; "
        "no markdown fences; no trailing commas.\n"
        "Output ONLY valid JSON (one object, no markdown): question_text, options (array of exactly 4 strings), "
        "correctAnswer (A|B|C|D), short_explanation (1–2 sentences; why the correct answer fits), "
        f'topic (exact string: "{lab}"), type (exact string: "{qt}").\n'
    )
    lay = (layout_signal_block or "").strip()
    lay_prefix = f"{lay}\n\n" if lay else ""
    pdf_block = (
        f"\n\n### PDF passage — primary source (ground every claim here)\n{ground}\n" if ground else ""
    )
    user = (
        lay_prefix
        + pdf_block
        + topic_hints
        + prior_block
        + "\n\nALREADY_USED_OPTION_TEXTS (do not repeat or trivially paraphrase):\n"
        + forbid_block
    )
    temp = 0.24 if diff == "beginner" else (0.34 if diff == "technical" else 0.28)
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": temp}, timeout_seconds=_quiz_ollama_timeout())
    q = _parse_single_quiz_question_json(raw, expected_topic=lab)
    if q and (q.question_type is None or q.question_type not in _ALLOWED_QTYPES):
        q = q.model_copy(update={"question_type": qt})
    return q


# fn: _run_study_topic_quiz_pipeline | tr: ana konu tabanlı quiz üretim hattı / en: main topic-based quiz generation pipeline
def _run_study_topic_quiz_pipeline(
    *,
    source: str,
    excerpt: str,
    content: str,
    max_content_chars: int,
    n: int,
    max_topics: int,
    focus_topics: Optional[Sequence[str]],
    challenge_topics: Optional[Sequence[str]],
    locale: str,
    difficulty: str,
    document_id: Optional[str] = None,
    deadline_monotonic: Optional[float] = None,
    from_pdf_document: bool = False,
    prior_question_stems: Optional[Sequence[str]] = None,
    generation_salt: int = 0,
) -> Tuple[List[QuizQuestion], List[str]]:
    """
    Two-stage: (1) topic labels + compact study cards, (2) one small LLM call per topic for the MCQ.
    Same document_id reuses cached cards (no re-extraction).
    """
    _quiz_deadline_check(deadline_monotonic)
    cache_key = _quiz_topic_cache_key(document_id, content, locale)
    labels: Optional[List[str]] = None
    cards: List[Dict[str, str]] = []
    doc_chunks: Optional[List[str]] = None
    if document_id:
        from app.services.document_store import get_document

        d0 = get_document(document_id)
        if d0:
            doc_chunks = list(d0.chunks or [])
            qc = getattr(d0, "quiz_topic_cards", None)
            if qc and len(qc) >= 2:
                cards = [dict(c) for c in qc]

    if not cards and cache_key:
        hit = _quiz_topic_cache_get(cache_key)
        if hit:
            cards = list(hit)

    label_cap = max(n, 6, min(max_topics + 4, 16))

    if not cards:
        labels = _ollama_extract_topic_labels(
            source, locale=locale, max_topics=label_cap, from_pdf_document=from_pdf_document
        )
        _quiz_deadline_check(deadline_monotonic)
        if not labels and len(content) > 5000:
            alt_src = _prepare_quiz_source_text(
                content[len(content) // 4 :], max_content_chars, from_pdf_document=from_pdf_document
            )
            if alt_src.strip():
                labels = _ollama_extract_topic_labels(
                    alt_src, locale=locale, max_topics=label_cap, from_pdf_document=from_pdf_document
                )
        _quiz_deadline_check(deadline_monotonic)

        if labels:
            filled = _ollama_fill_study_topic_cards(
                labels, source, locale=locale, from_pdf_document=from_pdf_document
            )
            if filled:
                cards = list(filled)
        _quiz_deadline_check(deadline_monotonic)

        if len(cards) < 2:
            tm = _ollama_extract_topic_map(
                source,
                locale=locale,
                max_topics=max(max_topics * 2, 10),
                from_pdf_document=from_pdf_document,
            )
            alt_cards = _topic_map_rows_to_rich_cards(tm or [])
            cards = _merge_study_cards_by_topic(cards, alt_cards)
            _quiz_deadline_check(deadline_monotonic)

        if len(cards) < 2:
            shot = _ollama_extract_rich_topics_one_shot(
                source,
                locale=locale,
                max_topics=max(label_cap, n + 2),
                from_pdf_document=from_pdf_document,
            )
            if shot:
                cards = _merge_study_cards_by_topic(cards, shot)

        if len(cards) >= 2 and document_id:
            from app.services.document_store import set_document_quiz_topic_cards

            set_document_quiz_topic_cards(document_id, cards)
        if cache_key and len(cards) >= 2:
            _quiz_topic_cache_set(cache_key, _slim_topic_cards_for_cache(cards))

    ordered = _order_rich_topic_cards(cards, focus_topics, challenge_topics)
    all_labels_out = [str(c.get("topic") or "").strip() for c in ordered if str(c.get("topic") or "").strip()]
    if labels is None:
        labels = []
    if not all_labels_out and labels:
        all_labels_out = [str(x).strip() for x in labels if str(x).strip()]

    seen_titles: set[str] = set()
    picked: List[Dict[str, str]] = []
    for c in ordered:
        t = str(c.get("topic") or "").strip()
        if len(t) < 2:
            continue
        k = t.lower()
        if k in seen_titles:
            continue
        seen_titles.add(k)
        picked.append(c)
        if len(picked) >= n:
            break
    # Fewer distinct topics than ``n`` used to cap ``picked`` at 1–2 cards → almost no LLM slots.
    # Cycle through ``ordered`` so we still run ``n`` topic-locked generations (varying qtype per index).
    if len(picked) < n and ordered:
        i = 0
        while len(picked) < n:
            picked.append(dict(ordered[i % len(ordered)]))
            i += 1
            if i > n * 12:
                break

    out: List[QuizQuestion] = []
    forbidden: List[str] = []
    prior_stems_list = [str(s).strip() for s in (prior_question_stems or []) if len(str(s).strip()) >= 8][:20]
    sess_diff = _normalize_difficulty(difficulty)
    qtypes = _question_type_cycle_for_difficulty(sess_diff)
    salt = int(generation_salt or 0) % 1_000_000_007
    if salt and len(qtypes) > 1:
        rqt = salt % len(qtypes)
        qtypes = qtypes[rqt:] + qtypes[:rqt]
    try:
        max_retries = int(os.getenv("QUIZ_CARD_MCQ_RETRIES", "5").strip() or "5")
    except ValueError:
        max_retries = 5
    max_retries = max(1, min(max_retries, 8))
    _chunk_raw = os.getenv("QUIZ_TOPIC_CHUNK_CHARS", "").strip()
    if _chunk_raw.isdigit():
        chunk_cap = max(800, min(int(_chunk_raw), 5000))
    else:
        chunk_cap = max(800, min(3200 if from_pdf_document else 2200, 5000))

    layout_ctx = _layout_signals_prefix(content)

    def _one_mcq_for_card(idx_card: Tuple[int, Dict[str, str]], forbid: Sequence[str]) -> Optional[QuizQuestion]:
        i, card = idx_card
        _quiz_deadline_check(deadline_monotonic)
        qt = qtypes[i % len(qtypes)]
        lab = str(card.get("topic") or "").strip()
        topic_chunk = _retrieve_topic_chunks(document_id, doc_chunks, lab, source, chunk_cap)
        q_fin: Optional[QuizQuestion] = None
        fb = list(forbid[-120:] if forbid else [])
        for _attempt in range(max_retries):
            q = _ollama_mcq_from_study_card(
                card,
                qtype=qt,
                locale=locale,
                difficulty=difficulty,
                forbidden_options=fb,
                grounding_excerpt=topic_chunk,
                prior_question_stems=prior_stems_list,
                from_pdf_document=from_pdf_document,
                layout_signal_block=layout_ctx,
            )
            if not q:
                continue
            q2 = _ensure_topic_label(q, locale)
            q2 = q2.model_copy(update={"difficulty": q2.difficulty or sess_diff})
            if validate_quiz_question_complete(q2, source_grounding=source):
                q_fin = q2
                break
        if q_fin is None:
            for _attempt in range(max(1, max_retries)):
                q = _ollama_one_question_for_topic(
                    lab,
                    topic_chunk or excerpt[:chunk_cap],
                    locale=locale,
                    difficulty=difficulty,
                    qtype=qt,
                    forbidden_options=fb[-96:],
                    prior_question_stems=prior_stems_list,
                    layout_signal_block=layout_ctx,
                )
                if not q:
                    continue
                q2 = _ensure_topic_label(q, locale)
                q2 = q2.model_copy(update={"difficulty": q2.difficulty or sess_diff})
                if validate_quiz_question_complete(q2, source_grounding=source):
                    q_fin = q2
                    break
        if q_fin:
            lab_clean = _sanitize_quiz_topic_label(lab)[:120]
            if lab_clean:
                q_fin = q_fin.model_copy(update={"topic": lab_clean})
        return q_fin

    jobs = list(enumerate(picked))
    if salt and len(jobs) > 1:
        rot = salt % len(jobs)
        jobs = jobs[rot:] + jobs[:rot]
    # Sequential generation only: parallel workers previously each saw an empty
    # forbidden-options list, so şıklar duplicated across questions and scoring broke.
    for jc in jobs:
        q_fin = _one_mcq_for_card(jc, forbidden)
        if q_fin:
            out.append(q_fin)
            forbidden.extend(list(q_fin.options or []))

    missing_topics = [
        str(c.get("topic") or "").strip()
        for c in picked
        if str(c.get("topic") or "").strip()
        and not any(
            (q.topic or "").strip().lower() == str(c.get("topic") or "").strip().lower() for q in out
        )
    ]
    if missing_topics and len(out) < n:
        _quiz_deadline_check(deadline_monotonic)
        fb2: List[str] = []
        for q in out:
            fb2.extend(list(q.options or []))
        for lab in missing_topics:
            _quiz_deadline_check(deadline_monotonic)
            if len(out) >= n:
                break
            chunk = _retrieve_topic_chunks(document_id, doc_chunks, lab, source, chunk_cap)
            qt = qtypes[len(out) % len(qtypes)]
            q_fin: Optional[QuizQuestion] = None
            for _attempt in range(max_retries):
                q = _ollama_one_question_for_topic(
                    lab,
                    chunk or excerpt[:chunk_cap],
                    locale=locale,
                    difficulty=difficulty,
                    qtype=qt,
                    forbidden_options=fb2[-96:],
                    prior_question_stems=prior_stems_list,
                    layout_signal_block=layout_ctx,
                )
                if not q:
                    continue
                q2 = _ensure_topic_label(q, locale)
                q2 = q2.model_copy(update={"difficulty": q2.difficulty or sess_diff})
                if validate_quiz_question_complete(q2, source_grounding=source):
                    q_fin = q2
                    break
            if q_fin:
                lab_clean = _sanitize_quiz_topic_label(lab)[:120]
                if lab_clean:
                    q_fin = q_fin.model_copy(update={"topic": lab_clean})
                out.append(q_fin)
                fb2.extend(list(q_fin.options or []))

    return out, all_labels_out


# fn: _ollama_one_question_for_topic | tr: tek konu için llm ile soru üret / en: generate one llm question for topic
def _ollama_one_question_for_topic(
    topic_label: str,
    excerpt: str,
    *,
    locale: str,
    difficulty: str,
    qtype: str,
    forbidden_options: Sequence[str],
    prior_question_stems: Sequence[str] = (),
    layout_signal_block: str = "",
) -> Optional[QuizQuestion]:
    """
    Exactly one MCQ for a single topic label. Options must stay inside that topic
    (plausible distractors, no cross-topic noise). forbidden_options reduces duplicate şıklar across the quiz.
    """
    lab = str(topic_label).strip()
    if len(lab) < 2:
        return None
    use_tr = (locale or "en").strip().lower().startswith("tr")
    lang_rule = (
        "Turkish (Türkçe) for question_text, all four options, short_explanation. "
        "Use complete, grammatical sentences (subject–verb agreement; no half-phrases or broken punctuation)."
        if use_tr
        else "English for question_text, all four options, short_explanation."
    )
    diff = _normalize_difficulty(difficulty)
    diff_note = _single_question_difficulty_note(diff)
    qt = _normalize_question_type(qtype) or "concept"
    type_rules = {
        "definition": "Ask what the term means or identify the best definition for THIS topic only.",
        "concept": "Ask how the idea works, what it implies, or how it relates to itself — not other chapters.",
        "application": "Use a short realistic scenario and ask for the best application of THIS topic only.",
        "comparison": (
            "Compare two ideas that both belong to THIS topic (trade-offs, when to use A vs B). "
            "All four options must stay inside this topic — no importing unrelated methods."
        ),
    }[qt]
    forbid_lines = [f"- {str(x).strip()[:200]}" for x in forbidden_options if str(x).strip()][:24]
    forbid_block = "\n".join(forbid_lines) if forbid_lines else "- (none)"
    prior_lines = [f"- {str(x).strip()[:180]}" for x in prior_question_stems if str(x).strip()][:14]
    prior_block = (
        "\nPRIOR_QUESTION_STEMS (avoid close paraphrase; pick a new angle on the same topic):\n"
        + "\n".join(prior_lines)
        if prior_lines
        else ""
    )

    system = (
        QUIZ_MCQ_SYSTEM_RULES
        + "You write ONE multiple-choice question for an exam.\n"
        f"STRICT TOPIC LOCK: The ONLY subject is: «{lab}». Every option must be about «{lab}» — "
        "wrong answers are plausible distractors (believable false claims about THIS topic). "
        "Never use options about other course topics, random methods, or unrelated formulas.\n"
        f"QUESTION STYLE: {qt}. {type_rules}\n"
        "Exactly 4 **full-sentence** options; exactly one correct; **correctAnswer** is only \"A\", \"B\", \"C\", or \"D\" "
        "(matching the position in your options array).\n"
        "Every option must be semantically distinct. Do not split one sentence into four variants.\n"
        f"Do not output one correct definition plus three near-copies that only negate or micro-edit the same sentence — "
        f"each distractor must rest on a different mistaken idea about «{lab}».\n"
        f"Vary question_text opening and shape with the required type ({qt}): definition, concept, comparison, and application "
        "should not all read like the same template with one noun swapped.\n"
        "Options: **full sentences**, as long as needed for accuracy — avoid rambling paragraphs but **never** truncate or split words to stay short.\n"
        "question_text: one **complete** sentence (no clipped endings).\n"
        "Do not use code-like syntax in question_text/options (e.g., `.loc[...]`, assignments, function calls).\n"
        "No «Part A/B», «Scenario:», workbook section labels, or Markdown ** in stems/options — exam-style prose only.\n"
        "Never split one sentence into four continuation chunks across options.\n"
        "OPTION OPENERS: First 6–12 words of each option must differ across A–D — no shared long prefix with only "
        "the ending edited.\n"
        "PDF TIE: Claims must be checkable from the PDF passage below; do not invent facts absent there.\n"
        "JSON SYNTAX: straight ASCII quotes only; escape literal \" inside strings as \\\"; no ``` fences.\n"
        f"{lang_rule}\n"
        f"{diff_note}\n"
        "Output ONLY valid JSON: one object with keys "
        "question_text, options (array of exactly 4 strings), correctAnswer (A|B|C|D), short_explanation (1–2 sentences), "
        f'topic (exact string: "{lab}"), type (exact string: "{qt}").\n'
    )
    lay = (layout_signal_block or "").strip()
    user = (
        (f"{lay}\n\n" if lay else "")
        + f'TOPIC_LABEL: "{lab}"\n'
        + f'REQUIRED_type_FIELD: "{qt}"\n'
        + f"{prior_block}\n"
        + "ALREADY_USED_OPTION_TEXTS (do not repeat or trivially paraphrase any of these):\n"
        + f"{forbid_block}\n\n"
        + "### PDF passage (sole source of truth)\n"
        + f"{excerpt}"
    )
    cap_ex = int(os.getenv("QUIZ_ONE_TOPIC_MAX_CHARS", "3200").strip() or "3200")
    cap_ex = max(1200, min(cap_ex, 8000))
    if len(user) > cap_ex:
        user = user[:cap_ex]
    temp = 0.24 if diff == "beginner" else (0.34 if diff == "technical" else 0.28)
    raw = _ollama_quiz_chat(user, system=system, options={"temperature": temp}, timeout_seconds=_quiz_ollama_timeout())
    q = _parse_single_quiz_question_json(raw, expected_topic=lab)
    if q and (q.question_type is None or q.question_type not in _ALLOWED_QTYPES):
        q = q.model_copy(update={"question_type": qt})
    return q


# fn: _ensure_topic_label | tr: soru konu etiketini düzelt / en: ensure question topic label
def _ensure_topic_label(q: QuizQuestion, locale: str) -> QuizQuestion:
    top = (q.topic or "").strip()
    if len(top) >= 2:
        return q.model_copy(update={"topic": top[:120]})
    use_tr = (locale or "en").strip().lower().startswith("tr")
    fallback = "Genel" if use_tr else "General"
    return q.model_copy(update={"topic": fallback})


# fn: _answer_similarity_threshold | tr: doğru cevap benzerlik eşiği / en: correct answer similarity threshold
def _answer_similarity_threshold() -> float:
    try:
        v = float(os.getenv("QUIZ_CORRECT_ANSWER_SIM_MAX", "0.84").strip() or "0.84")
    except ValueError:
        v = 0.84
    return max(0.72, min(v, 0.98))


# fn: _answer_signature | tr: cevap metni imzası oluştur / en: build answer text signature
def _answer_signature(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r"[^\w\sçğıöşü]", " ", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


# fn: _correct_answer_too_similar | tr: doğru cevap önceki sorulara çok mu benzer / en: check correct answer too similar to prior
def _correct_answer_too_similar(candidate: QuizQuestion, prior: Sequence[QuizQuestion]) -> bool:
    if not prior:
        return False
    cand = _answer_signature(candidate.correct_answer or "")
    if len(cand) < 14:
        return False
    sim_max = _answer_similarity_threshold()
    for q in prior:
        prev = _answer_signature(q.correct_answer or "")
        if len(prev) < 14:
            continue
        if cand == prev:
            return True
        shorter, longer = (cand, prev) if len(cand) <= len(prev) else (prev, cand)
        if len(shorter) >= 18 and shorter in longer:
            return True
        if SequenceMatcher(None, cand, prev).ratio() >= sim_max:
            return True
    return False


# fn: _accumulate_unique_valid | tr: benzersiz geçerli soruları topla / en: accumulate unique valid questions
def _accumulate_unique_valid(
    acc: List[QuizQuestion],
    incoming: Optional[List[QuizQuestion]],
    cap: int,
    locale: str,
    *,
    source_grounding: Optional[str] = None,
    quiz_target_size: Optional[int] = None,
) -> List[QuizQuestion]:
    merged = list(acc)
    mink = _min_stem_key_len()
    seen = {
        _question_stem_dedupe_key(q.question_text or "")
        for q in merged
        if len(_question_stem_key(q.question_text or "")) >= mink
    }
    if not incoming:
        return merged
    for q in incoming:
        q2 = _polish_quiz_question_style(_ensure_topic_label(q, locale), locale)
        ok_c = validate_quiz_question_complete(q2, source_grounding=source_grounding)
        ok_s = validate_quiz_question_strict(q2, source_grounding=source_grounding)
        ok_t = validate_quiz_question_structural(q2)
        if not ok_c and not ok_s and not ok_t:
            continue
        sk_raw = _question_stem_key(q2.question_text or "")
        if len(sk_raw) < mink:
            continue
        sk = _question_stem_dedupe_key(q2.question_text or "")
        if sk in seen:
            continue
        if merged and _question_redundant_with_prior_quiz(
            q2, merged, quiz_target_size=quiz_target_size
        ):
            continue
        if _correct_answer_too_similar(q2, merged):
            continue
        seen.add(sk)
        merged.append(q2)
        if len(merged) >= cap:
            break
    return merged


# fn: _stems_for_forbid | tr: yasak soru kökü listesi oluştur / en: build forbidden stem list
def _stems_for_forbid(acc: List[QuizQuestion]) -> List[str]:
    out: List[str] = []
    mink = _min_stem_key_len()
    for q in acc:
        s = _question_stem_dedupe_key(q.question_text or "")
        if len(_question_stem_key(q.question_text or "")) >= mink:
            out.append(s[:200])
    return out[:24]


# fn: _merged_forbidden_stems | tr: birleşik yasak soru kökü listesi / en: merged forbidden stem list
def _merged_forbidden_stems(acc: List[QuizQuestion], extra: Optional[Sequence[str]]) -> List[str]:
    """LLM'e gönderilen yasak kökler: bu turdaki üretim + veritabanından son soru metinleri."""
    merged = list(_stems_for_forbid(acc))
    seen = {m.lower() for m in merged}
    mink = _min_stem_key_len()
    for raw in extra or []:
        s = str(raw).strip()
        if len(_question_stem_key(s)) < mink:
            continue
        key = _question_stem_dedupe_key(s)[:200]
        lk = key.lower()
        if not lk or lk in seen:
            continue
        seen.add(lk)
        merged.append(key)
        if len(merged) >= 36:
            break
    return merged


# fn: _ordered_topic_labels | tr: öncelikli sıralı konu etiketleri / en: ordered priority topic labels
def _ordered_topic_labels(
    labels: List[str],
    focus_topics: Optional[Sequence[str]],
) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for t in focus_topics or []:
        s = _sanitize_quiz_topic_label(str(t).strip())
        if len(s) < 2:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s[:100])
    for lab in labels:
        s = _sanitize_quiz_topic_label(str(lab).strip())
        if len(s) < 2:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s[:100])
    return out


# fn: _top_up_with_locked_topic_mcq | tr: eksik soruyu kilitli konu mcq ile tamamla / en: top up missing questions with locked topic mcq
def _top_up_with_locked_topic_mcq(
    acc: List[QuizQuestion],
    topic_labels: List[str],
    source_text: str,
    target_n: int,
    locale: str,
    difficulty: str,
    focus_topics: Optional[Sequence[str]],
    *,
    deadline_monotonic: Optional[float] = None,
    document_id: Optional[str] = None,
    doc_chunks: Optional[List[str]] = None,
) -> None:
    """Fill missing slots with one Ollama call per question, topic-locked (uses a short chunk per topic)."""
    labels = _ordered_topic_labels(topic_labels, focus_topics)
    if not labels:
        return
    mink = _min_stem_key_len()
    seen = {
        _question_stem_dedupe_key(q.question_text or "")
        for q in acc
        if len(_question_stem_key(q.question_text or "")) >= mink
    }
    forbid: List[str] = []
    for q in acc:
        forbid.extend(list(q.options or []))
    sess_diff = _normalize_difficulty(difficulty)
    qtypes = _question_type_cycle_for_difficulty(sess_diff)
    try:
        max_calls_default = max(14, int(target_n) * 2)
        max_calls = int(os.getenv("QUIZ_PER_TOPIC_MAX_CALLS", str(max_calls_default)).strip() or str(max_calls_default))
    except ValueError:
        max_calls = max(14, int(target_n) * 2)
    max_calls = max(1, min(max_calls, 24))
    calls = 0
    idx = 0
    chunk_cap = int(os.getenv("QUIZ_TOPIC_CHUNK_CHARS", "2200").strip() or "2200")
    chunk_cap = max(800, min(chunk_cap, 4000))
    lay_ctx = _layout_signals_prefix(source_text)
    while len(acc) < target_n and calls < max_calls:
        _quiz_deadline_check(deadline_monotonic)
        lab = labels[idx % len(labels)]
        qt = qtypes[calls % len(qtypes)]
        chunk = _retrieve_topic_chunks(document_id, doc_chunks, lab, source_text, chunk_cap)
        q = _ollama_one_question_for_topic(
            lab,
            chunk,
            locale=locale,
            difficulty=difficulty,
            qtype=qt,
            forbidden_options=forbid[-96:],
            layout_signal_block=lay_ctx,
        )
        calls += 1
        idx += 1
        if not q:
            continue
        q2 = _polish_quiz_question_style(_ensure_topic_label(q, locale), locale)
        ok_c = validate_quiz_question_complete(q2, source_grounding=source_text)
        ok_s = validate_quiz_question_strict(q2, source_grounding=source_text)
        ok_t = validate_quiz_question_structural(q2)
        if not ok_c and not ok_s and not ok_t:
            continue
        sk_raw = _question_stem_key(q2.question_text or "")
        sk = _question_stem_dedupe_key(q2.question_text or "")
        if len(sk_raw) < mink or sk in seen:
            continue
        if acc and _question_redundant_with_prior_quiz(
            q2, acc, quiz_target_size=target_n
        ):
            continue
        if _correct_answer_too_similar(q2, acc):
            continue
        seen.add(sk)
        acc.append(q2)
        forbid.extend(list(q2.options or []))


# fn: _try_push_quiz_question | tr: soruyu listeye eklemeyi dene ve doğrula / en: try to validate and push quiz question
def _try_push_quiz_question(
    out_clean: List[QuizQuestion],
    seen_stems: set[str],
    q: QuizQuestion,
    *,
    n: int,
    locale: str,
    sess_diff: str,
    source: str,
    validation: str,
    allow_stem_suffix: bool = False,
    quiz_target_size: Optional[int] = None,
) -> bool:
    """
    Append one polished question if it passes the chosen validation tier and stem dedupe.
    validation: "complete" | "strict" | "structural" | "fill" | "survivor"
    """
    if len(out_clean) >= n:
        return False
    q2 = _polish_quiz_question_style(_ensure_topic_label(q, locale), locale)
    if q2.difficulty is None:
        q2 = q2.model_copy(update={"difficulty": sess_diff})
    if validation == "survivor":
        ok = validate_quiz_question_survivor(q2)
    elif validation == "fill":
        ok = validate_quiz_question_count_fill(q2)
    elif validation == "structural":
        ok = validate_quiz_question_structural(q2)
    elif validation == "strict":
        ok = validate_quiz_question_strict(q2, source_grounding=source)
    else:
        ok = validate_quiz_question_complete(q2, source_grounding=source)
    if not ok:
        return False
    if out_clean:
        if validation == "survivor":
            if _survivor_collides_with_prior_quiz(q2, out_clean):
                return False
        elif _question_redundant_with_prior_quiz(
            q2, out_clean, quiz_target_size=quiz_target_size or n
        ):
            return False
        if _correct_answer_too_similar(q2, out_clean):
            return False
    mink = _min_stem_key_len()
    stem_raw = _question_stem_key(q2.question_text or "")
    sk = _question_stem_dedupe_key(q2.question_text or "")
    if len(stem_raw) >= mink and sk in seen_stems:
        if not allow_stem_suffix:
            return False
        q2 = q2.model_copy(update={"question_text": f"{q2.question_text} ({len(out_clean) + 1})"})
        stem_raw = _question_stem_key(q2.question_text or "")
        sk = _question_stem_dedupe_key(q2.question_text or "")
        if len(stem_raw) >= mink and sk in seen_stems:
            return False
    if len(stem_raw) >= mink:
        seen_stems.add(sk)
    out_clean.append(q2)
    return True


_PAD_WRONG_TR = (
    "Bu özet, metinde yalnızca geçen küçük bir dipnotu sanki bölümün merkez teziymiş gibi abartır.",
    "Bu yorum, metinde açıkça kurulan neden–sonuç sırasını ters çevirerek okuyucuyu yanlış yönlendirir.",
    "Bu ifade, metinde hiç net biçimde söylenmeyen kesin bir sonuca atlayarak metni zorlar.",
    "Bu okuma, metindeki tanımı gereğinden daraltıp ondan geniş hükümler çıkarmaya çalışır.",
    "Bu satır, metinde ayrı ayrı tutulması gereken değerlendirme ölçütlerini birbirine karıştırır.",
    "Bu yorum, metnin gerçekten kapsadığı alanın ötesine çıkarak dış kaynaklı iddiaları içeri taşır.",
    "Bu özet, yalnızca örnekle gösterilen özel durumu genel kural sanarak metni yanlış geneller.",
    "Bu ifade, metinde yalnızca kısaca değinilen ayrıntıyı ana mesajın yerine koyarak dengesiz kalır.",
)
_PAD_CORRECT_FB_TR = (
    "Metnin bu bölümünde ana fikir, kavramların ayrı ayrı ve dikkatle ele alınması gerektiğidir.",
    "Özetlenen parçada vurgu, tanımların örneklerden ayrı tutulması ve doğru yorumlanmasıdır.",
    "Metin, okurun hem genel çerçeveyi hem de ayrıntıdaki sınırları birlikte görmesini ister.",
    "Bu kısımda yazar, değerlendirme ölçütlerinin birbirine karıştırılmamasını ima eder.",
    "Parçanın merkezinde, iddianın desteklenen kısımlarla uyumlu okunması vardır.",
    "Metinde öne çıkan mesaj, sonuca atlamadan ara adımların korunmasıdır.",
    "Bu bölümde tutarlı okuma, kapsam dışına çıkmadan metnin söylediğini genişletmektir.",
    "Vurgu, tek bir örneği genel kural sanmaktan kaçınıp metnin bütününü dikkate almaktır.",
)
_PAD_WRONG_EN = (
    "This summary treats a minor aside in the notes as if it were the section's central thesis.",
    "This reading flips the cause-and-effect order that the passage actually lays out for the reader.",
    "This line leaps to a firm conclusion that the text never states in clear, explicit terms.",
    "This interpretation narrows the definition given in the material and then over-generalises from it.",
    "This option blurs evaluation criteria that the notes keep distinct for a reason.",
    "This answer stretches the passage beyond its real scope by importing claims the excerpt does not make.",
    "This summary mistakes one illustrative example for the general rule the section is defending.",
    "This wording elevates a passing detail to headline status while sidelining the main argument.",
)
_PAD_CORRECT_FB_EN = (
    "The passage stresses that concepts should be handled distinctly and read with care.",
    "The focal idea is that definitions and examples complement each other but are not interchangeable.",
    "The excerpt asks the reader to keep both the big picture and the limits of each detail in view.",
    "The material implies that evaluation criteria should not be blurred together carelessly.",
    "The supported reading is to align any claim with what the text actually argues, not with extras.",
    "The emphasis is on following the reasoning steps the text gives rather than skipping to a conclusion.",
    "A consistent reading stays within scope instead of stretching the passage beyond what it covers.",
    "The point is to avoid treating one illustration as if it were the general rule stated in the text.",
)
_PAD_STEMS_TR = (
    "Bu bölümün notlardaki ana çizgisi hangi ifadeyle en iyi özetlenir?",
    "Bu parçanın vurgusu hangi yorumla metne en sadık kalır?",
    "Bu kısım için hangi özet, metnin iddiasını ve sınırlarını dengeli tutar?",
    "Bu kesitte hangi ifade, yazarın asıl mesajına en yakın kalır?",
    "Bu bölümün özü hangi seçenekte en doğru yansır?",
    "Bu not parçası için hangi yorum gereksiz genişletmeden kaçınır?",
    "Bu bölümde hangi seçenek, kavramlar arasındaki ayrımı en doğru korur?",
    "Metnin bu kısmında hangi ifade, örnek ile genel kuralı karıştırmadan kalır?",
    "Bu parçada hangi yorum, metnin desteklediği sonuçla en uyumludur?",
    "Bu kesitte hangi özet, metnin kapsamını aşmadan kalır?",
    "Bu bölüm için hangi seçenek, yanlış genellemeyi en iyi önler?",
    "Bu notlarda hangi ifade, tanım–örnek sırasına en sadık kalır?",
)
_PAD_STEMS_EN = (
    "Which statement best describes the main thread of this part of the study material?",
    "Which line stays closest to the author's emphasis in this segment of the notes?",
    "Which reading keeps this portion's claims and limits properly aligned with the text?",
    "Which option best states the core takeaway the material is building toward in this slice?",
    "Which summary is least stretched beyond what this section of the notes actually argues?",
    "Which interpretation treats definitions, examples, and scope the way this passage intends?",
    "Which option best preserves the distinction between related ideas in this excerpt?",
    "Which line keeps examples and general rules properly separated for this passage?",
    "Which reading stays aligned with the conclusion the text actually supports here?",
    "Which summary stays inside the real scope of this slice of the notes?",
    "Which option is least likely to over-generalize from one detail in this section?",
    "Which statement best tracks the definition-then-example order the passage uses?",
)
_PAD_TOPICS_TR = (
    "Ana fikir",
    "Bölüm özeti",
    "Öz çizgi",
    "Merkez vurgu",
    "Temel mesaj",
    "Kavram ayrımı",
    "Metin sınırları",
    "Okuma tutarlılığı",
)
_PAD_TOPICS_EN = (
    "Main ideas",
    "Section takeaway",
    "Core thread",
    "Central point",
    "Key emphasis",
    "Concept distinction",
    "Scope discipline",
    "Reading consistency",
)


# fn: _pad_anchor_prefix | tr: yedek soru kökü ön eki üret / en: build pad question stem anchor prefix
def _pad_anchor_prefix(span: str, *, max_words: int = 8, max_chars: int = 52) -> str:
    s = re.sub(r"\s+", " ", (span or "").strip())
    if len(s) < 18:
        return ""
    words = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ%+\-]+", s, flags=re.UNICODE)
    if len(words) < 4:
        return ""
    if not (words[0][0].isupper() or words[0][0].isdigit()):
        return ""
    frag = " ".join(words[:max_words])
    if len(frag) > max_chars:
        frag = clip_quiz_visible_text(frag, max_chars).rstrip("-,;:")
        if len(frag) > max_chars:
            frag = frag[:max_chars].rstrip("-,;:")
    if looks_like_code_or_tooling_noise(frag) or looks_like_broken_math_notation(frag):
        return ""
    if _pad_span_looks_like_code_scrap(frag) or _bad_option_text(frag):
        return ""
    return frag.replace('"', "'").replace("«", "").replace("»", "")


# fn: _safe_clip_text | tr: metni güvenli uzunlukta kırp / en: safely clip text to length
def _safe_clip_text(s: str, max_chars: int) -> str:
    """Clip only when above max_chars; same word-safe rules as ``clip_quiz_visible_text``."""
    return clip_quiz_visible_text(s, max_chars)


# fn: _pad_build_stem | tr: yedek soru kökü metni oluştur / en: build pad question stem text
def _pad_build_stem(span: str, vk: int, disp: int, use_tr: bool) -> str:
    anchor = _pad_anchor_prefix(span)
    if anchor:
        if use_tr:
            return _safe_clip_text(
                (
                f"Şu sözlerle başlayan kısım: «{anchor}» — hangi ifade bu parçanın ana fikrine en yakındır?"
                ),
                300,
            )
        return _safe_clip_text(
            (
            f'This part of the notes begins: "{anchor}" — which line best captures its main idea?'
            ),
            300,
        )
    stems = _PAD_STEMS_TR if use_tr else _PAD_STEMS_EN
    return stems[(vk + disp) % len(stems)]


# fn: _pad_build_topic | tr: yedek konu etiketi oluştur / en: build pad topic label
def _pad_build_topic(vk: int, disp: int, use_tr: bool) -> str:
    tops = _PAD_TOPICS_TR if use_tr else _PAD_TOPICS_EN
    return _safe_clip_text(tops[(vk * 5 + disp * 3) % len(tops)], 80)


_PAD_CODE_SCRAP_HINTS = (
    "axes[",
    "axvline",
    "subplot",
    "ff_df",
    "lasso_",
    "ridgecv",
    "lassocv",
    "iloc",
    "dtype",
    "matplotlib",
    "seaborn",
    "sklearn",
    "train_test_split",
    "gridsearch",
    "pipeline(",
    "color=",
    "alpha=0.",
)


# fn: _pad_span_looks_like_code_scrap | tr: metin kod parçasına mı benziyor / en: check if span looks like code scrap
def _pad_span_looks_like_code_scrap(s: str) -> bool:
    low = re.sub(r"\s+", " ", (s or "").strip()).lower()
    return any(h in low for h in _PAD_CODE_SCRAP_HINTS)


# fn: _pick_three_distinct | tr: üç farklı yanlış şık adayı seç / en: pick three distinct wrong option candidates
def _pick_three_distinct(pool: Sequence[str], slot: int) -> Tuple[str, str, str]:
    n = len(pool)
    i0 = (slot * 2) % n
    i1 = (slot * 3 + 1) % n
    i2 = (slot * 5 + 4) % n
    if i1 == i0:
        i1 = (i0 + 1) % n
    if i2 == i0 or i2 == i1:
        i2 = (i0 + 2) % n
        if i2 == i1:
            i2 = (i1 + 1) % n
    return pool[i0], pool[i1], pool[i2]


# fn: _fallback_question_type | tr: yedek soru türü seç / en: pick fallback question type
def _fallback_question_type(seed: int, sess_diff: str) -> str:
    cycle = _question_type_cycle_for_difficulty(sess_diff)
    if not cycle:
        return "concept"
    return _normalize_question_type(cycle[abs(int(seed)) % len(cycle)]) or "concept"


# fn: _fallback_stem_for_type | tr: tür için yedek soru kökü üret / en: build fallback stem for type
def _fallback_stem_for_type(span: str, seed: int, idx: int, use_tr: bool, qtype: str) -> str:
    s = re.sub(r"\s+", " ", (span or "").strip())
    anchor = _pad_anchor_prefix(s)
    i = abs(int(seed)) + max(1, int(idx))
    if use_tr:
        banks = {
            "definition": (
                "Bu bölümde geçen temel kavramın en doğru tanımı hangisidir?",
                "Bu kesitteki ana terimi en iyi açıklayan seçenek hangisidir?",
            ),
            "comparison": (
                "Bu parçada yan yana verilen iki yaklaşım arasındaki farkı en iyi hangi ifade açıklar?",
                "Bu bölüm bağlamında yakın iki kavramı en doğru ayıran seçenek hangisidir?",
            ),
            "application": (
                "Bu bölümdeki ilke bir problem durumuna uygulanırsa en uygun sonuç hangi seçenekte verilir?",
                "Bu kesitteki ana fikri pratik bir senaryoda en doğru kullanan ifade hangisidir?",
            ),
            "concept": _PAD_STEMS_TR,
        }
        bank = banks.get(qtype) or banks["concept"]
        base = bank[i % len(bank)]
        if anchor and qtype in ("concept", "definition"):
            return _safe_clip_text(
                (
                f"Şu sözlerle başlayan kısım: «{anchor}» — bu bağlamı en doğru yorumlayan seçenek hangisidir?"
                ),
                300,
            )
        return _safe_clip_text(str(base), 300)
    banks_en = {
        "definition": (
            "Which option gives the most accurate definition of the core term in this section?",
            "Which line best defines the key concept introduced in this excerpt?",
        ),
        "comparison": (
            "Which option best distinguishes the two close ideas presented in this passage?",
            "In this section, which line captures the key difference between related approaches?",
        ),
        "application": (
            "Which option applies this section's core idea most accurately to a problem scenario?",
            "If the principle in this excerpt is applied, which outcome is most consistent with the text?",
        ),
        "concept": _PAD_STEMS_EN,
    }
    bank_en = banks_en.get(qtype) or banks_en["concept"]
    base_en = bank_en[i % len(bank_en)]
    if anchor and qtype in ("concept", "definition"):
        return _safe_clip_text(
            (
            f'This part begins with "{anchor}" — which option best captures its intended meaning?'
            ),
            300,
        )
    return _safe_clip_text(str(base_en), 300)


# fn: _fallback_option_banks | tr: tür için yedek şık bankaları / en: fallback option banks for type
def _fallback_option_banks(use_tr: bool, qtype: str) -> Tuple[Sequence[str], Sequence[str]]:
    if use_tr:
        tr_correct = {
            "definition": (
                "Kavramı, metindeki bağlam ve sınırlarla uyumlu şekilde tanımlar.",
                "Tanımı, bölümde verilen kapsamı aşmadan doğru çerçevede kurar.",
                "Temel terimi, örnekten ayırarak metnin anlattığı biçimde açıklar.",
            ),
            "comparison": (
                "Yakın kavramların amaç ve kullanım farkını metne uygun biçimde ayırır.",
                "İki yaklaşım arasındaki sınırları, pasajdaki vurguya sadık kalarak netleştirir.",
                "Benzer görünen fikirleri karıştırmadan işlev farkını doğru kurar.",
            ),
            "application": (
                "İlkeyi, metindeki koşulları koruyarak senaryoya dengeli biçimde uygular.",
                "Uygulamayı, bölümün amaç ve sınırlamalarıyla tutarlı olacak şekilde kurar.",
                "Ana fikri, kapsam dışına taşmadan pratik duruma doğru aktarır.",
            ),
            "concept": _PAD_CORRECT_FB_TR,
        }
        tr_wrong = {
            "definition": (
                "Tanımı, kavramın örneğiyle karıştırıp asıl sınırı belirsiz bırakır.",
                "Kavrama metinde geçmeyen ek anlamlar yükleyerek çerçeveyi bozar.",
                "Komşu bir terimi bu kavramın yerine koyarak anlam kayması yaratır.",
                "Tanımı gereksiz daraltıp sonra metnin vermediği geniş sonuçlara atlar.",
                "Kavramı, bölümde açıkça ayrılan başka bir başlıkla aynılaştırır.",
                "Tanımı yalnızca tek bir ayrıntıya indirgediği için bütün bağlamı kaçırır.",
            ),
            "comparison": (
                "İki kavramı eşdeğer sayarak metindeki ayrımı görünmez hale getirir.",
                "Yalnızca kelime benzerliğine bakıp işlev farkını atlar.",
                "Örnek düzeyindeki farkı büyütüp temel farkı yanlış yerde kurar.",
                "Kapsam farkını yok sayıp iki yaklaşımı aynı koşullarda değerlendirir.",
                "Metinde ayrı ele alınan iki fikri tek bir sonuçta birleştirir.",
                "Benzerliği merkeze alıp amaç farklılığını göz ardı eder.",
            ),
            "application": (
                "İlkeyi bağlam dışına taşıyarak metnin kapsamını aşan bir sonuca gider.",
                "Ana kuralı tali ayrıntıya indirgediği için uygulama hedefiyle uyumsuz kalır.",
                "Sınırlayıcı koşulları yok sayıp örneği evrensel kural gibi uygular.",
                "Metindeki ara adımları atlayarak sonuca erken sıçrar.",
                "Uygulamayı, metnin savunmadığı varsayımlara dayandırır.",
                "İlkenin geçerlilik sınırını değiştirip yanlış senaryoya taşır.",
            ),
            "concept": _PAD_WRONG_TR,
        }
        return tr_correct.get(qtype, _PAD_CORRECT_FB_TR), tr_wrong.get(qtype, _PAD_WRONG_TR)
    en_correct = {
        "definition": (
            "It defines the core term within the boundaries and context stated in the passage.",
            "It explains the concept accurately without extending beyond the section's scope.",
            "It separates the concept from its examples and keeps the definition precise.",
        ),
        "comparison": (
            "It distinguishes purpose and scope between the close ideas in line with the text.",
            "It keeps similar concepts separate by preserving the functional difference the passage makes.",
            "It compares the approaches accurately without collapsing their boundaries.",
        ),
        "application": (
            "It applies the principle with the same constraints and intent described in the section.",
            "It transfers the idea to the scenario while preserving the passage's limits.",
            "It uses the core rule in practice without adding unsupported assumptions.",
        ),
        "concept": _PAD_CORRECT_FB_EN,
    }
    en_wrong = {
        "definition": (
            "It confuses the concept definition with one illustrative example from the notes.",
            "It adds meanings that the passage never supports and overextends the term.",
            "It replaces the target concept with a neighboring but distinct idea.",
            "It narrows the definition too much and then over-generalizes from it.",
            "It merges two separate terms that the section keeps distinct.",
            "It reduces the concept to one detail and misses the full context.",
        ),
        "comparison": (
            "It treats the two ideas as interchangeable and removes the boundary the text keeps.",
            "It focuses on wording overlap and misses the functional distinction.",
            "It magnifies a minor example-level difference and misses the core contrast.",
            "It ignores scope and compares both approaches under the wrong conditions.",
            "It fuses two separately framed ideas into one unsupported conclusion.",
            "It prioritizes surface similarity while dropping the purpose difference.",
        ),
        "application": (
            "It extends the principle beyond the limits explicitly stated by the passage.",
            "It reduces the main rule to a minor detail and misaligns with the task.",
            "It ignores constraint conditions and treats one example as universal.",
            "It skips key reasoning steps and jumps to a conclusion too early.",
            "It relies on assumptions that are not supported in the section.",
            "It transfers the rule to a context where the text does not justify it.",
        ),
        "concept": _PAD_WRONG_EN,
    }
    return en_correct.get(qtype, _PAD_CORRECT_FB_EN), en_wrong.get(qtype, _PAD_WRONG_EN)


# fn: _fallback_options_for_type | tr: tür için yedek şıklar üret / en: build fallback options for type
def _fallback_options_for_type(span: str, qtype: str, seed: int, use_tr: bool) -> Tuple[List[str], str]:
    good_bank, bad_bank = _fallback_option_banks(use_tr, qtype)
    s = abs(int(seed))
    good = str(good_bank[s % len(good_bank)]).strip()
    b0, b1, b2 = _pick_three_distinct(bad_bank, s + 5)
    opts = [str(b0).strip(), str(b1).strip(), str(b2).strip()]
    pos = s % 4
    if qtype in ("definition", "concept"):
        anchor = _pad_anchor_prefix(span, max_words=5, max_chars=34)
        if anchor:
            if use_tr:
                good = f"«{anchor}» ifadesiyle uyumlu merkezi yorum budur."
            else:
                good = f'This aligns with the central meaning signaled by "{anchor}".'
    opts.insert(pos, good)
    opts = [_safe_clip_text(x, option_len_max()) for x in opts]
    return opts, opts[pos]


# fn: _build_guaranteed_pad_mcq | tr: garantili yedek mcq sorusu oluştur / en: build guaranteed pad mcq question
def _build_guaranteed_pad_mcq(
    source_excerpt: str,
    variety_key: int,
    display_index: int,
    locale: str,
    sess_diff: str,
) -> QuizQuestion:
    """
    Deterministic MCQ when all generators/validators are exhausted — always shape-valid
    after polish so ``generate_quiz_guaranteed`` can return length N.
    ``variety_key`` rotates excerpt offsets and distractors; ``display_index`` is the small,
    human-visible quiz position (1-based) and must not reuse internal mixing integers.
    """
    use_tr = (locale or "en").strip().lower().startswith("tr")
    vk = abs(int(variety_key))
    disp = max(1, int(display_index))
    bio = re.sub(r"\s+", " ", (source_excerpt or "").strip())
    if len(bio) < 48:
        bio = (
            "Öğrenme materyalindeki ana kavramları ayırt etmek ve tanımları doğru kullanmak önem taşır."
            if use_tr
            else "Distinguishing main concepts and using definitions correctly matters in this study material."
        )
    L = len(bio)
    step = 41 + (vk % 7) * 9
    start = max(0, (vk * step) % max(1, L - 35))
    if start > 0:
        nb = bio.rfind(" ", 0, start + 1)
        if nb >= 0:
            start = nb + 1
    span = bio[start : start + 150].strip()
    if len(span) < 36:
        span = bio[: min(150, L)]
    qtype = _fallback_question_type(vk + disp, sess_diff)
    stem = _fallback_stem_for_type(span, vk, disp, use_tr, qtype)
    opts_list, ca = _fallback_options_for_type(span, qtype, vk + disp * 13, use_tr)
    topic = _pad_build_topic(vk, disp, use_tr)
    return QuizQuestion(
        question_text=stem,
        options=opts_list,
        correct_answer=ca,
        topic=topic,
        question_type=qtype,
        difficulty=sess_diff,
        explanation=None,
    )


# fn: _nuclear_fill_quiz_to_n | tr: n soruya zorla doldur (son çare) / en: force-fill quiz to n questions (last resort)
def _nuclear_fill_quiz_to_n(
    out_clean: List[QuizQuestion],
    n: int,
    pad_src: str,
    locale: str,
    sess_diff: str,
    seen_stems: Set[str],
) -> None:
    """
    When all ``_try_push`` paths still leave len < n, append deterministic pads directly.
    Skips cross-quiz collision checks (those can deadlock with strict earlier items);
    each stem gets a unique suffix so stem keys differ.
    """
    guard = 0
    while len(out_clean) < n and guard < max(200, n * 40):
        guard += 1
        mix = (len(out_clean) + 1) * 104729 + guard * 49999
        tag = len(out_clean) + 1
        q = _build_guaranteed_pad_mcq(pad_src, mix, tag, locale, sess_diff)
        q = _ensure_topic_label(q, locale)
        if q.difficulty is None:
            q = q.model_copy(update={"difficulty": sess_diff})
        q2 = _polish_quiz_question_style(q, locale)
        if not validate_quiz_question_survivor(q2):
            q2 = q
            if not validate_quiz_question_survivor(q2):
                continue
        sk_raw = _question_stem_key(q2.question_text or "")
        if len(sk_raw) >= _min_stem_key_len():
            seen_stems.add(_question_stem_dedupe_key(q2.question_text or ""))
        out_clean.append(q2)


# fn: _synthetic_slot_mcq | tr: sentetik slot mcq sorusu üret / en: generate synthetic slot mcq question
def _synthetic_slot_mcq(slot_index: int, locale: str, sess_diff: str) -> QuizQuestion:
    """
    Deterministic, always schema- and survivor-valid MCQ with four unique options.
    Used only to close the last gap to N when pads/nuclear would otherwise deadlock
    on survivor option-signature collisions.
    """
    use_tr = (locale or "en").strip().lower().startswith("tr")
    i = max(1, int(slot_index))
    qtype = _fallback_question_type(i * 37 + 11, sess_diff)
    stem = _fallback_stem_for_type("", i * 19 + 3, i, use_tr, qtype)
    opts, ca = _fallback_options_for_type("", qtype, i * 53 + 7, use_tr)
    topic = f"{_pad_build_topic(i * 11, i, use_tr)} {i}"
    return QuizQuestion(
        question_text=stem,
        options=[_safe_clip_text(x, option_len_max()) for x in opts],
        correct_answer=_safe_clip_text(ca, option_len_max()),
        topic=_safe_clip_text(topic, 80),
        question_type=qtype,
        difficulty=sess_diff,
        explanation=None,
    )


# fn: _synthetic_backfill_to_n | tr: sentetik sorularla n adede tamamla / en: backfill to n with synthetic questions
def _synthetic_backfill_to_n(
    out_clean: List[QuizQuestion],
    n: int,
    locale: str,
    sess_diff: str,
) -> int:
    """Append minimal unique MCQs until len == n. Returns how many were added."""
    added = 0
    guard = 0
    while len(out_clean) < n:
        guard += 1
        slot = len(out_clean) + 1 + added * 31
        q = _synthetic_slot_mcq(slot, locale, sess_diff)
        q = _ensure_topic_label(q, locale)
        if q.difficulty is None:
            q = q.model_copy(update={"difficulty": sess_diff})
        q2 = _polish_quiz_question_style(q, locale)
        if not validate_quiz_question_survivor(q2):
            if not validate_quiz_question_survivor(q):
                if guard > max(60, n * 20):
                    break
                continue
            q2 = q
        out_clean.append(q2)
        added += 1
        if added > n + 24 or guard > max(80, n * 24):
            break
    return added


# fn: warm_document_quiz_topics_background | tr: arka planda konu önbelleğini ısıt / en: warm topic cache in background
def warm_document_quiz_topics_background(document_id: str, locale: str = "en") -> None:
    """After PDF upload: extract topic cards once so quiz generation skips repeated LLM topic passes."""
    try:
        did = (document_id or "").strip()
        if not did:
            return
        from app.services.document_store import get_document, set_document_quiz_topic_cards

        doc = get_document(did)
        if not doc or not (doc.study_text or "").strip():
            return
        if getattr(doc, "quiz_topic_cards", None) and len(doc.quiz_topic_cards) >= 2:
            return
        loc = (locale or "en").strip().lower()[:8]
        content = doc.study_text
        source = _prepare_quiz_source_text(content, 15000, from_pdf_document=True)
        if not source.strip():
            return
        cache_key = _quiz_topic_cache_key(did, content, loc)
        label_cap = max(6, min(14, 12))
        cards: List[Dict[str, str]] = []
        labels = _ollama_extract_topic_labels(
            source, locale=loc, max_topics=label_cap, from_pdf_document=True
        )
        if not labels and len(content) > 5000:
            alt_src = _prepare_quiz_source_text(content[len(content) // 4 :], 15000, from_pdf_document=True)
            if alt_src.strip():
                labels = _ollama_extract_topic_labels(
                    alt_src, locale=loc, max_topics=label_cap, from_pdf_document=True
                )
        if labels:
            filled = _ollama_fill_study_topic_cards(labels, source, locale=loc, from_pdf_document=True)
            if filled:
                cards = list(filled)
        if len(cards) < 2:
            tm = _ollama_extract_topic_map(source, locale=loc, max_topics=12, from_pdf_document=True)
            cards = _merge_study_cards_by_topic(cards, _topic_map_rows_to_rich_cards(tm or []))
        if len(cards) < 2:
            shot = _ollama_extract_rich_topics_one_shot(
                source, locale=loc, max_topics=label_cap, from_pdf_document=True
            )
            if shot:
                cards = _merge_study_cards_by_topic(cards, shot)
        if len(cards) >= 2:
            set_document_quiz_topic_cards(did, cards)
        if cache_key and len(cards) >= 2:
            _quiz_topic_cache_set(cache_key, _slim_topic_cards_for_cache(cards))
    except Exception:
        log.exception("warm_document_quiz_topics_background failed")


# fn: generate_quiz_guaranteed | tr: tam n soru garantili quiz üret (ana api) / en: generate exactly n quiz questions (main api)
def generate_quiz_guaranteed(
    content: str,
    num_questions: int = 5,
    max_topics: int = 5,
    max_content_chars: int = 15000,
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
    locale: str = "en",
    difficulty: str = "normal",
    document_id: Optional[str] = None,
    forbidden_stems: Optional[Sequence[str]] = None,
    generation_salt: int = 0,
    **kwargs: Any,
) -> List[QuizQuestion]:
    """
    Produce exactly ``num_questions`` MCQs or raise ``QuizGenerationError``.

    Default (Ollama): topic cards → one JSON MCQ per topic, validated (complete → strict → structural fill)
    so the returned list length matches the request whenever the document has enough raw text for fallbacks.
    Optional ``QUIZ_USE_LEGACY_BULK_GENERATION`` restores bulk conceptual quiz + topic top-up.
    Heuristic rule-based fill runs when ``QUIZ_ALLOW_RULEBASED_FALLBACK`` is on (default).
    """
    if not content or not content.strip():
        raise QuizGenerationError("Quiz generation could not continue because the PDF text is too limited.")

    n = _clamp_quiz_count(num_questions)
    provider = os.getenv("QUIZ_PROVIDER", "auto").lower().strip()
    from_pdf_doc = bool((document_id or "").strip())

    mcc = max(4000, int(max_content_chars or 15000))
    if from_pdf_doc:
        mcc = max(mcc, 22_000)
    mcc = min(mcc, 120_000)

    source = _prepare_quiz_source_text(content, mcc, from_pdf_document=from_pdf_doc)
    if not source.strip():
        source = (content or "").strip()[:mcc]
    if not source.strip():
        raise QuizGenerationError("Quiz generation could not continue because the PDF text is too limited.")

    acc: List[QuizQuestion] = []
    excerpt_cap = int(os.getenv("QUIZ_GEN_EXCERPT_CHARS", "4000").strip() or "4000")
    excerpt_cap = max(2500, min(excerpt_cap, 9000))
    salt_i = int(generation_salt or 0) % 1_000_000_007
    if from_pdf_doc and len(source) > excerpt_cap:
        excerpt = _stratified_pdf_excerpt(source, excerpt_cap, salt_i)
    elif salt_i and len(source) > excerpt_cap + 400:
        span = max(1, len(source) - excerpt_cap)
        start = (salt_i * 7919) % span
        excerpt = source[start : start + excerpt_cap]
    else:
        excerpt = source[:excerpt_cap]

    gen_deadline = time.monotonic() + _quiz_gen_deadline_seconds(n)

    doc_chunks_topup: Optional[List[str]] = None
    if document_id:
        from app.services.document_store import get_document

        _dtop = get_document(document_id)
        if _dtop:
            doc_chunks_topup = list(_dtop.chunks or [])

    ollama_ok = False
    if provider in ("ollama", "auto", "openai"):
        try:
            from app.services.ollama_service import ollama_available

            ollama_ok = bool(ollama_available() and source.strip())
        except Exception:
            ollama_ok = False

    cloud_ok = _quiz_cloud_ready()
    if provider == "openai":
        llm_ok = cloud_ok
    elif provider == "ollama":
        llm_ok = ollama_ok
    else:
        llm_ok = ollama_ok or cloud_ok

    topic_labels: Optional[List[str]] = None
    prior_stems_for_pipe = [str(s).strip() for s in (forbidden_stems or []) if len(str(s).strip()) >= 8][:22]

    def _fb(acc: List[QuizQuestion]) -> List[str]:
        return _merged_forbidden_stems(acc, forbidden_stems)

    if llm_ok:
        try:
            _quiz_deadline_check(gen_deadline)
            use_legacy = os.getenv("QUIZ_USE_LEGACY_BULK_GENERATION", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            label_cap = max(n, 6, min(max_topics + 4, 16))

            if not use_legacy:
                pipe_qs, topic_labels = _run_study_topic_quiz_pipeline(
                    source=source,
                    excerpt=excerpt,
                    content=content,
                    max_content_chars=mcc,
                    n=n,
                    max_topics=max_topics,
                    focus_topics=focus_topics,
                    challenge_topics=challenge_topics,
                    locale=locale,
                    difficulty=difficulty,
                    document_id=document_id,
                    deadline_monotonic=gen_deadline,
                    from_pdf_document=from_pdf_doc,
                    prior_question_stems=prior_stems_for_pipe,
                    generation_salt=salt_i,
                )
                acc = _accumulate_unique_valid(acc, pipe_qs, n + 12, locale, source_grounding=source, quiz_target_size=n)

                if (not topic_labels or len(topic_labels) < 2) and source.strip():
                    lbl_fb = _ollama_extract_topic_labels(
                        source,
                        locale=locale,
                        max_topics=label_cap,
                        from_pdf_document=from_pdf_doc,
                    )
                    if lbl_fb:
                        topic_labels = lbl_fb

                for _bulk_wave in range(2):
                    _quiz_deadline_check(gen_deadline)
                    need_b = n - len(acc)
                    if need_b <= 0:
                        break
                    ask_b = min(max(need_b + 1, need_b), n, 10)
                    batch_b = _ollama_conceptual_quiz(
                        source,
                        ask_b,
                        focus_topics,
                        challenge_topics=challenge_topics,
                        locale=locale,
                        difficulty=difficulty,
                        forbidden_stems=_fb(acc),
                        from_pdf_document=from_pdf_doc,
                    )
                    acc = _accumulate_unique_valid(acc, batch_b, n + 12, locale, source_grounding=source, quiz_target_size=n)
                    if len(acc) >= n:
                        break
                    need_b2 = n - len(acc)
                    if need_b2 > 0 and topic_labels and len(topic_labels) >= 2:
                        b_lab = _ollama_quiz_from_topic_labels(
                            topic_labels,
                            excerpt,
                            num_questions=max(need_b2 + 1, min(n, 8)),
                            locale=locale,
                            difficulty=difficulty,
                            focus_topics=focus_topics,
                            challenge_topics=challenge_topics,
                            layout_signal_source=content,
                        )
                        acc = _accumulate_unique_valid(acc, b_lab, n + 12, locale, source_grounding=source, quiz_target_size=n)
            else:
                topic_labels = _ollama_extract_topic_labels(
                    source,
                    locale=locale,
                    max_topics=label_cap,
                    from_pdf_document=from_pdf_doc,
                )
                if not topic_labels and len(source) > 5000:
                    alt_src = _prepare_quiz_source_text(
                        content[len(content) // 4 :], mcc, from_pdf_document=from_pdf_doc
                    )
                    topic_labels = _ollama_extract_topic_labels(
                        alt_src,
                        locale=locale,
                        max_topics=label_cap,
                        from_pdf_document=from_pdf_doc,
                    )
                if not topic_labels:
                    topic_map = _ollama_extract_topic_map(
                        source,
                        locale=locale,
                        max_topics=max(max_topics * 2, 8),
                        from_pdf_document=from_pdf_doc,
                    )
                    if topic_map:
                        topic_labels = [
                            str(x.get("topic", "")).strip()
                            for x in topic_map
                            if str(x.get("topic", "")).strip()
                        ]

                for wave in range(3):
                    _quiz_deadline_check(gen_deadline)
                    need = n - len(acc)
                    if need <= 0:
                        break
                    ask = min(max(need + 1, need), n, 10)
                    batch = _ollama_conceptual_quiz(
                        source,
                        ask,
                        focus_topics,
                        challenge_topics=challenge_topics,
                        locale=locale,
                        difficulty=difficulty,
                        forbidden_stems=_fb(acc),
                        from_pdf_document=from_pdf_doc,
                    )
                    acc = _accumulate_unique_valid(acc, batch, n + 12, locale, source_grounding=source, quiz_target_size=n)
                    if len(acc) >= n:
                        break

                    if wave <= 1 and topic_labels:
                        need2 = n - len(acc)
                        if need2 > 0:
                            b2 = _ollama_quiz_from_topic_labels(
                                topic_labels,
                                excerpt,
                                num_questions=max(need2 + 1, min(n, 8)),
                                locale=locale,
                                difficulty=difficulty,
                                focus_topics=focus_topics,
                                challenge_topics=challenge_topics,
                                layout_signal_source=content,
                            )
                            acc = _accumulate_unique_valid(acc, b2, n + 12, locale, source_grounding=source, quiz_target_size=n)

                for _ in range(2):
                    _quiz_deadline_check(gen_deadline)
                    need = n - len(acc)
                    if need <= 0:
                        break
                    ask = min(max(need + 1, need), n, 10)
                    batch = _ollama_conceptual_quiz(
                        source,
                        ask,
                        focus_topics,
                        challenge_topics=challenge_topics,
                        locale=locale,
                        difficulty=difficulty,
                        forbidden_stems=_fb(acc),
                        from_pdf_document=from_pdf_doc,
                    )
                    acc = _accumulate_unique_valid(acc, batch, n + 12, locale, source_grounding=source, quiz_target_size=n)

            if len(acc) < n and topic_labels:
                _quiz_deadline_check(gen_deadline)
                _top_up_with_locked_topic_mcq(
                    acc,
                    topic_labels,
                    source,
                    n,
                    locale,
                    difficulty,
                    focus_topics,
                    deadline_monotonic=gen_deadline,
                    document_id=document_id,
                    doc_chunks=doc_chunks_topup,
                )
        except QuizGenerationError as e:
            # Deadline (or any soft abort inside the LLM phase) must NOT skip
            # rule-based MCQs + deterministic pads — those tiers still satisfy ``n``.
            log.warning(
                "quiz LLM phase stopped early (falling back to rule-based/pad): %s acc_len=%s",
                getattr(e, "detail_tr", str(e)),
                len(acc),
            )
        except Exception:
            log.warning("quiz ollama phase failed", exc_info=True)

    # Keep full quiz generation local-first for cost, but rescue low-yield runs with cloud.
    # We only do this when cloud is ready and the local LLM phase did not produce enough
    # diverse valid items to maintain quality.
    if (
        len(acc) < n
        and cloud_ok
        and _quiz_quality_cloud_fallback_enabled()
        and provider != "openai"
        and len(acc) < _quiz_cloud_fallback_threshold(n)
    ):
        try:
            _quiz_deadline_check(gen_deadline)
            need_cloud = n - len(acc)
            ask_cloud = min(max(need_cloud + 2, need_cloud), n, 10)
            cloud_batch = _ollama_conceptual_quiz(
                source,
                ask_cloud,
                focus_topics,
                challenge_topics=challenge_topics,
                locale=locale,
                difficulty=difficulty,
                forbidden_stems=_fb(acc),
                provider_override="openai",
                from_pdf_document=from_pdf_doc,
            )
            acc = _accumulate_unique_valid(
                acc,
                cloud_batch,
                n + 12,
                locale,
                source_grounding=source,
                quiz_target_size=n,
            )
            if len(acc) < n and need_cloud > 1:
                cloud_batch_2 = _ollama_conceptual_quiz(
                    source,
                    min(max(n - len(acc) + 1, 2), n, 10),
                    focus_topics,
                    challenge_topics=challenge_topics,
                    locale=locale,
                    difficulty=difficulty,
                    forbidden_stems=_fb(acc),
                    provider_override="openai",
                    from_pdf_document=from_pdf_doc,
                )
                acc = _accumulate_unique_valid(
                    acc,
                    cloud_batch_2,
                    n + 12,
                    locale,
                    source_grounding=source,
                    quiz_target_size=n,
                )
        except QuizGenerationError:
            raise
        except Exception:
            log.warning("quiz cloud quality fallback failed", exc_info=True)

    allow_rb = _quiz_rulebased_fallback_enabled()
    pairs: List[tuple[str, str]] = []
    need_rb = n - len(acc)
    if need_rb > 0 and allow_rb:
        pairs = _extract_concept_pairs(source, max_pairs=max(72, n * 14))
        if len(pairs) < n + 2:
            extra_src = _prepare_quiz_source_text(
                content, min(int(mcc), 16000), from_pdf_document=from_pdf_doc
            )
            if extra_src and extra_src != source:
                for a, b in _extract_concept_pairs(extra_src, max_pairs=max(72, n * 14)):
                    if (a.lower(), b.lower()) not in {(x.lower(), y.lower()) for x, y in pairs}:
                        pairs.append((a, b))
        if len(pairs) < n + 2:
            plain = re.sub(r"\s+", " ", (content or "").strip())[:16000]
            for a, b in _extract_concept_pairs(plain, max_pairs=max(72, n * 14)):
                if (a.lower(), b.lower()) not in {(x.lower(), y.lower()) for x, y in pairs}:
                    pairs.append((a, b))
        if len(pairs) < max(3, n):
            boot = _bootstrap_pairs_from_sentences(content, min_want=max(n + 8, 16))
            pk = {(x.lower(), y.lower()) for x, y in pairs}
            for a, b in boot:
                k = (a.lower(), b.lower())
                if k not in pk:
                    pk.add(k)
                    pairs.append((a, b))
        rb = _build_rule_based_mcq(
            pairs[: max(24, max_topics * 8)],
            num_questions=max(need_rb + 8, n),
            focus_topics=focus_topics,
            session_difficulty=difficulty,
            locale=locale,
        )
        acc = _accumulate_unique_valid(acc, rb, n + 12, locale, source_grounding=source, quiz_target_size=n)
    elif need_rb > 0 and not allow_rb:
        log.info("quiz: skipping rule-based fallback (disabled); acc_len=%s need=%s", len(acc), need_rb)

    final = _dedupe_questions(acc)
    sess_diff = _normalize_difficulty(difficulty)
    out_clean: List[QuizQuestion] = []
    seen_stems: set[str] = set()
    for q in final:
        if len(out_clean) >= n:
            break
        _try_push_quiz_question(
            out_clean,
            seen_stems,
            q,
            n=n,
            locale=locale,
            sess_diff=sess_diff,
            source=source,
            validation="complete",
            allow_stem_suffix=False,
        )

    if len(out_clean) < n:
        for q in final:
            if len(out_clean) >= n:
                break
            _try_push_quiz_question(
                out_clean,
                seen_stems,
                q,
                n=n,
                locale=locale,
                sess_diff=sess_diff,
                source=source,
                validation="strict",
                allow_stem_suffix=False,
            )

    if len(out_clean) < n:
        for q in final:
            if len(out_clean) >= n:
                break
            _try_push_quiz_question(
                out_clean,
                seen_stems,
                q,
                n=n,
                locale=locale,
                sess_diff=sess_diff,
                source=source,
                validation="structural",
                allow_stem_suffix=True,
            )

    if len(out_clean) < n:
        need = n - len(out_clean)
        boot2 = _bootstrap_pairs_from_sentences(content, min_want=max(need + 10, 20))
        pair_fallback = pairs[: max(8, n)] if pairs else _extract_concept_pairs(
            re.sub(r"\s+", " ", (content or "").strip())[:16000], max_pairs=max(24, n * 6)
        )
        rb_em = _build_rule_based_mcq(
            boot2 if boot2 else pair_fallback,
            num_questions=need + 6,
            focus_topics=focus_topics,
            session_difficulty=difficulty,
            locale=locale,
        )
        for q in _dedupe_questions(rb_em):
            if len(out_clean) >= n:
                break
            if not _try_push_quiz_question(
                out_clean,
                seen_stems,
                q,
                n=n,
                locale=locale,
                sess_diff=sess_diff,
                source=source,
                validation="complete",
                allow_stem_suffix=False,
            ):
                _try_push_quiz_question(
                    out_clean,
                    seen_stems,
                    q,
                    n=n,
                    locale=locale,
                    sess_diff=sess_diff,
                    source=source,
                    validation="strict",
                    allow_stem_suffix=True,
                )

    # Last-mile: rule-based waves with stem suffix + structural validation so we still reach N.
    if len(out_clean) < n:
        for _wave in range(14):
            if len(out_clean) >= n:
                break
            need = n - len(out_clean)
            emergency_pairs = _bootstrap_pairs_from_sentences(content, min_want=max(need * 4, 24))
            if not emergency_pairs:
                emergency_pairs = _extract_concept_pairs(
                    re.sub(r"\s+", " ", (content or "").strip())[:16000],
                    max_pairs=max(32, n * 10),
                )
            emergency_rb = _build_rule_based_mcq(
                emergency_pairs
                if emergency_pairs
                else [("General concept", "The statement supported by the source text.")],
                num_questions=max(need * 3, need + 10),
                focus_topics=focus_topics,
                session_difficulty=difficulty,
                locale=locale,
            )
            for q in emergency_rb:
                if len(out_clean) >= n:
                    break
                if _try_push_quiz_question(
                    out_clean,
                    seen_stems,
                    q,
                    n=n,
                    locale=locale,
                    sess_diff=sess_diff,
                    source=source,
                    validation="strict",
                    allow_stem_suffix=True,
                ):
                    continue
                _try_push_quiz_question(
                    out_clean,
                    seen_stems,
                    q,
                    n=n,
                    locale=locale,
                    sess_diff=sess_diff,
                    source=source,
                    validation="structural",
                    allow_stem_suffix=True,
                )

    # Absolute last tier: count-fill (still blocks code / math / Part-B style junk).
    if len(out_clean) < n and allow_rb:
        for _wave in range(14):
            if len(out_clean) >= n:
                break
            need = n - len(out_clean)
            pcom: List[tuple[str, str]] = list(pairs) if pairs else []
            if len(pcom) < 6:
                pcom.extend(_bootstrap_pairs_from_sentences(content, min_want=max(24, need * 4)))
            if len(pcom) < 2:
                pcom = [("General concept", "The statement supported by the source text.")]
            fill_rb = _build_rule_based_mcq(
                pcom[: max(40, n * 8)],
                num_questions=max(need * 6, n + 14),
                focus_topics=focus_topics,
                session_difficulty=difficulty,
                locale=locale,
            )
            for q in fill_rb:
                if len(out_clean) >= n:
                    break
                _try_push_quiz_question(
                    out_clean,
                    seen_stems,
                    q,
                    n=n,
                    locale=locale,
                    sess_diff=sess_diff,
                    source=source,
                    validation="fill",
                    allow_stem_suffix=True,
                )

    # Hard guarantee: nuclear pads (no _try_push survivor — shared şık metinleri yüzünden tıkanmayı önler).
    if len(out_clean) < n:
        pad_tail = re.sub(r"\s+", " ", (source or "").strip())[:12000]
        _nuclear_fill_quiz_to_n(out_clean, n, pad_tail, locale, sess_diff, seen_stems)

    synthetic_added = 0
    if len(out_clean) < n:
        synthetic_added = _synthetic_backfill_to_n(out_clean, n, locale, sess_diff)
        if synthetic_added:
            log.info(
                "quiz: appended %s deterministic backfill MCQ(s) to reach requested count N=%s",
                synthetic_added,
                n,
            )

    if len(out_clean) < n:
        log.error(
            "quiz invariant broken: need=%s got=%s acc_len=%s final_len=%s pairs=%s rulebased=%s ollama_ok=%s cloud_ok=%s",
            n,
            len(out_clean),
            len(acc),
            len(final),
            len(pairs),
            allow_rb,
            ollama_ok,
            cloud_ok,
        )
        if not allow_rb:
            raise QuizGenerationError(
                "Quiz could not be produced because the model did not return enough valid items or the output failed validation. "
                "Try again with fewer questions and ensure the model output is strict JSON."
            )
        raise QuizGenerationError("The requested question count could not be produced. Please try again.")
    if len(out_clean) > n:
        out_clean = out_clean[:n]
    return _assign_unique_question_ids(out_clean)


# fn: generate_quiz | tr: quiz üret; hata olursa boş liste (uyumluluk) / en: generate quiz; return empty list on failure (compat)
def generate_quiz(
    content: str,
    num_questions: int = 5,
    max_topics: int = 5,
    max_content_chars: int = 15000,
    focus_topics: Optional[Sequence[str]] = None,
    challenge_topics: Optional[Sequence[str]] = None,
    locale: str = "en",
    difficulty: str = "normal",
    document_id: Optional[str] = None,
) -> List[QuizQuestion]:
    """Backward-compatible: same as ``generate_quiz_guaranteed`` but returns ``[]`` on failure."""
    try:
        return generate_quiz_guaranteed(
            content,
            num_questions=num_questions,
            max_topics=max_topics,
            max_content_chars=max_content_chars,
            focus_topics=focus_topics,
            challenge_topics=challenge_topics,
            locale=locale,
            difficulty=difficulty,
            document_id=document_id,
        )
    except QuizGenerationError:
        return []



# svc: ollama | tr: yerel ollama llm istemcisi (chat, ozet, quiz, koçluk) / en: local ollama llm client (chat, summary, quiz, coaching)

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from app.services.text_chunking import chunk_text_by_words, word_count


# fn: _base_url | tr: ollama sunucu adresi / en: ollama server url
def _base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


# fn: _model | tr: varsayılan genel model / en: default general model
def _model() -> str:
    return os.getenv("OLLAMA_MODEL", "llama3.2").strip()


# fn: _quiz_model | tr: quiz için özel model (phi3:mini) / en: quiz-specific model (phi3:mini)
def _quiz_model() -> str:
    raw = os.getenv("OLLAMA_QUIZ_MODEL", "").strip()
    if raw:
        return raw
    default_q = os.getenv("OLLAMA_QUIZ_DEFAULT_MODEL", "phi3:mini").strip()
    return default_q if default_q else _model()


# fn: _chat_model | tr: pdf chat icin model / en: model for pdf chat
def _chat_model() -> str:
    raw = os.getenv("OLLAMA_CHAT_MODEL", "").strip()
    return raw if raw else _model()


# fn: _timeout_seconds | tr: istek zaman asimi / en: request timeout
def _timeout_seconds() -> float:
    return float(os.getenv("OLLAMA_TIMEOUT", "180"))


# cfg: özet  prompt sabitleri (kod yok, öğretim modu) / en: summary prompt constants (no code, teaching mode)
_NO_CODE_IN_SUMMARY = (
    " Do not paste or echo source code, shell commands, or `pip install` lines. "
    "Describe what code does in plain language only."
)
_STRICT_PROSE_GUARD = (
    " CRITICAL: Your entire reply must be normal sentences and paragraphs. "
    "Absolutely forbid `import`, `from … import`, `def`, `class`, assignments, or API-like syntax from the source. "
    "If the input is a notebook or script, explain goals, steps, and ideas in words—never reproduce the code."
)
_CONCEPT_LENS = (
    " CONCEPT-FIRST FILTER (do mentally before writing): label each piece as "
    "CORE CONCEPTS (must teach), EXAMPLES (keep only simplified if they build understanding), "
    "CODE / SETUP / PRINT OUTPUT / RANDOM SEEDS / LIBRARY INSTALL (DELETE entirely), "
    "NOISE (DELETE). Never surface (CODE/SETUP/PRINTS/SEEDS) in your output. "
    "Ask: 'What is this unit teaching?' and 'What must a student understand for an exam?'—prioritize that."
)
_TEACHING_MODE = (
    " TEACHING MODE (not summarizing): You RE-TEACH the topic. Do not reuse the PDF’s wording, "
    "order, or slide-style fragments. Never copy distinctive phrases; write completely new sentences. "
    "Internally: name the main topic → reason step-by-step → link concepts (e.g. signal vs noise, central tendency "
    "vs whole-dataset picture, outliers vs conclusions)—then output ONLY the final handout with zero internal "
    "reasoning shown. "
    "Bad style: separate lines like 'Outlier detection…' then 'Central tendency…'. "
    "Good style: one woven explanation, e.g. descriptive statistics summarize data using measures such as mean, "
    "median, and mode, while outliers can skew those summaries so we interpret them carefully."
)
_ANTI_NOTE_SHAPE = (
    " If the result still looks like lecture notes (stacked topic labels, choppy clauses, or mirrored slide order), "
    "rewrite until it reads as spoken teaching."
)


# fn: _reteach_gate_enabled | tr: son kontrol gecisi acik mi / en: is reteach gate pass enabled
def _reteach_gate_enabled() -> bool:
    return os.getenv("SUMMARY_RETEACH_GATE", "true").lower() in ("1", "true", "yes")


# fn: _reteach_pdf_free_gate | tr: özeti pdf'siz anlaşılır hale getir / en: polish summary so pdf not needed
def _reteach_pdf_free_gate(draft: str, max_chars: int, strict_suffix: str) -> str:
    system = (
        "You validate and fix a student handout.\n"
        "(1) Ask internally: can a student understand the topic without the original PDF? If not, rewrite the whole "
        "handout in teaching mode.\n"
        "(2) If the opening still looks like copied slide bullets (short topic phrases as separate sentences or lines), "
        "merge into ONE flowing lecture paragraph.\n"
        "(3) Remove every question; use statements only.\n"
        "(4) Ensure no obvious reuse of source phrasing—use fresh sentences.\n"
        "(5) Keep structure: title line; blank; one paragraph; blank; 'Key concepts:'; lines '- Concept → explanation' "
        "(arrow between term and gloss).\n"
        "If already excellent, return almost unchanged. OUTPUT ONLY the handout—no validation commentary."
    )
    system = system + strict_suffix + _TEACHING_MODE + _CONCEPT_LENS
    user = (draft or "").strip()[: min(len(draft), 12000)]
    if not user:
        return ""
    return ollama_chat(user, system=system, options=_summary_options())


# fn: _final_polish_enabled | tr: profesor cilasi acik mi / en: is final polish pass enabled
def _final_polish_enabled() -> bool:
    return os.getenv("SUMMARY_FINAL_POLISH", "true").lower() in ("1", "true", "yes")


# fn: _professor_final_polish | tr: taslak özeti öğretim diline cilala / en: polish draft into teaching prose
def _professor_final_polish(draft: str, max_chars: int, strict_suffix: str) -> str:
    system = (
        "You are a professor polishing a draft into true TEACHING prose—not summarized notes.\n"
        "(1) After title + blank line: EXACTLY ONE continuous paragraph before 'Key concepts:'. "
        "Merge ideas; link concepts explicitly (e.g. how signal and noise differ, how central tendency summarizes a "
        "dataset, how outliers distort summaries). No choppy topic-label sentences.\n"
        "(2) Under 'Key concepts:', each line starts with '- ' and uses 'Concept → short explanation' (Unicode arrow "
        "between term and gloss). No bare labels.\n"
        "(3) Delete questions; replace with declarative teaching sentences.\n"
        "(4) If any sentence still echoes slide/PDF phrasing, rewrite in new words.\n"
        "(5) Strip code, setup, prints, seeds.\n"
        "(6) Internal check: could a student learn this without the PDF? If not, rewrite the opening paragraph.\n"
        "Preserve optional 'Examples:' section if present. OUTPUT ONLY the handout—no commentary."
    )
    system = system + strict_suffix + _TEACHING_MODE + _CONCEPT_LENS + _ANTI_NOTE_SHAPE
    user = (draft or "").strip()[: min(len(draft), 14000)]
    if not user:
        return ""
    return ollama_chat(user, system=system, options=_summary_options())


# fn: _maybe_polish_handout | tr: cilalama + reteach geçişlerini sırala / en: run polish and reteach passes
def _maybe_polish_handout(draft: str, max_chars: int, strict_suffix: str) -> str:
    text = (draft or "").strip()
    if not text:
        return ""
    out = text[:max_chars]
    if _final_polish_enabled():
        polished = _professor_final_polish(text, max_chars, strict_suffix).strip()
        if polished and len(polished) > 200:
            out = polished[:max_chars]
    if _reteach_gate_enabled():
        gated = _reteach_pdf_free_gate(out, max_chars, strict_suffix).strip()
        if gated and len(gated) > 200:
            return gated[:max_chars]
    return out


# fn: _two_pass_enabled | tr: iki geçişli özet açık mı / en: is two-pass summary enabled
def _two_pass_enabled() -> bool:
    return os.getenv("SUMMARY_TWO_PASS", "true").lower() in ("1", "true", "yes")


# fn: _tutor_scratchpad_from_chunk_notes | tr: chunk notlarından gizli öğretmen planı / en: hidden tutor plan from chunk notes
def _tutor_scratchpad_from_chunk_notes(merged_notes: str, max_in: int) -> str:
    system = (
        "You are an expert tutor preparing a lesson. You receive bullet notes extracted from many parts of a "
        "textbook or slides (order may be wrong, content may overlap).\n"
        "Do NOT write a student handout yet. Do NOT paraphrase sentence-by-sentence.\n"
        "Privately work out your understanding using ONLY these sections (use these headings exactly):\n"
        "## Main themes\n"
        "## Core concepts and definitions\n"
        "## Why these ideas matter (exam / practice)\n"
        "## How ideas connect\n"
        "## Order you would teach this in\n"
        "## Examples worth keeping (simplified)\n"
        "## Noise to ignore (code boilerplate, dataset filenames, etc.)\n"
        "## Classified: CORE vs EXAMPLES vs removed CODE/SETUP\n"
        "## Single-paragraph lecture arc (how you would explain the big picture in one spoken paragraph)\n\n"
        "Think step by step. Do not quote the PDF; capture ideas only. "
        "Name and link concepts (e.g. central tendency relates mean/median/mode to typical values; outliers pull summaries)."
        + _CONCEPT_LENS
        + _TEACHING_MODE
    )
    user = f"Chunk notes from the document:\n\n{merged_notes.strip()[:max_in]}"
    return ollama_chat(user, system=system, options=_summary_options())


# fn: _tutor_scratchpad_from_source | tr: kısa döküman için gizli öğretme planı / en: hidden tutor plan for short doc
def _tutor_scratchpad_from_source(source_excerpt: str, max_in: int) -> str:
    system = (
        "You are an expert tutor reading course material. Do NOT compress sentences. Do NOT output a summary.\n"
        "First build a PRIVATE teaching plan using ONLY these headings:\n"
        "## Main topic\n"
        "## What is really being taught\n"
        "## Why it matters\n"
        "## Key concepts to define for a student\n"
        "## Relationships and contrasts\n"
        "## Examples (if any) in simple terms\n"
        "## What to skip (noise, code, irrelevant data prep)\n"
        "## Classified: CORE vs EXAMPLES vs removed CODE/SETUP/PRINTS/SEEDS\n"
        "## Single-paragraph lecture arc (one coherent spoken explanation of the whole)\n\n"
        "Reason carefully. Do not copy source sentences into this scratchpad—ideas only. Use correct terminology."
        + _CONCEPT_LENS
        + _TEACHING_MODE
    )
    user = f"Course material:\n\n{source_excerpt.strip()[:max_in]}"
    return ollama_chat(user, system=system, options=_summary_options())


# fn: _student_handout_english | tr: ogrenci el kitabi formatinda ozet yaz / en: write student handout format summary
def _student_handout_english(
    tutor_material: str,
    min_chars: int,
    max_chars: int,
    strict_suffix: str,
) -> str:
    lo = max(0, int(min_chars)) if min_chars else 2200
    hi = max_chars
    system = (
        "You are in TEACHING MODE: you RE-TEACH the topic to a student before an exam. You are NOT summarizing and "
        "NOT mirroring slide structure. Ignore the original sentence order; build a fresh logical explanation from "
        "understanding.\n"
        + _TEACHING_MODE
        + "\n"
        "Before you write, understand the topic internally (main idea → steps → how concepts link). "
        "Output ONLY the finished handout—never show planning or headings like 'Step 1'.\n"
        + _CONCEPT_LENS
        + "\n"
        "STRICT layout:\n"
        "- Line 1: short document title (no 'Title:', no #).\n"
        "- Line 2: blank.\n"
        "- EXACTLY ONE paragraph (no line breaks inside): full, flowing lecture-style explanation. "
        "Weave related ideas (e.g. descriptive statistics use mean/median/mode to describe typical behavior; outliers "
        "can distort those summaries, so we interpret with care). Link abstractions: signal vs noise, central tendency "
        "vs whole distribution, etc., when relevant. Never paste PDF phrases.\n"
        "- Blank line.\n"
        "- Exact line: Key concepts:\n"
        "- 5–10 lines, each '- Concept → explanation' (arrow Unicode → between term and one-sentence gloss).\n"
        "- No questions anywhere—only statements.\n"
        "- Optional: blank line, Examples: , one short paragraph.\n"
        f"- Length about {lo}–{hi} characters; max {hi}. No code/setup/prints/seeds.\n"
        + _ANTI_NOTE_SHAPE
        + "\n"
        "Final internal test: PDF never seen—still clear? If not, rewrite before submitting."
    )
    system = system + strict_suffix
    user = (
        "Using the material below, write ONLY the student handout in the format specified.\n\n"
        f"{tutor_material.strip()[: min(len(tutor_material), 40000)]}"
    )
    raw = ollama_chat(user, system=system, options=_summary_options())
    return _maybe_polish_handout(raw, max_chars, strict_suffix)


# fn: _legacy_merge_english | tr: eski tek geçişli not birleştirme / en: legacy single-pass note merge
def _legacy_merge_english(merged: str, lo: int, hi: int, max_chars: int, strict_suffix: str) -> str:
    final_system = (
        "TEACHING MODE: Re-teach from fragmented notes—do NOT preserve note order or wording. New sentences only.\n"
        "Structure: (1) title line; (2) blank; (3) ONE paragraph, no internal line breaks—woven explanation with "
        "concept links; (4) blank; (5) 'Key concepts:'; (6) 5–10 lines '- Term → gloss' (arrow). "
        "No questions. No lecture-note stack. "
        f"Length {lo}–{hi}; max {hi}. No #. No 'Summary:' title."
        + _TEACHING_MODE
        + _ANTI_NOTE_SHAPE
    )
    final_system = final_system + strict_suffix + _CONCEPT_LENS
    final_user = f"Synthesize these notes:\n\n{merged}"
    out = ollama_chat(final_user, system=final_system, options=_summary_options())
    return _maybe_polish_handout((out or "").strip(), max_chars, strict_suffix)[:max_chars]


# fn: _summary_options | tr: özet için düşük temperature / en: low temperature for summaries
def _summary_options() -> Dict[str, Any]:
    raw = os.getenv("OLLAMA_SUMMARY_TEMPERATURE", "0.2").strip()
    try:
        t = float(raw)
    except ValueError:
        t = 0.2
    t = max(0.0, min(t, 0.9))
    return {"temperature": t}


# fn: ollama_chat | tr: tek tur llm cagrisi (/api/chat, yedek /api/generate) / en: single-turn llm call
def ollama_chat(
    user_prompt: str,
    system: Optional[str] = None,
    *,
    options: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    model: Optional[str] = None,
) -> str:
    base = _base_url()
    resolved = (model or "").strip()
    resolved = resolved if resolved else _model()
    timeout = float(timeout_seconds) if timeout_seconds is not None else _timeout_seconds()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    opts = options or {}
    with httpx.Client(timeout=timeout) as client:
        try:
            body: Dict[str, Any] = {
                "model": resolved,
                "messages": messages,
                "stream": False,
            }
            if opts:
                body["options"] = opts
            r = client.post(f"{base}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
            msg = (data.get("message") or {}).get("content") or ""
            if msg.strip():
                return msg.strip()
        except (httpx.HTTPError, KeyError, TypeError):
            pass

        # tr: chat başarısızsa generate dene / en: fallback to /api/generate
        full_prompt = user_prompt
        if system:
            full_prompt = f"{system}\n\n{user_prompt}"
        gen_body: Dict[str, Any] = {"model": resolved, "prompt": full_prompt, "stream": False}
        if opts:
            gen_body["options"] = opts
        try:
            r = client.post(f"{base}/api/generate", json=gen_body)
            r.raise_for_status()
            data = r.json()
            return (data.get("response") or "").strip()
        except (httpx.HTTPError, KeyError, TypeError):
            return ""


# fn: ollama_chat_messages | tr: çok tur sohbet (pdf tutor) / en: multi-turn chat (pdf tutor)
def ollama_chat_messages(
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    *,
    options: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> str:
    base = _base_url()
    resolved_model = (model or "").strip() or _chat_model()
    timeout = _timeout_seconds()
    payload_messages: List[Dict[str, str]] = []
    if system:
        payload_messages.append({"role": "system", "content": system})
    for m in messages:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        payload_messages.append({"role": role, "content": content})
    if len(payload_messages) <= (1 if system else 0):
        return ""

    opts = options or {}
    with httpx.Client(timeout=timeout) as client:
        try:
            body: Dict[str, Any] = {
                "model": resolved_model,
                "messages": payload_messages,
                "stream": False,
            }
            if opts:
                body["options"] = opts
            r = client.post(f"{base}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
            msg = (data.get("message") or {}).get("content") or ""
            if msg.strip():
                return msg.strip()
        except (httpx.HTTPError, KeyError, TypeError):
            pass

    # tr: chat başarısızsa tüm geçmişi tek prompt yap / en: flatten history into generate fallback
    parts: List[str] = []
    if system:
        parts.append(system)
    for m in payload_messages:
        if m["role"] == "system":
            continue
        parts.append(f"{m['role'].upper()}:\n{m['content']}")
    full_prompt = "\n\n".join(parts)
    gen_body: Dict[str, Any] = {"model": resolved_model, "prompt": full_prompt, "stream": False}
    if opts:
        gen_body["options"] = opts
    with httpx.Client(timeout=timeout) as client:
        try:
            r = client.post(f"{base}/api/generate", json=gen_body)
            r.raise_for_status()
            data = r.json()
            return (data.get("response") or "").strip()
        except (httpx.HTTPError, KeyError, TypeError):
            return ""


# fn: ollama_available | tr: ollama kullanımı açık mı / en: is ollama enabled
def ollama_available() -> bool:
    return os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")


# fn: _summarize_chunks_then_merge | tr: uzun pdf: parça parça özetle sonra birleştir / en: map-reduce summary for long pdfs
def _summarize_chunks_then_merge(
    snippet: str,
    max_chars: int,
    min_chars: int,
    max_in: int,
    output_locale: str = "en",
    strict_prose: bool = False,
    temperature: Optional[float] = None,
) -> str:
    chunk_words = int(os.getenv("SUMMARY_CHUNK_WORDS", "800").strip() or "800")
    overlap_w = int(os.getenv("SUMMARY_CHUNK_WORD_OVERLAP", "120").strip() or "120")
    chunk_words = max(200, min(chunk_words, 1200))
    overlap_w = max(0, min(overlap_w, chunk_words // 2))

    chunks = chunk_text_by_words(snippet, chunk_words, overlap_w)
    per_chunk_char_cap = int(os.getenv("SUMMARY_CHUNK_MAX_CHARS", "16000").strip() or "16000")
    per_chunk_char_cap = max(6000, min(per_chunk_char_cap, 32000))
    opts = dict(_summary_options())
    if temperature is not None:
        try:
            opts["temperature"] = max(0.0, min(float(temperature), 0.9))
        except (TypeError, ValueError):
            pass
    tr = output_locale == "tr"

    chunk_system = (
        "You extract study content from a PDF fragment. Write ONLY 3–6 bullet lines. "
        "Each line starts with '- ' then one short grammatical sentence in natural academic Turkish "
        "stating a fact, definition, method, or main idea from THIS fragment only. "
        "Skip boilerplate, page numbers, and broken words. Do not copy long code blocks; "
        "if the fragment is mostly code, say what it implements in one bullet. "
        "No introduction or conclusion lines."
        if tr
        else (
            "You are a professor scanning ONE fragment of course material. "
            "First classify mentally: CORE CONCEPTS / EXAMPLES (simplify) / CODE·SETUP·PRINTS·SEEDS (discard) / NOISE (discard). "
            "Do NOT summarize sentence-by-sentence; extract meaning.\n"
            "Ask: What topic is this teaching? What must the student understand?\n"
            "Output ONLY these labeled lines in English (exact labels):\n"
            "MAIN_TOPIC: <short name>\n"
            "KEY_IDEA: <one sentence: core teaching point in your words>\n"
            "WHY_IT_MATTERS: <one sentence>\n"
            "TEACH_SIMPLY: <2–4 sentences as if teaching a student; optional tiny example only if it clarifies>\n"
            "CONCEPTS: <comma-separated terms to define in the final handout>\n"
            "NOISE_SKIPPED: <list what you discarded: code, imports, prints, seeds, setup, paths, etc.>\n\n"
            "Rules: Never copy or closely paraphrase source sentences in TEACH_SIMPLY—re-explain in new words. "
            "Never carry code/setup/prints/seeds into TEACH_SIMPLY. "
            "Repair broken PDF wording mentally; do not quote garbage."
        )
    )
    if strict_prose:
        chunk_system = chunk_system + _STRICT_PROSE_GUARD + _CONCEPT_LENS
    else:
        chunk_system = chunk_system + _NO_CODE_IN_SUMMARY + _CONCEPT_LENS

    notes: List[str] = []
    # tr: her chunk'tan kısa not çıkar / en: extract short notes per chunk
    for idx, ch in enumerate(chunks):
        ch_trim = ch[: min(len(ch), per_chunk_char_cap)]
        up = (
            f"Parça {idx + 1} / {len(chunks)}.\n\n"
            f"{ch_trim}"
            if tr
            else (
                f"Fragment {idx + 1} of {len(chunks)}.\n\n"
                f"{ch_trim}"
            )
        )
        part = ollama_chat(up, system=chunk_system, options=opts)
        if part.strip():
            notes.append(part.strip())

    if not notes:
        return ""

    merged = "\n".join(notes)
    merged = merged.strip()[:max_in]

    lo = max(0, int(min_chars)) if min_chars else 2200
    hi = max_chars
    strict_suffix = _STRICT_PROSE_GUARD if strict_prose else _NO_CODE_IN_SUMMARY

    # tr: ingilizce: iki geçiş (scratchpad -> handout) / en: english two-pass scratchpad then handout
    if not tr and _two_pass_enabled():
        scratch = _tutor_scratchpad_from_chunk_notes(merged, max_in)
        if scratch.strip():
            out = _student_handout_english(scratch, min_chars, max_chars, strict_suffix)
            if out.strip():
                return out[:max_chars]
        out = _legacy_merge_english(merged, lo, hi, max_chars, strict_suffix)
        return (out or "").strip()[:max_chars]

    final_system = (
        "You write one excellent study summary in Turkish for a university student. "
        "Input: bullet notes taken from many parts of a PDF (order may be mixed). "
        "Your job: merge them into a SINGLE coherent summary that reflects the whole document’s topics. "
        "Infer structure: group related ideas; remove duplicates; fix any broken phrases from the notes. "
        "Write in your own clear Turkish sentences—do not paste bullet text verbatim as the whole answer. "
        f"Length: between about {lo} and {hi} characters; never exceed {hi}. "
        "Use 3–6 paragraphs separated by one blank line. "
        "Correct grammar, spelling, and punctuation. No markdown # headings. "
        "No title line like 'Özet:' or 'Summary:' and no meta commentary."
        if tr
        else (
            "You write one excellent study summary in English for a university student. "
            "Input: teaching notes from many parts of a PDF (order may be mixed). "
            "Teach the ideas: merge into ONE coherent explanation—not sentence-by-sentence paraphrase. "
            "Infer structure: group related ideas; remove duplicates; repair OCR/PDF artifacts in the notes. "
            "Write in your own clear sentences. "
            f"Length: between about {lo} and {hi} characters; never exceed {hi}. "
            "Use 3–6 paragraphs separated by one blank line. "
            "Correct grammar, spelling, and punctuation. No markdown # headings. "
            "No title line like 'Summary:' and no meta commentary."
        )
    )
    if strict_prose:
        final_system = final_system + _STRICT_PROSE_GUARD
    else:
        final_system = final_system + _NO_CODE_IN_SUMMARY

    final_user = (
        "Bu notları talimatlarına uygun tek bir özet halinde birleştir.\n\n"
        f"Notlar:\n{merged}"
        if tr
        else (
            "Synthesize these notes into the summary described in your instructions.\n\n"
            f"Notes:\n{merged}"
        )
    )
    out = ollama_chat(final_user, system=final_system, options=opts)
    return (out or "").strip()[:max_chars]


# fn: summarize_with_ollama | tr: genel özet fonksiyonu (kisa veya dokuman modu) / en: general summary function
def summarize_with_ollama(
    text: str,
    max_sentences: int = 4,
    max_chars: int = 1200,
    document_mode: bool = False,
    min_chars: int = 0,
    output_locale: str = "en",
    strict_prose: bool = False,
    skip_map_reduce: bool = False,
    temperature: Optional[float] = None,
) -> str:
    if document_mode:
        default_in = "72000"
    else:
        default_in = "14000"
    max_in = int(os.getenv("OLLAMA_SUMMARY_MAX_INPUT_CHARS", default_in))
    max_in = max(4000, min(max_in, 120000))
    snippet = (text or "").strip()[:max_in]
    if not snippet:
        return ""

    opts = dict(_summary_options())
    if temperature is not None:
        try:
            opts["temperature"] = max(0.0, min(float(temperature), 0.9))
        except (TypeError, ValueError):
            pass
    use_chunks = document_mode and os.getenv("SUMMARY_CHUNKED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    min_words = int(os.getenv("SUMMARY_CHUNK_MIN_WORDS", "450").strip() or "450")
    min_words = max(200, min(min_words, 5000))

    tr = output_locale == "tr"

    # tr: uzun döküman -> map-reduce / en: long document -> map-reduce
    if document_mode and use_chunks and not skip_map_reduce and word_count(snippet) > min_words:
        out = _summarize_chunks_then_merge(
            snippet,
            max_chars,
            min_chars,
            max_in,
            output_locale=output_locale,
            strict_prose=strict_prose,
            temperature=temperature,
        )
        if out.strip():
            return out

    if document_mode:
        lo = max(0, int(min_chars)) if min_chars else 2200
        hi = max_chars
        system = (
            "You are an expert academic writing tutor. Produce ONE summary in fluent Turkish. "
            "Read the source as lecture or textbook material. Explain what the document is about: "
            "main topics, definitions, methods, and conclusions—in your own words in Turkish. "
            "Do NOT dump raw text, code listings, or PDF extraction garbage. If the source has broken "
            "spacing or odd line breaks, mentally repair it and write normal sentences. "
            "If there is code, summarize what it does in prose unless a one-line example is essential. "
            f"Length: stay between roughly {lo} and {hi} characters; hard maximum {hi}. "
            "Structure: 3–6 paragraphs, each 3–6 sentences, separated by one blank line. "
            "Use Turkish discourse markers where helpful (ancak, bu nedenle, buna karşın). "
            "Grammar and punctuation must be correct. No # markdown headings, no fenced code blocks "
            "unless quoting code is unavoidable. No greeting or filler endings like 'Sonuç olarak' alone."
            if tr
            else (
                "You are an expert academic writing tutor. Produce ONE summary in fluent English. "
                "Read the source as lecture or textbook material. Explain what the document is about: "
                "main topics, definitions, methods, and conclusions—in your own words. "
                "Do NOT dump raw text, code listings, or PDF extraction garbage. If the source has broken "
                "spacing or odd line breaks, mentally repair it and write normal sentences. "
                "If there is code, summarize what it does in prose unless a one-line example is essential. "
                f"Length: stay between roughly {lo} and {hi} characters; hard maximum {hi}. "
                "Structure: 3–6 paragraphs, each 3–6 sentences, separated by one blank line. "
                "Use connectors (However, Therefore, In contrast) where helpful. "
                "Grammar and punctuation must be correct. No # markdown headings, no fenced code blocks "
                "unless quoting code is unavoidable. No greeting or 'In conclusion' filler."
            )
        )
        system = system + (_STRICT_PROSE_GUARD if strict_prose else _NO_CODE_IN_SUMMARY)
        user_prompt = (
            "Aşağıdaki metni sınav tekrarı için özetle. Ders materyalinin anlamına ve yapısına odaklan; "
            "biçimlendirme artifaktlarını kopyalama.\n\n"
            f"Metin:\n{snippet}"
            if tr
            else (
                "Summarize the following text for exam revision. Focus on meaning and structure of the course "
                "material, not on reproducing formatting artifacts. Ignore broken PDF spacing and junk symbols—"
                "write clean prose. Do not paste slide-style diagrams (no pipes, triangle symbols, or arrow glyphs "
                "as layout); describe layers and steps in full sentences.\n\n"
                f"Text:\n{snippet}"
            )
        )
    else:
        system = (
            "You are a study assistant. Write polished, readable academic Turkish. "
            "Structure the answer for a student: each main idea in its own paragraph, "
            "with a fully blank line between paragraphs and between bullet groups. "
            "When listing related points, use lines starting with '- ' (hyphen and space), "
            "one item per line. "
            "No title line, no greeting, no markdown headings (#), no meta phrases."
            if tr
            else (
                "You are a study assistant. Write polished, readable academic English. "
                "Structure the answer for a student: each main idea in its own paragraph, "
                "with a fully blank line between paragraphs and between bullet groups. "
                "When listing related points, use lines starting with '- ' (hyphen and space), "
                "one item per line. "
                "No title line, no greeting, no markdown headings (#), no 'Here is the summary'. "
                "Use full sentences only—do not build fake flowcharts with |, ▼, or arrow characters."
            )
        )
        user_prompt = (
            f"Aşağıdaki materyali en fazla {max_sentences} paragraf veya kısa madde grubunda özetle "
            f"(tek blok metin değil). Anahtar terimleri ve tanımları koru. Yaklaşık {max_chars} karakteri geçme.\n\n"
            f"Metin:\n{snippet}"
            if tr
            else (
                f"Produce a structured summary of the material below in at most {max_sentences} "
                f"paragraphs or short bullet groups (not one dense block). "
                f"Preserve key terms and definitions. Stay under roughly {max_chars} characters.\n\n"
                f"Text:\n{snippet}"
            )
        )
    out = ollama_chat(user_prompt, system=system, options=opts)
    return out[:max_chars] if out else ""


# fn: summarize_document_english_with_ollama | tr: once ingilizce döküman özeti / en: document summary in english first
def summarize_document_english_with_ollama(
    text: str,
    max_chars: int,
    min_chars: int,
    strict_prose: bool = False,
) -> str:
    default_in = "72000"
    max_in = int(os.getenv("OLLAMA_SUMMARY_MAX_INPUT_CHARS", default_in))
    max_in = max(4000, min(max_in, 120000))
    snippet = (text or "").strip()[:max_in]
    if not snippet:
        return ""

    use_chunks = os.getenv("SUMMARY_CHUNKED", "true").lower() in ("1", "true", "yes")
    min_words = int(os.getenv("SUMMARY_CHUNK_MIN_WORDS", "450").strip() or "450")
    min_words = max(200, min(min_words, 5000))
    wc = word_count(snippet)

    if use_chunks and wc > min_words:
        out = _summarize_chunks_then_merge(
            snippet,
            max_chars,
            min_chars,
            max_in,
            output_locale="en",
            strict_prose=strict_prose,
            temperature=None,
        )
        if out.strip():
            return out

    return document_summary_english_two_pass(
        snippet,
        min_chars=min_chars,
        max_chars=max_chars,
        strict_prose=strict_prose,
        max_in=max_in,
    )


# fn: document_summary_english_two_pass | tr: kısa döküman için iki geçişli özet / en: two-pass summary for short docs
def document_summary_english_two_pass(
    snippet: str,
    min_chars: int,
    max_chars: int,
    strict_prose: bool,
    max_in: int,
) -> str:
    strict_suffix = _STRICT_PROSE_GUARD if strict_prose else _NO_CODE_IN_SUMMARY
    ex = (snippet or "").strip()[:max_in]
    if not ex:
        return ""
    if _two_pass_enabled():
        scratch = _tutor_scratchpad_from_source(ex, max_in)
        if scratch.strip():
            out = _student_handout_english(scratch, min_chars, max_chars, strict_suffix)
            if out.strip():
                return out[:max_chars]
    bridge = (
        "You must infer the topic, core concepts, and how they connect—without sentence-level copying. "
        "Skip code and irrelevant data-prep. Then output the student handout in the required format.\n\n"
        f"Source:\n{ex}"
    )
    out = _student_handout_english(bridge, min_chars, max_chars, strict_suffix)
    return (out or "").strip()[:max_chars]


# fn: translate_summary_to_turkish | tr: ingilizce özeti türkçeye çevir / en: translate english summary to turkish
def translate_summary_to_turkish(english_summary: str, max_chars: int) -> str:
    if not (english_summary or "").strip():
        return ""
    stripped = english_summary.strip()
    cap = min(len(stripped), max(max_chars * 3, 12000))
    sample = stripped[:cap]
    system = (
        "Translate the English handout into natural Turkish TEACHING prose (same pedagogy—not literal note translation).\n"
        "- Line 1: title; blank line.\n"
        "- ONE continuous Turkish paragraph (no line breaks inside)—same flowing re-teach style.\n"
        "- Blank line; then 'Temel kavramlar:' (or natural equivalent).\n"
        "- Each line '- Kavram → kısa açıklama' (keep the → pattern).\n"
        "- 'Examples:' if present.\n"
        "No questions. No code. A student without the PDF must still understand. No commentary."
    )
    user = f"English handout:\n\n{sample}"
    out = ollama_chat(user, system=system, options=_summary_options())
    return (out or "").strip()[: max(max_chars + 2000, 5000)]


# fn: repair_extracted_text_with_ollama | tr: pdf metnindeki bozuklukları düzelt (özet degil) / en: fix ocr-like pdf text noise
def repair_extracted_text_with_ollama(text: str) -> str:
    cap = int(os.getenv("SUMMARY_REPAIR_MAX_CHARS", "6000").strip() or "6000")
    cap = max(2000, min(cap, 14000))
    full = (text or "").strip()
    if len(full) < 500 or len(full) > cap * 3:
        return full
    system = (
        "You repair noisy PDF-extracted text only. Fix spaces wrongly inserted inside words, "
        "join broken line wraps into full sentences, and remove obvious table separator junk. "
        "Output plain text in the same language as the input. "
        "Do NOT summarize, do NOT omit sections, do NOT add commentary."
    )
    user = full[:cap]
    out = ollama_chat(user, system=system, options=_summary_options())
    out = (out or "").strip()
    if len(out) < len(user) * 0.4:
        return full
    if len(full) <= cap:
        return out
    return out + "\n\n" + full[len(user) :].lstrip()


# fn: coach_message_with_ollama | tr: quiz sonucuna gore kısa kocluk mesajı / en: short coaching message from quiz results
def coach_message_with_ollama(
    score_percentage: float,
    topic_summary: list,
    user_id: int,
    output_locale: str = "en",
) -> str:
    tr = output_locale == "tr"
    system = (
        "You are a supportive study coach. Write 2–4 short sentences in Turkish, warm but professional. "
        "No Markdown. Sound specific to this learner, not generic."
        if tr
        else (
            "You are a supportive study coach. Write 2–4 short sentences in English, warm but professional. "
            "No Markdown. Sound specific to this learner, not generic."
        )
    )
    payload = {
        "user_id": user_id,
        "overall_score_percent": round(score_percentage, 1),
        "topics": topic_summary,
    }
    user_prompt = (
        "Bu quiz sonucuna göre kişiselleştirilmiş bir teşvik ve çok kısa bir çalışma planı ver. "
        "Zayıf konularda ne yapılacağını ve güçlü konuların nasıl korunacağını söyle.\n\n"
        f"Veri (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        if tr
        else (
            "Based on this quiz outcome, give a personalized nudge plus a tiny study plan. "
            "Say what to do on weak topics and how to maintain strong ones.\n\n"
            f"Data (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    return ollama_chat(user_prompt, system=system, options={"temperature": 0.5})

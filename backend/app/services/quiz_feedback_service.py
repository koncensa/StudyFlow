# svc: quiz_feedback_service | tr: quiz sonrası yanlış cevaplar için koçluk geri bildirimi üret / en: generate coaching feedback for wrong quiz answers

from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.quiz_schema import QuizQuestion, QuizQuestionFeedback

# tr: formül sorularında anahtar kelimeler (ingilizce) / en: formula-related keywords (english)
_FORMULA_HINTS_EN = (
    "formula",
    "equation",
    "derivative",
    "integral",
    "matrix",
    "variance",
    "standard deviation",
    "mean",
    "median",
    "mode",
    "probability",
    "log",
    "regression",
)
# tr: formül sorularında anahtar kelimeler (türkçe) / en: formula-related keywords (turkish)
_FORMULA_HINTS_TR = (
    "formül",
    "denklem",
    "türev",
    "integral",
    "matris",
    "varyans",
    "standart sapma",
    "ortalama",
    "medyan",
    "mod",
    "olasılık",
    "logaritma",
    "regresyon",
)
# tr: dikkatsizlik ipuçları (ingilizce) / en: careless-reading keywords (english)
_CARELESS_HINTS_EN = ("not ", "except", "least", "most", "false", "true", "always", "never")
# tr: dikkatsizlik ipuçları (türkçe) / en: careless-reading keywords (turkish)
_CARELESS_HINTS_TR = ("değil", "hariç", "en az", "en çok", "yanlış", "doğru", "her zaman", "asla")

_MAX_Q_TEXT = 6000
_MAX_WHY_LEN = 1100
_MAX_TEACH_LEN = 1200
_MAX_HINT_LEN = 640
_MAX_COACH_SECTION = 1400

_FALLBACK_WHY_TR = "Bu soruda konuyu karıştırmışsın."
_FALLBACK_WHY_EN = "On this item you mixed up the underlying ideas."
_FALLBACK_NEXT_TR = "Bu konuyu tekrar edip mini quiz çöz."
_FALLBACK_NEXT_EN = "Review this idea briefly, then try a short mini quiz."


# fn: _trim_feedback_dangling_tail | tr: metin sonundaki yarım kelime veya bağlacı temizle / en: trim dangling words/conjunctions at text end
def _trim_feedback_dangling_tail(t: str) -> str:
    out = re.sub(r"\s+", " ", (t or "").strip())
    if not out:
        return out
    while True:
        prev = out
        out = re.sub(
            r"\b(and|or|because|if|that|which|with|for|of|to|from|into|about|ve|veya|çünkü|için|eğer|ile|gibi|kadar|göre|üzerine)\s*$",
            "",
            out,
            flags=re.I,
        ).strip()
        out = re.sub(r"[,;:]\s*$", "", out).strip()
        if out == prev:
            break
    return out


# fn: _soft_cap_text | tr: metni uzunluk sınırında kelime ortasında kesmeden kırp / en: cap text length without mid-word cuts
def _soft_cap_text(s: Optional[str], max_len: int) -> Optional[str]:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    if len(t) <= max_len:
        return t
    chunk = t[:max_len]
    for sep in (". ", "! ", "? ", ".\n", "。\n"):
        cut = chunk.rfind(sep)
        if cut >= max_len // 4:
            return _trim_feedback_dangling_tail(chunk[: cut + len(sep)].rstrip())
    sp = chunk.rfind(" ")
    if sp >= max_len // 3:
        capped = _trim_feedback_dangling_tail(chunk[:sp].rstrip())
        return f"{capped}…" if capped else None
    capped = _trim_feedback_dangling_tail(chunk.rstrip())
    return f"{capped}…" if capped else None


# fn: _clip_feedback_text | tr: geri bildirim alanını en fazla uzunluğa kırp / en: clip feedback field to max length
def _clip_feedback_text(s: Optional[str], max_len: int) -> Optional[str]:
    return _soft_cap_text(s, max_len)


# tr: belirsiz/genel neden ifadelerini reddet (regex) / en: reject vague generic why-phrases (regex)
_GENERIC_WHY_RE = re.compile(
    r"\byour choice\b|\bdoes not match\b|\bnot match\b|\bincorrect answer\b|\bis incorrect\b|"
    r"seçimin|eşleşmiyor|yanlış\s+cevap|doğru\s+değil|cevabın\s+uyumsuz",
    re.I,
)
# tr: belirsiz koçluk ifadelerini reddet (regex) / en: reject vague coaching phrases (regex)
_GENERIC_COACH_RE = re.compile(
    r"\bmisread\b|\bmay have misread\b|\bread too quickly\b|\bquick slip\b|"
    r"yanlış\s+yorumlamış\s+olabilirsin|yanlış\s+oku|hızlı\s+oku|olabilirsin|"
    r"karıştırmış\s+olabilirsin|dikkat\s+kayması|"
    r"you may have\b|\bmight have\b|\bpossibly\b",
    re.I,
)


# fn: _too_short | tr: metin çok kısa mı / en: check if text is too short
def _too_short(s: Optional[str], min_len: int = 10) -> bool:
    t = (s or "").strip()
    return len(t) < min_len


# fn: _normalize_concept_list | tr: karıştırılan kavram listesini düzenle / en: normalize confused-concepts list
def _normalize_concept_list(raw: Any, topic: str, tr: bool) -> List[str]:
    out: List[str] = []
    if isinstance(raw, list):
        for x in raw:
            t = str(x).strip()
            if t and t not in out:
                out.append(t)
    if not out:
        out = _split_topic_concepts(topic)
    if not out:
        out = [topic.strip() or ("Genel" if tr else "General")]
    return out[:4]


# fn: _finalize_wrong_feedback | tr: yanlış cevap geri bildirimini tamamla ve doğrula / en: finalize and validate wrong-answer feedback
def _finalize_wrong_feedback(
    fb: QuizQuestionFeedback,
    q: QuizQuestion,
    tr: bool,
) -> QuizQuestionFeedback:
    topic = (fb.topic or ("Genel" if tr else "General")).strip() or ("Genel" if tr else "General")
    sel = fb.selected_answer or ""
    cor = fb.correct_answer or ""
    err = (fb.error_type or "").strip().lower()
    if err not in ("concept_mixup", "formula_mixup", "careless", "interpretation"):
        err = _infer_error_type(q, sel, cor, tr)
    t_ym, t_wi, t_ct, t_cae, tpl_hint, tpl_concepts = _template_feedback(err, topic, sel, cor, tr, q)

    concepts = _normalize_concept_list(fb.confused_concepts, topic, tr)

    def _bad(s: str, min_len: int = 24) -> bool:
        t = (s or "").strip()
        if _too_short(t, min_len):
            return True
        if _GENERIC_WHY_RE.search(t) or _GENERIC_COACH_RE.search(t):
            return True
        return False

    your_mistake = (getattr(fb, "your_mistake", None) or "").strip()
    if _bad(your_mistake, 28):
        your_mistake = t_ym

    why_incorrect = (getattr(fb, "why_incorrect", None) or "").strip()
    if _bad(why_incorrect, 28):
        legacy = (fb.why_wrong or "").strip()
        why_incorrect = legacy if not _bad(legacy, 20) else t_wi
    if _bad(why_incorrect, 20):
        why_incorrect = t_wi

    correct_thinking = (getattr(fb, "correct_thinking", None) or "").strip()
    if _bad(correct_thinking, 28):
        legacy_t = (fb.teaching_snippet or "").strip()
        correct_thinking = legacy_t if not _bad(legacy_t, 20) else t_ct
    if _bad(correct_thinking, 20):
        correct_thinking = t_ct

    correct_answer_explained = (getattr(fb, "correct_answer_explained", None) or "").strip()
    if _bad(correct_answer_explained, 28):
        correct_answer_explained = t_cae

    why_wrong = _clip_feedback_text(why_incorrect, _MAX_WHY_LEN)
    teach_snip = _clip_feedback_text(correct_thinking, _MAX_TEACH_LEN)

    nxt = (fb.hint or "").strip()
    if _too_short(nxt, 12):
        nxt = tpl_hint
    if _too_short(nxt, 8):
        nxt = _FALLBACK_NEXT_TR if tr else _FALLBACK_NEXT_EN

    tback = (getattr(fb, "teach_back_prompt", None) or "").strip()
    if _too_short(tback, 16):
        tback = _teach_back_prompt_line(err, topic, tr, getattr(q, "question_type", None))

    return fb.model_copy(
        update={
            "error_type": err,
            "confused_concepts": concepts,
            "your_mistake": _clip_feedback_text(your_mistake, _MAX_COACH_SECTION),
            "why_incorrect": _clip_feedback_text(why_incorrect, _MAX_COACH_SECTION),
            "correct_thinking": _clip_feedback_text(correct_thinking, _MAX_COACH_SECTION),
            "correct_answer_explained": _clip_feedback_text(correct_answer_explained, _MAX_COACH_SECTION),
            "why_wrong": why_wrong,
            "teaching_snippet": teach_snip,
            "hint": _clip_feedback_text(nxt, _MAX_HINT_LEN),
            "teach_back_prompt": _clip_feedback_text(tback, 420),
        }
    )


# fn: _ollama_enabled | tr: ollama ile zenginleştirme açık mı / en: is ollama enrichment enabled
def _ollama_enabled() -> bool:
    v = os.getenv("QUIZ_FEEDBACK_OLLAMA", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")


# fn: _is_tr | tr: yerel ayar türkçe mi / en: is locale turkish
def _is_tr(locale: str) -> bool:
    return (locale or "en").strip().lower().startswith("tr")


# fn: _tokens | tr: metinden kelime kümesi çıkar / en: extract word token set from text
def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ0-9]+", (s or "").lower()) if len(w) > 1}


# fn: _token_jaccard | tr: iki metin arası benzerlik (jaccard) / en: text similarity via jaccard overlap
def _token_jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# fn: _infer_error_type | tr: yanlış cevabın hata türünü tahmin et / en: infer error type for wrong answer
def _infer_error_type(
    q: QuizQuestion,
    selected: str,
    correct: str,
    tr: bool,
) -> str:
    qt = (getattr(q, "question_type", None) or "").strip().lower()
    if qt == "formula":
        return "formula_mixup"
    if qt == "interpretation":
        return "interpretation"
    stem = (q.question_text or "").lower()
    sel, cor = (selected or "").lower(), (correct or "").lower()
    hints = _FORMULA_HINTS_TR if tr else _FORMULA_HINTS_EN
    careless_kw = _CARELESS_HINTS_TR if tr else _CARELESS_HINTS_EN
    if any(h in stem for h in hints):
        return "formula_mixup"
    if any(k in stem for k in careless_kw) and _token_jaccard(selected, correct) >= 0.35:
        return "careless"
    if _token_jaccard(selected, correct) >= 0.45:
        return "careless"
    if len(_tokens(selected)) <= 2 and len(_tokens(correct)) >= 4:
        return "careless"
    if "which" in stem or "what" in stem or "nedir" in stem or "hangi" in stem:
        return "interpretation"
    return "concept_mixup"


# fn: _split_topic_concepts | tr: konu başlığını alt kavramlara böl / en: split topic label into sub-concepts
def _split_topic_concepts(topic: str) -> List[str]:
    parts = re.split(r"[,;/]|(?:\s+and\s+)|(?:\s+ve\s+)", topic or "", flags=re.I)
    return [p.strip() for p in parts if p and len(p.strip()) > 1][:3]


# fn: _teach_back_prompt_line | tr: öğrenciye geri anlatım görevi üret / en: generate teach-back prompt for learner
def _teach_back_prompt_line(
    error_type: str,
    topic: str,
    tr: bool,
    question_type: Optional[str],
) -> str:
    qt = (question_type or "").strip().lower()
    if tr:
        if error_type == "formula_mixup":
            return f"«{topic}» için hangi sembol veya terimi doğru cevapla karıştırdığını tek cümlede yaz."
        if error_type == "interpretation":
            return "Bu soruda kök tam olarak ne istiyor? Tek cümlede yaz."
        if error_type == "careless":
            return "Hangi anahtar kelimeyi (ör. 'değil', 'en az') hızlı okumada kaçırmış olabilirsin — yaz."
        if qt == "comparison":
            return f"«{topic}» içinde karşılaştırdığın iki fikri tek cümlede ayır."
        return f"«{topic}» kavramını kendi cümlelerinle tek satırda açıkla."
    if error_type == "formula_mixup":
        return f"In one sentence, which symbol or term did you confuse with the correct answer on «{topic}»?"
    if error_type == "interpretation":
        return "In one sentence, what was the stem actually asking for?"
    if error_type == "careless":
        return "Which keyword (NOT / least / except) might you have read too quickly?"
    if qt == "comparison":
        return f"In one line, separate the two ideas you compared within «{topic}»."
    return f"In your own words, explain «{topic}» in one clear sentence."


# fn: _stem_clip | tr: soru kökünü kısa kırp / en: clip question stem to short length
def _stem_clip(q: QuizQuestion, max_len: int = 360) -> str:
    t = re.sub(r"\s+", " ", (q.question_text or "").strip())
    if len(t) <= max_len:
        return t
    capped = _soft_cap_text(t, max_len)
    return capped or (t[: max_len - 1] + "…")


# fn: _answer_clip | tr: cevap metnini kısa kırp / en: clip answer text to short length
def _answer_clip(s: str, max_len: int = 220) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if len(t) <= max_len:
        return t
    capped = _soft_cap_text(t, max_len)
    return capped or (t[: max_len - 1] + "…")


# fn: _template_feedback | tr: hata türüne göre şablon koçluk metni üret / en: generate template coaching text by error type
def _template_feedback(
    error_type: str,
    topic: str,
    selected: str,
    correct: str,
    tr: bool,
    q: QuizQuestion,
) -> Tuple[str, str, str, str, str, List[str]]:
    concepts = _split_topic_concepts(topic)
    if not concepts:
        concepts = [topic or ("Genel" if tr else "General")]
    stem = _stem_clip(q)
    sel_c = _answer_clip(selected)
    cor_c = _answer_clip(correct)
    book = (q.explanation or "").strip()

    if tr:
        type_labels = {
            "concept_mixup": "kavram karışıklığı",
            "formula_mixup": "formül / sembol ile kavramı birbirine karıştırma",
            "careless": "kökteki kısıt veya anahtar kelimeyi gözden kaçırma",
            "interpretation": "metinden çıkarım ile tanımı veya farklı bir alt soruyu birleştirme",
        }
        label = type_labels.get(error_type, "yanlış eşleştirme")
        your_mistake = (
            f"«{sel_c}» şıkkını seçtin. Bu soruda «{topic}» bağlamında hata türü: {label}. "
            f"Soru kökünün özü: «{stem}»"
        )
        why_incorrect = (
            f"Soru şunu net olarak soruyor: «{stem}» "
            f"Bu istekle «{sel_c}» örtüşmüyor; çünkü bu seçenek soruda aranan yanıt anahtarını taşımıyor. "
            f"Doğru şık «{cor_c}» ise tam olarak bu anahtarı karşılıyor."
        )
        correct_thinking = (
            "Önce kökte sorulan işi tek cümlede yaz (tanımla mı, listele mi, hangi durumda geçerli mi). "
            f"Sonra her şıkkı bu cümleyle tek tek karşılaştır; «{concepts[0]}» etiketini taşıyan ama sorulan işi yapmayan şıkları ele. "
            f"Bu soruda hedef kavram «{topic}» ve doğru eşleşme «{cor_c}»."
        )
        if book:
            correct_answer_explained = (
                f"Doğru cevap «{cor_c}». Kaynak açıklamasıyla uyum: {book[:700]}{'…' if len(book) > 700 else ''}"
            )
        else:
            correct_answer_explained = (
                f"Doğru cevap «{cor_c}»; çünkü kökteki görev bu seçenekte verilen tanım / ölçüt / ilişki ile doğrudan örtüşüyor. "
                f"«{sel_c}» ise aynı başlık altında olsa bile soruda istenen spesifik yanıtı vermiyor."
            )
        hint = (
            f"PDF'de «{concepts[0]}» geçen bölümü aç; soru kökündeki fiille eşleşen tek cümleyi işaretle; "
            "sonra aynı konudan 3 soruluk mini quiz dene."
        )
    else:
        type_labels = {
            "concept_mixup": "concept mix-up",
            "formula_mixup": "mixing symbols/formulas with the underlying concept",
            "careless": "missing a stem constraint or qualifier",
            "interpretation": "matching the stem to the wrong sub-question within the topic",
        }
        label = type_labels.get(error_type, "mismatch")
        your_mistake = (
            f"You selected «{sel_c}». Under «{topic}», classify this miss as: {label}. "
            f"Stem gist: «{stem}»"
        )
        why_incorrect = (
            f"The stem asks: «{stem}» "
            f"That request is not satisfied by «{sel_c}»: that option does not carry the answer key the stem checks for. "
            f"The keyed correct option is «{cor_c}», which matches what the stem is demanding."
        )
        correct_thinking = (
            "First restate the stem's job in one sentence (define, compare, pick the case that applies, etc.). "
            f"Then discriminate each option against that sentence; eliminate options that only sound related to «{concepts[0]}» but do not perform the requested job. "
            f"Here the target topic is «{topic}» and the option that performs the job is «{cor_c}»."
        )
        if book:
            correct_answer_explained = (
                f"The correct answer is «{cor_c}». Why it fits: {book[:700]}{'…' if len(book) > 700 else ''}"
            )
        else:
            correct_answer_explained = (
                f"The correct answer is «{cor_c}» because it is the option whose meaning directly fulfills the stem's task "
                f"(definition, criterion, or relationship being tested). «{sel_c}» may look adjacent, but it does not deliver the specific response the stem requires."
            )
        hint = (
            f"Open the PDF section that defines «{concepts[0]}», underline the one sentence that matches the stem's verb, "
            "then run a 3-question micro-quiz on the same idea."
        )
    return your_mistake, why_incorrect, correct_thinking, correct_answer_explained, hint, concepts


# fn: _parse_enrich_blob | tr: ollama json cevabını ayrıştır / en: parse ollama json enrichment response
def _parse_enrich_blob(raw: str, chunk: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    if not raw:
        return out
    try:
        m = re.search(r"\{[\s\S]*\}\s*$", raw.strip())
        blob = json.loads(m.group(0) if m else raw)
        items_list = blob.get("items") or []
        index_by_pos = [int(x["index"]) for x in chunk if isinstance(x.get("index"), int)]
        for pos, it in enumerate(items_list):
            if not isinstance(it, dict):
                continue
            idx_raw = it.get("index")
            try:
                idx = int(idx_raw)
            except (TypeError, ValueError):
                idx = -1
            if idx < 0:
                if pos < len(chunk) and isinstance(chunk[pos].get("index"), int):
                    idx = int(chunk[pos]["index"])
                elif pos < len(index_by_pos):
                    idx = index_by_pos[pos]
                else:
                    continue
            cc = it.get("confused_concepts")
            if not isinstance(cc, list):
                cc = []
            out[idx] = {
                "error_type": str(it.get("error_type") or "concept_mixup"),
                "why_wrong": str(it.get("why_wrong") or "").strip(),
                "teaching_snippet": str(it.get("teaching_snippet") or "").strip(),
                "your_mistake": str(it.get("your_mistake") or "").strip(),
                "why_incorrect": str(it.get("why_incorrect") or "").strip(),
                "correct_thinking": str(it.get("correct_thinking") or "").strip(),
                "correct_answer_explained": str(it.get("correct_answer_explained") or "").strip(),
                "hint": str(it.get("hint") or "").strip(),
                "teach_back_prompt": str(it.get("teach_back_prompt") or "").strip(),
                "confused_concepts": cc,
            }
        return out
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


# fn: _ollama_enrich_chunk | tr: yanlış cevap grubunu ollama ile zenginleştir / en: enrich wrong-answer chunk via ollama
def _ollama_enrich_chunk(
    chunk: List[Dict[str, Any]],
    tr: bool,
) -> Dict[int, Dict[str, Any]]:
    if not chunk:
        return {}
    try:
        from app.services.ollama_service import ollama_available, ollama_chat_messages

        if not ollama_available():
            return {}
    except Exception:
        return {}

    lang = "Turkish" if tr else "English"
    system = (
        f"You are a rigorous tutor. Reply ONLY with valid JSON (no markdown fences). Language: {lang}.\n"
        "Input is a JSON array of wrong MCQ attempts. For EVERY input row output one object in "
        '`"items"` in the SAME ORDER. Keys (all required, non-empty strings unless noted):\n'
        "- index (number, MUST match the input row's index),\n"
        "- error_type: one of concept_mixup, formula_mixup, careless, interpretation,\n"
        "- your_mistake: 2-4 sentences. Name the exact option text the learner picked and the mistake category "
        "(concept mix-up vs formula vs mis-applied interpretation vs missed stem constraint). Quote short phrases from stem/options when helpful.\n"
        "- why_incorrect: 2-4 sentences. Explain precisely why the selected option fails the stem's requirement—"
        "tie mismatch to definitions, conditions, or scope. Do NOT blame vague reading; no 'you may have misread'.\n"
        "- correct_thinking: 2-4 sentences. Step-by-step discrimination: how to read the stem, what to check in each option, what signal picks the key.\n"
        "- correct_answer_explained: 2-4 sentences. State the correct option and why it satisfies the stem; contrast with the wrong pick.\n"
        "- why_wrong: DUPLICATE or concise summary of why_incorrect (1-2 sentences) for backward compatibility,\n"
        "- teaching_snippet: DUPLICATE or concise summary of correct_thinking (1-2 sentences),\n"
        "- hint: one concrete next action referencing the topic/PDF (one sentence),\n"
        "- teach_back_prompt: one short active task for the learner,\n"
        "- confused_concepts: JSON array of 1-3 short strings (key terms only).\n"
        'Return exactly: {{"items":[...]}} with the same number of objects as input rows.\n'
        "Banned vague phrases (any language): misread, read too quickly, may have, possibly, yanlış okumuş, olabilirsin, dikkat kayması as the whole explanation.\n"
        "Each paragraph must reference concrete ideas from that row's question, selected, and correct fields."
    )
    user_payload = json.dumps(chunk, ensure_ascii=False)
    raw = ollama_chat_messages(
        [{"role": "user", "content": user_payload}],
        system=system,
        options={"temperature": 0.2, "num_predict": 2048},
    )
    return _parse_enrich_blob(raw or "", chunk)


# fn: _ollama_batch_enrich | tr: tüm yanlış cevapları parça parça ollama ile zenginleştir / en: batch-enrich all wrong answers via ollama
def _ollama_batch_enrich(
    wrong_items: List[Dict[str, Any]],
    tr: bool,
) -> Dict[int, Dict[str, Any]]:
    if not wrong_items or not _ollama_enabled():
        return {}
    merged: Dict[int, Dict[str, Any]] = {}
    chunk_size = max(1, int(os.getenv("QUIZ_FEEDBACK_OLLAMA_CHUNK", "3") or "3"))
    for start in range(0, len(wrong_items), chunk_size):
        chunk = wrong_items[start : start + chunk_size]
        merged.update(_ollama_enrich_chunk(chunk, tr))
    out: Dict[int, Dict[str, Any]] = {}
    for k, v in merged.items():
        out[k] = {
            "error_type": str(v.get("error_type") or "concept_mixup"),
            "why_wrong": str(v.get("why_wrong") or "").strip(),
            "teaching_snippet": str(v.get("teaching_snippet") or "").strip(),
            "your_mistake": str(v.get("your_mistake") or "").strip(),
            "why_incorrect": str(v.get("why_incorrect") or "").strip(),
            "correct_thinking": str(v.get("correct_thinking") or "").strip(),
            "correct_answer_explained": str(v.get("correct_answer_explained") or "").strip(),
            "hint": str(v.get("hint") or "").strip(),
            "teach_back_prompt": str(v.get("teach_back_prompt") or "").strip(),
            "confused_concepts": v.get("confused_concepts") or [],
        }
    return out


# fn: build_quiz_feedback_bundle | tr: quiz sonrası tüm sorular için geri bildirim paketi üret / en: build full per-question feedback bundle after quiz
def build_quiz_feedback_bundle(
    questions: List[QuizQuestion],
    selected_answers: List[str],
    correctness: List[bool],
    locale: str = "en",
    *,
    ollama_enrich: bool = True,
) -> Tuple[List[QuizQuestionFeedback], Dict[str, int], List[str], List[str]]:
    tr = _is_tr(locale)
    per_q: List[QuizQuestionFeedback] = []
    wrong_for_llm: List[Dict[str, Any]] = []
    wrong_by_topic: Counter[str] = Counter()

    for i, (q, sel, ok) in enumerate(zip(questions, selected_answers, correctness)):
        topic = (q.topic or ("Genel" if tr else "General")).strip()
        cor = q.correct_answer or ""
        qtext = ((q.question_text or "").strip()[:_MAX_Q_TEXT] or None)
        sel_stripped = (sel or "").strip()
        if ok:
            per_q.append(
                QuizQuestionFeedback(
                    question_index=i,
                    topic=topic,
                    question_text=qtext,
                    is_correct=True,
                    is_unanswered=False,
                    selected_answer=sel_stripped,
                    correct_answer=cor,
                    question_type=q.question_type,
                )
            )
            continue

        if not sel_stripped:
            why_u = (
                "Bu soruyu boş bıraktın; doğru seçeneği PDF'deki ilgili bölümle eşleştirmeyi dene."
                if tr
                else "You left this item blank — match the right option to the relevant section in your notes."
            )
            hint_u = (
                "Önce kök metni oku, sonra şıkları tek tek eleyerek işaretle."
                if tr
                else "Re-read the stem, eliminate distractors, then pick the best match."
            )
            per_q.append(
                QuizQuestionFeedback(
                    question_index=i,
                    topic=topic,
                    question_text=qtext,
                    is_correct=False,
                    is_unanswered=True,
                    selected_answer="",
                    correct_answer=cor,
                    question_type=q.question_type,
                    error_type="unanswered",
                    confused_concepts=[],
                    why_wrong=_clip_feedback_text(why_u, _MAX_WHY_LEN),
                    teaching_snippet=None,
                    hint=_clip_feedback_text(hint_u, _MAX_HINT_LEN),
                    teach_back_prompt=None,
                )
            )
            continue

        wrong_by_topic[topic] += 1
        err = _infer_error_type(q, sel, cor, tr)
        ym0, wi0, ct0, cae0, hint0, concepts = _template_feedback(err, topic, sel, cor, tr, q)
        tback0 = _teach_back_prompt_line(err, topic, tr, q.question_type)
        per_q.append(
            QuizQuestionFeedback(
                question_index=i,
                topic=topic,
                question_text=qtext,
                is_correct=False,
                is_unanswered=False,
                selected_answer=sel,
                correct_answer=cor,
                question_type=q.question_type,
                error_type=err,
                confused_concepts=concepts,
                your_mistake=_clip_feedback_text(ym0, _MAX_COACH_SECTION),
                why_incorrect=_clip_feedback_text(wi0, _MAX_COACH_SECTION),
                correct_thinking=_clip_feedback_text(ct0, _MAX_COACH_SECTION),
                correct_answer_explained=_clip_feedback_text(cae0, _MAX_COACH_SECTION),
                why_wrong=_clip_feedback_text(wi0, _MAX_WHY_LEN),
                teaching_snippet=_clip_feedback_text(ct0, _MAX_TEACH_LEN),
                hint=_clip_feedback_text(hint0, _MAX_HINT_LEN),
                teach_back_prompt=_clip_feedback_text(tback0, 420),
            )
        )
        wrong_for_llm.append(
            {
                "index": i,
                "topic": topic,
                "question": (q.question_text or "")[:900],
                "options": q.options[:12],
                "selected": sel,
                "correct": cor,
                "initial_error_type": err,
            }
        )

    # tr: ollama varsa şablon metinleri zenginleştir / en: enrich template text via ollama when available
    llm_map = _ollama_batch_enrich(wrong_for_llm, tr) if ollama_enrich else {}
    merged: List[QuizQuestionFeedback] = []
    for fb in per_q:
        if fb.is_correct:
            merged.append(fb)
            continue
        if getattr(fb, "is_unanswered", False):
            merged.append(fb)
            continue
        q = questions[fb.question_index]
        extra = llm_map.get(fb.question_index)
        base = fb
        if extra:
            et = extra.get("error_type") or fb.error_type
            if et not in ("concept_mixup", "formula_mixup", "careless", "interpretation"):
                et = fb.error_type
            cc = extra.get("confused_concepts")
            concepts = list(fb.confused_concepts or [])
            if isinstance(cc, list) and cc:
                concepts = [str(x).strip() for x in cc if str(x).strip()][:4]
            why_llm = str(extra.get("why_wrong") or "").strip()
            teach_llm = str(extra.get("teaching_snippet") or "").strip()
            hint_llm = str(extra.get("hint") or "").strip()
            tb_llm = str(extra.get("teach_back_prompt") or "").strip()
            ym_llm = str(extra.get("your_mistake") or "").strip()
            wi_llm = str(extra.get("why_incorrect") or "").strip()
            ct_llm = str(extra.get("correct_thinking") or "").strip()
            cae_llm = str(extra.get("correct_answer_explained") or "").strip()
            base = fb.model_copy(
                update={
                    "error_type": et,
                    "your_mistake": ym_llm or getattr(fb, "your_mistake", None),
                    "why_incorrect": wi_llm or getattr(fb, "why_incorrect", None),
                    "correct_thinking": ct_llm or getattr(fb, "correct_thinking", None),
                    "correct_answer_explained": cae_llm or getattr(fb, "correct_answer_explained", None),
                    "why_wrong": why_llm or fb.why_wrong,
                    "teaching_snippet": teach_llm or fb.teaching_snippet,
                    "hint": hint_llm or fb.hint,
                    "teach_back_prompt": tb_llm or getattr(fb, "teach_back_prompt", None),
                    "confused_concepts": concepts,
                    "question_type": q.question_type or fb.question_type,
                }
            )
        merged.append(_finalize_wrong_feedback(base, q, tr))
    per_q = merged

    err_counts: Dict[str, int] = {}
    for fb in per_q:
        if fb.is_correct or not fb.error_type:
            continue
        err_counts[fb.error_type] = err_counts.get(fb.error_type, 0) + 1

    confused_ranked = [t for t, _ in wrong_by_topic.most_common()]

    follow: List[str] = []
    unanswered_n = sum(1 for fb in per_q if getattr(fb, "is_unanswered", False))
    if unanswered_n:
        follow.append(
            f"{unanswered_n} soru boş kaldı; göndermeden önce tüm maddeleri işaretle veya bilinçli olarak boş bırak."
            if tr
            else f"{unanswered_n} item(s) were left blank — mark every question before submit, or leave blank intentionally."
        )
    if confused_ranked:
        top = confused_ranked[0]
        if tr:
            follow.append(
                f"Öncelik: «{top}» konusunda kısa tekrar + mini quiz; ardından PDF'de ilgili bölümü yeniden oku."
            )
        else:
            follow.append(
                f"Priority: short recap on «{top}», then a mini-quiz; re-open the matching PDF section."
            )
    if err_counts.get("formula_mixup"):
        follow.append(
            "Formül sorularında: her terimi tanımla, birimleri kontrol et, sonra rakamları yerleştir."
            if tr
            else "For formula items: define each symbol, check units, then substitute values."
        )
    if err_counts.get("careless"):
        follow.append(
            "Dikkat hatalarında: soruyu sesli oku, «en az / değil» gibi anahtar kelimeleri işaretle."
            if tr
            else "For slips: read the stem aloud and underline negations like NOT / least / except."
        )

    return per_q, err_counts, confused_ranked, follow

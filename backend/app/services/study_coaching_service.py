"""
Build a personalized post-quiz coaching pack from structured quiz_result (topic_analysis, error_types).
Uses PDF text when document_id is available; optional Ollama JSON enrich for weak topics.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from app.schemas.quiz_schema import QuizSubmissionResponse
from app.schemas.suggestion_schema import (
    QuizCoachingPack,
    RecommendedMiniQuiz,
    StudyResourceTip,
    SuggestedNextQuiz,
    TopicScoreRow,
    TopicStudyContent,
)
from app.schemas.topic_schema import TopicPerformance
from app.services.document_store import get_document
from app.services.ollama_service import ollama_available, ollama_chat


def _clamp_pct(n: float) -> int:
    return max(0, min(100, int(round(n))))


def _norm_topic(s: str) -> str:
    return (s or "").strip() or "Genel"


def _mini_quiz_count_from_score(score_pct: int) -> int:
    """Mini quiz length: 3–4 questions."""
    if score_pct < 45:
        return 4
    return 3


def _global_band(score_pct: int) -> str:
    if score_pct < 40:
        return "critical"
    if score_pct < 70:
        return "building"
    return "strong"


def _error_hint(code: str, tr: bool) -> str:
    c = (code or "").strip().lower()
    if c == "concept_mixup":
        return (
            "Yorum kısmını karıştırmış olabilirsin."
            if tr
            else "You may be mixing up similar ideas in the wording."
        )
    if c == "formula_mixup":
        return (
            "Formülü cevap gibi düşünmüş olabilirsin."
            if tr
            else "You may be treating a formula like the final answer."
        )
    if c == "careless":
        return (
            "Dikkat hatası var; soruyu bir kez daha yavaş oku."
            if tr
            else "Looks like a slip—reread the question once, slowly."
        )
    if c == "interpretation":
        return (
            "Soru ne istiyor, onu netleştirmek faydalı olur."
            if tr
            else "Clarifying what the question asks would help."
        )
    return "" if tr else ""


def _top_error_type_and_hint(err: Dict[str, int], tr: bool) -> Tuple[str, str]:
    if not err:
        return "", ("Belirgin bir hata tipi yok." if tr else "No strong error-type pattern.")
    code, _n = max(err.items(), key=lambda kv: kv[1])
    hint = _error_hint(code, tr)
    if not hint:
        hint = code.replace("_", " ") if code else ""
    return code, hint


def _document_study_text(document_id: Optional[str]) -> str:
    if not document_id:
        return ""
    doc = get_document(document_id.strip())
    return (doc.study_text if doc else "") or ""


def _topic_success_pct(tp: TopicPerformance) -> int:
    r = float(tp.success_rate or 0)
    if r <= 1.0001:
        return _clamp_pct(r * 100.0)
    return _clamp_pct(r)


def _pdf_snippet_for_topic(full_text: str, topic: str, max_chars: int = 900) -> str:
    if not full_text or not topic:
        return ""
    t = topic.strip()
    if len(t) < 2:
        return ""
    low = full_text.lower()
    needle = t.lower()
    idx = low.find(needle)
    if idx < 0:
        words = [w for w in re.split(r"\s+", t) if len(w) > 2][:4]
        for w in words:
            idx = low.find(w.lower())
            if idx >= 0:
                break
    if idx < 0:
        return full_text[:max_chars].strip()
    start = max(0, idx - 200)
    end = min(len(full_text), idx + max_chars)
    return full_text[start:end].strip()


def _rule_topic_content(
    topic: str,
    success_pct: int,
    pdf_excerpt: str,
    tr: bool,
) -> TopicStudyContent:
    if tr:
        what = f"'{topic}' konusunda bu quizde %{success_pct} doğruluk."
        why = (
            "PDF’deki tanım ve örnekleri yan yana oku; benzer kavramları karıştırmamaya dikkat et."
            if success_pct < 50
            else "Küçük bir tekrar ve 2–3 örnek soru genelde yeterli olur."
        )
        summ = (
            f"Kısa özet: '{topic}' ile ilgili temel fikirleri ve ilişkileri hatırla."
            if pdf_excerpt
            else f"Özet: '{topic}' için notlarından ana başlıkları çıkar."
        )
        simple = (
            "Basit anlatım: Önce neyi ölçüyoruz, sonra hangi değişkenler var—sırayla git."
            if success_pct < 60
            else "Basit anlatım: Soruda verilenleri tek tek işaretle, sonra isteneni yaz."
        )
        ex_q = f"Örnek: '{topic}' ile ilgili kısa bir tanım veya yorum sorusu üret ve cevabını kontrol et."
        ex_a = "İpucu: Önce anahtar kelimeleri, sonra gerekçeyi yaz."
        micro = f"Mini görev: 3 cümlede '{topic}' konusunu kendi cümlelerinle özetle."
        outline = f"Mini quiz taslağı: '{topic}' için 3 doğru/yanlış + 1 kısa açıklama sorusu."
    else:
        what = f"On this quiz, '{topic}' was at {success_pct}% accuracy."
        why = (
            "Reread definitions and examples side by side; watch for near-miss wording."
            if success_pct < 50
            else "A short review plus a couple practice checks usually helps."
        )
        summ = (
            f"Quick recap: recall the core idea and how '{topic}' connects to the rest."
            if pdf_excerpt
            else f"Recap: pull the main headings for '{topic}' from your notes."
        )
        simple = (
            "Plain explanation: first what is being measured, then which variables matter—step by step."
            if success_pct < 60
            else "Plain explanation: list what’s given, then state what’s asked."
        )
        ex_q = f"Example: write one short definition or interpretation question about '{topic}' and self-check."
        ex_a = "Hint: keywords first, then your reasoning."
        micro = f"Micro task: summarize '{topic}' in three sentences in your own words."
        outline = f"Mini-quiz sketch: 3 true/false + 1 short explain for '{topic}'."
    return TopicStudyContent(
        topic=topic,
        success_rate_pct=success_pct,
        weak_under_half=success_pct < 50,
        what_went_wrong=what,
        why_hint=why,
        pdf_excerpt=pdf_excerpt[:1200] if pdf_excerpt else "",
        topic_summary=summ,
        simple_explanation=simple,
        example_question=ex_q,
        example_answer_hint=ex_a,
        micro_task=micro,
        mini_quiz_outline=outline,
    )


def _ollama_enrich_topic(
    topic: str,
    success_pct: int,
    pdf_excerpt: str,
    tr: bool,
) -> Optional[Dict[str, str]]:
    if not ollama_available():
        return None
    lang = "Turkish" if tr else "English"
    excerpt = (pdf_excerpt or "")[:3500]
    prompt = f"""You are a study coach. Return ONLY valid JSON with these string keys:
what_went_wrong, why_hint, topic_summary, simple_explanation, example_question, example_answer_hint, micro_task, mini_quiz_outline.
Language: {lang}. Topic: "{topic}". Quiz success about this topic: {success_pct}%.
Use the excerpt as ground truth; if excerpt is empty, give generic but honest coaching.
Excerpt:
\"\"\"{excerpt}\"\"\"
JSON:"""
    raw = ollama_chat(
        prompt,
        system="Reply with a single JSON object only. No markdown fences.",
        options={"temperature": 0.2},
    )
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out: Dict[str, str] = {}
    for k in (
        "what_went_wrong",
        "why_hint",
        "topic_summary",
        "simple_explanation",
        "example_question",
        "example_answer_hint",
        "micro_task",
        "mini_quiz_outline",
    ):
        v = data.get(k)
        out[k] = str(v).strip() if isinstance(v, str) else ""
    return out


def _natural_feedback_lines(
    weakest: str,
    err_code: str,
    err_hint: str,
    score_pct: int,
    tr: bool,
) -> List[str]:
    lines: List[str] = []
    if weakest and score_pct < 85:
        lines.append(
            f"En çok '{weakest}' konusunda zorlandın."
            if tr
            else f"You struggled most with '{weakest}'."
        )
    if err_hint:
        lines.append(err_hint)
    if score_pct < 50:
        lines.append(
            "Bu konuyu tekrar etmen faydalı olur."
            if tr
            else "A focused retry on the weak spots will help."
        )
    elif score_pct < 75:
        lines.append(
            "İyi gidiyorsun; zayıf kalan 1–2 konuya odaklan."
            if tr
            else "You’re close—double down on one or two weak topics."
        )
    else:
        lines.append(
            "Genel olarak sağlam; ince ayar için zayıf konuya bak."
            if tr
            else "Solid overall—tighten the one topic that dipped."
        )
    return [x for x in lines if x][:5]


def _personalized_tips(
    weakest: str,
    weak_topics: List[str],
    mini_n: int,
    tr: bool,
) -> List[str]:
    primary = weakest or (weak_topics[0] if weak_topics else "")
    others = [t for t in weak_topics if t != primary][:2]
    if tr:
        tips = [
            f"Önce '{primary}' konusunu tekrar et." if primary else "Önce en zayıf konunu tekrar et.",
            f"Sonra bu konudan {mini_n} soruluk mini quiz çöz.",
            "Ardından PDF’de ilgili kısmı tekrar oku.",
        ]
        if others:
            tips.append(f"'{others[0]}' için de kısa bir tekrar ekle.")
    else:
        tips = [
            f"Review '{primary}' first." if primary else "Review your weakest topic first.",
            f"Then take a {mini_n}-question mini quiz on it.",
            "Reread the matching section in the PDF.",
        ]
        if others:
            tips.append(f"Add a quick pass on '{others[0]}' too.")
    return tips[:6]


def _study_plan_steps(
    weakest: str,
    mini_n: int,
    tr: bool,
) -> List[str]:
    if tr:
        w = weakest or "en zayıf konun"
        return [
            f"{w} için kısa özeti oku.",
            f"Bu konudan {mini_n} soruluk mini quiz çöz.",
            "PDF’de ilgili paragrafı tekrar incele.",
            "Hazır olunca ana quizi tekrar dene.",
        ]
    w = weakest or "your weakest topic"
    return [
        f"Read the short recap for {w}.",
        f"Take the {mini_n}-question mini quiz.",
        "Skim the related PDF section again.",
        "When ready, retry the main quiz.",
    ]


def build_coaching_pack(
    quiz_result: QuizSubmissionResponse,
    document_id: Optional[str] = None,
    output_locale: str = "tr",
) -> QuizCoachingPack:
    tr = (output_locale or "tr").lower().startswith("tr")
    score_pct = int(round(float(quiz_result.score_percentage or 0)))
    band = _global_band(score_pct)
    mini_n = _mini_quiz_count_from_score(score_pct)

    ta = quiz_result.topic_analysis
    topic_list = list(ta.topics or [])
    topics_sorted = sorted(topic_list, key=lambda tp: (_topic_success_pct(tp), (tp.topic or "").lower()))
    rows: List[TopicScoreRow] = []
    weak_topics: List[str] = []
    weakest_topic = ""
    min_sr = 101

    for tp in topics_sorted:
        sr = _topic_success_pct(tp)
        wrong = int(tp.wrong_count or 0)
        total = int(tp.total_attempts or 0)
        rows.append(
            TopicScoreRow(
                topic=_norm_topic(tp.topic),
                success_pct=sr,
                wrong_count=wrong,
                total_attempts=total,
            )
        )
        if total > 0 and sr < min_sr:
            min_sr = sr
            weakest_topic = _norm_topic(tp.topic)
        if total > 0 and float(tp.success_rate or 0) < 0.5:
            weak_topics.append(_norm_topic(tp.topic))

    if not weak_topics and weakest_topic:
        weak_topics = [weakest_topic]

    err = quiz_result.error_type_summary or {}
    top_code, top_hint = _top_error_type_and_hint(
        {k: int(v or 0) for k, v in err.items() if int(v or 0) > 0},
        tr,
    )

    natural_lines = _natural_feedback_lines(weakest_topic, top_code, top_hint, score_pct, tr)
    tip_lines = _personalized_tips(weakest_topic, weak_topics, mini_n, tr)
    plan_steps = _study_plan_steps(weakest_topic, mini_n, tr)

    focus_topics: List[str] = []
    if weakest_topic:
        focus_topics.append(weakest_topic)
    for t in weak_topics:
        if t not in focus_topics:
            focus_topics.append(t)
    ranked = list(quiz_result.confused_topics_ranked or [])
    for t in ranked:
        nt = _norm_topic(t)
        if nt and nt not in focus_topics:
            focus_topics.append(nt)
    if not focus_topics and topic_list:
        focus_topics = [_norm_topic(topic_list[0].topic)]

    pdf_text = _document_study_text(document_id)

    topic_contents: List[TopicStudyContent] = []
    for t in weak_topics[:4]:
        excerpt = _pdf_snippet_for_topic(pdf_text, t)
        base = _rule_topic_content(t, next((r.success_pct for r in rows if r.topic == t), 0), excerpt, tr)
        enriched = _ollama_enrich_topic(t, base.success_rate_pct, excerpt, tr)
        if enriched:
            for field in (
                "what_went_wrong",
                "why_hint",
                "topic_summary",
                "simple_explanation",
                "example_question",
                "example_answer_hint",
                "micro_task",
                "mini_quiz_outline",
            ):
                val = enriched.get(field, "")
                if val:
                    setattr(base, field, val)
        topic_contents.append(base)

    if tr:
        overall_what = (
            f"Bu denemede özellikle '{weakest_topic}' zorlandı."
            if weakest_topic
            else "Bu denemede birkaç konu daha zayıf kaldı."
        )
        overall_why = top_hint or "Hata tipine göre okuma veya yorum adımını netleştir."
        overall_next = (
            f"Önce '{weakest_topic}' için özet + mini quiz, sonra PDF tekrar."
            if weakest_topic
            else "Zayıf konular için özet, mini quiz ve PDF tekrarı."
        )
        cap = f"{mini_n} soruluk mini quiz — odak: {', '.join(focus_topics[:3])}"
    else:
        overall_what = (
            f"You struggled most with '{weakest_topic}'."
            if weakest_topic
            else "A few topics need more attention."
        )
        overall_why = top_hint or "Tighten reading or interpretation based on the error pattern."
        overall_next = (
            f"Recap + mini quiz on '{weakest_topic}', then reread the PDF."
            if weakest_topic
            else "Recap weak topics, mini quiz, then reread the PDF."
        )
        cap = f"{mini_n}-question mini quiz — focus: {', '.join(focus_topics[:3])}"

    diff = "beginner" if band == "critical" else "normal"
    suggested = SuggestedNextQuiz(
        num_questions=mini_n,
        difficulty=diff,
        focus_topics=focus_topics[:12],
        rationale=overall_next,
        short_recap="",
    )

    pm = 25 if score_pct < 55 else 20
    resource_tips: List[StudyResourceTip] = []
    for tc in topic_contents[:5]:
        resource_tips.append(
            StudyResourceTip(
                topic=tc.topic,
                short_summary=(tc.topic_summary or tc.what_went_wrong or "")[:240],
                explain_action=(tc.simple_explanation or tc.why_hint or overall_why)[:400],
                pdf_section_hint=(tc.pdf_excerpt or "")[:500],
                mini_quiz_hint=(tc.mini_quiz_outline or f"{mini_n}-question mini quiz: {tc.topic}."),
                pomodoro_minutes=pm,
            )
        )

    return QuizCoachingPack(
        global_band=band,
        overall_what_wrong=overall_what,
        overall_why=overall_why,
        overall_next_steps=overall_next,
        suggested_quiz=suggested,
        topic_contents=topic_contents,
        topic_score_rows=rows,
        weakest_topic=weakest_topic,
        top_error_type=top_code,
        top_error_hint=top_hint,
        natural_feedback_lines=natural_lines,
        personalized_tip_lines=tip_lines,
        study_plan_steps=plan_steps,
        recommended_mini_quiz=RecommendedMiniQuiz(
            num_questions=mini_n,
            focus_topics=focus_topics[:12],
            caption=cap,
        ),
        resource_tips=resource_tips,
    )

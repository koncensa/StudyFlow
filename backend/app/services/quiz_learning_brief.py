# svc: quiz_learning_brief | tr: quiz sonrası kişisel öğrenme özeti (zayıf/güçlü konular, sonraki adımlar) / en: personalized post-quiz learning snapshot (weak/strong topics, next steps)

from __future__ import annotations

from typing import List, Optional

from app.schemas.quiz_schema import QuizLearningBrief, QuizSubmissionResponse, TopicSnapshotLine
from app.schemas.topic_schema import TopicPerformance


# fn: _tr | tr: yerel ayar türkçe mi / en: is locale turkish
def _tr(locale: str) -> bool:
    return (locale or "en").strip().lower().startswith("tr")


# fn: _band_label | tr: konu durum etiketini yerelleştir / en: localize topic band status label
def _band_label(status: str, tr: bool) -> str:
    s = (status or "").strip().lower()
    if tr:
        m = {
            "very_weak": "Çok zayıf",
            "weak": "Zayıf",
            "developing": "Orta",
            "good": "İyi",
            "strong": "Çok iyi",
        }
        return m.get(s, s or "—")
    m = {
        "very_weak": "Very weak",
        "weak": "Weak",
        "developing": "Mid",
        "good": "Good",
        "strong": "Strong",
    }
    return m.get(s, s or "—")


# fn: _line_coaching | tr: konu satırı için kısa koçluk cümlesi / en: short coaching line for one topic row
def _line_coaching(tp: TopicPerformance, tr: bool) -> str:
    t = (tp.topic or "—").strip()
    st = (tp.status or "").lower()
    sr = float(tp.success_rate or 0.0)
    if st in ("very_weak", "weak") or sr < 0.5:
        return (
            f"«{t}» için tekrar et; PDF'de bu başlığı oku; bu konudan mini quiz çöz."
            if tr
            else f"Review «{t}», reread that part of your PDF, then take a mini quiz."
        )
    if st == "developing" or sr < 0.75:
        return (
            f"«{t}» için biraz daha pratik yap."
            if tr
            else f"A bit more practice on «{t}» will help."
        )
    return f"«{t}»: İyi gidiyorsun." if tr else f"«{t}»: You're doing well."


# fn: _headline | tr: öğrenme özeti başlık cümlesi / en: learning brief headline sentence
def _headline(result: QuizSubmissionResponse, tr: bool) -> str:
    ta = result.topic_analysis
    ranked = list(result.confused_topics_ranked or [])
    if ranked:
        top = ranked[0]
        return (
            f"Bu quizde en çok «{top}» konusunda takıldın."
            if tr
            else f"You struggled most with «{top}» on this quiz."
        )
    weak_names = list(getattr(ta, "very_weak_topics", None) or []) + list(ta.weak_topics or [])
    if weak_names:
        tail = " …" if len(weak_names) > 3 else ""
        if tr:
            return f"Zayıf kalan konular: {', '.join(weak_names[:3])}{tail}."
        return f"Topics to tighten: {', '.join(weak_names[:3])}{tail}."
    if (result.score_percentage or 0) >= 70:
        return "Genel performansın iyi; güçlü konuları aralıklı tekrar et." if tr else "Solid run — keep spacing reviews on your strong topics."
    return "Bu quizden çıkan konu dağılımına göre aşağıdaki adımları kullan." if tr else "Use the breakdown below for your next steps."


# fn: _what_to_do | tr: sonraki adım önerileri listesi / en: list of recommended next steps
def _what_to_do(result: QuizSubmissionResponse, tr: bool) -> List[str]:
    out: List[str] = []
    ranked = [str(x).strip() for x in (result.confused_topics_ranked or []) if str(x).strip()]
    err = result.error_type_summary or {}
    top_err = max(err.items(), key=lambda kv: kv[1])[0] if err else ""

    if ranked:
        t0 = ranked[0]
        out.append(
            f"Önce «{t0}» bölümünü PDF'de yeniden oku."
            if tr
            else f"First, reread «{t0}» in your PDF."
        )
    mini = int(result.recommended_mini_quiz_count or 3)
    focus = ranked[:4] or [
        tp.topic for tp in (result.topic_analysis.topics or []) if (tp.success_rate or 0) < 0.75
    ][:4]
    if focus:
        out.append(
            f"{mini} soruluk mini quiz çöz — odak: {', '.join(focus)}."
            if tr
            else f"Take a {mini}-question mini quiz focused on: {', '.join(focus)}."
        )
    if top_err == "formula_mixup":
        out.append(
            "Formül sorularında önce sembolleri tanımla, sonra rakam yerleştir."
            if tr
            else "On formula items, name each symbol before plugging numbers."
        )
    elif top_err == "careless":
        out.append(
            "Dikkat için soruyu sesli oku; «değil / en az» gibi kelimeleri işaretle."
            if tr
            else "Read stems aloud and underline words like NOT / least / except."
        )
    elif top_err == "interpretation":
        out.append(
            "Her şıkta sorunun tam olarak ne sorduğunu tek cümlede yaz."
            if tr
            else "For each option, write one line stating what the question actually asks."
        )
    if not out:
        out.append(
            "PDF asistanında zayıf konuya kısa soru sor; ardından mini quiz."
            if tr
            else "Ask the assistant one short question on a weak area, then mini-quiz."
        )
    return out[:6]


# fn: _resource_lines | tr: pdf tabanlı çalışma kaynağı satırları / en: pdf-grounded study resource lines
def _resource_lines(result: QuizSubmissionResponse, tr: bool) -> List[str]:
    ranked = [str(x).strip() for x in (result.confused_topics_ranked or []) if str(x).strip()]
    weak_tp = [
        tp.topic
        for tp in (result.topic_analysis.topics or [])
        if (tp.success_rate or 0) < 0.6 and (tp.topic or "").strip()
    ]
    topics = ranked[:5] or weak_tp[:5]
    lines: List[str] = []
    for t in topics:
        lines.append(
            f"«{t}»: PDF'de bu konunun geçtiği yeri bul, 2 cümle özetle, 1 örnek soru yaz."
            if tr
            else f"«{t}»: Find it in your PDF, summarize in two sentences, write one practice question."
        )
    return lines[:6]


# fn: _mini_quiz_line | tr: önerilen mini quiz satırı / en: suggested mini quiz line
def _mini_quiz_line(result: QuizSubmissionResponse, tr: bool) -> str:
    n = int(result.recommended_mini_quiz_count or 3)
    ranked = [str(x).strip() for x in (result.confused_topics_ranked or []) if str(x).strip()]
    if not ranked:
        ranked = [
            tp.topic
            for tp in sorted(
                result.topic_analysis.topics or [],
                key=lambda x: (float(x.success_rate or 0), (x.topic or "").lower()),
            )
            if (tp.success_rate or 0) < 0.85 and (tp.topic or "").strip()
        ][:4]
    focus = ", ".join(ranked[:5]) if ranked else "—"
    return (
        f"Sana önerilen: {n} soru — konular: {focus}."
        if tr
        else f"Suggested for you: {n} questions — topics: {focus}."
    )


# fn: build_learning_brief | tr: quiz sonucundan öğrenme özeti nesnesi oluştur / en: build learning brief object from quiz result
def build_learning_brief(result: QuizSubmissionResponse, locale: str = "en") -> Optional[QuizLearningBrief]:
    tr = _tr(locale)
    topics = list(result.topic_analysis.topics or [])
    if not topics:
        return None

    def sort_key(tp: TopicPerformance) -> tuple:
        return (float(tp.success_rate or 0), (tp.topic or "").lower())

    sorted_topics = sorted(topics, key=sort_key)
    weak: List[TopicSnapshotLine] = []
    mid: List[TopicSnapshotLine] = []
    strong: List[TopicSnapshotLine] = []

    # tr: konuları zayıf / orta / güçlü gruplara ayır / en: split topics into weak / mid / strong groups
    for tp in sorted_topics:
        st = (tp.status or "").lower()
        line = TopicSnapshotLine(
            topic=(tp.topic or "General").strip() or "General",
            correct_count=int(tp.correct_count or 0),
            total_attempts=int(tp.total_attempts or 0),
            success_rate=round(float(tp.success_rate or 0.0), 4),
            band_label=_band_label(tp.status or "", tr),
            coaching_line=_line_coaching(tp, tr),
        )
        if st in ("very_weak", "weak"):
            weak.append(line)
        elif st == "developing":
            mid.append(line)
        elif st in ("good", "strong"):
            strong.append(line)
        else:
            mid.append(line)

    return QuizLearningBrief(
        headline=_headline(result, tr),
        weak_topics=weak,
        developing_topics=mid,
        strong_topics=strong,
        what_to_do_next=_what_to_do(result, tr),
        mini_quiz_hint=_mini_quiz_line(result, tr),
        resource_hints=_resource_lines(result, tr),
    )

# svc: quiz_scores | tr: gönderilen cevapları puanla, konu analizi ve koçluk meta verisi üret / en: score submitted answers, topic breakdown, and coaching metadata

import logging
import os
import re
from collections import Counter
from typing import List, Optional, Tuple

from app.schemas.quiz_schema import QuizQuestion, QuizQuestionFeedback, QuizSubmissionResponse
from app.services.quiz_feedback_service import build_quiz_feedback_bundle
from app.services.quiz_learning_brief import build_learning_brief
from app.services.quiz_result_pipeline import (
    LIFETIME_STRONG_FROM,
    LIFETIME_WEAK_BELOW,
    build_topic_analysis,
    topic_band_status,
)
from app.schemas.topic_schema import TopicAnalysisResponse

# tr: stats_service uyumluluğu — zayıf <%60, güçlü ≥%75 / en: stats_service compat — weak <60%, strong ≥75%
DEVELOPING_MIN = LIFETIME_WEAK_BELOW
STRONG_MIN = LIFETIME_STRONG_FROM

_log = logging.getLogger(__name__)


# fn: _norm_mc_answer | tr: mcq cevabını karşılaştırma için normalize et / en: normalize mcq answer for comparison
def _norm_mc_answer(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


# fn: _selection_matches_correct | tr: seçilen cevap doğru mu (büyük/küçük harf duyarsız) / en: check if selected answer matches correct
def _selection_matches_correct(q: QuizQuestion, raw: str) -> bool:
    sel = _norm_mc_answer(raw)
    if not sel:
        return False
    ca = _norm_mc_answer(q.correct_answer or "")
    if sel == ca:
        return True
    for o in q.options or []:
        if _norm_mc_answer(str(o)) == sel:
            return _norm_mc_answer(str(o)) == ca
    return False


# fn: _coaching_wrong_extras | tr: yanlış sayısı, zayıf konular, önerilen mini quiz konusu / en: wrong count, weak topics, suggested mini quiz topic
def _coaching_wrong_extras(
    qfb: List[QuizQuestionFeedback],
    topic_analysis: TopicAnalysisResponse,
) -> Tuple[int, List[QuizQuestionFeedback], List[str], Optional[str]]:
    wrong_items = [f for f in qfb if (not f.is_correct) and (not getattr(f, "is_unanswered", False))]
    listed_wrong_count = len(wrong_items)
    weak_topics: List[str] = []
    for t in topic_analysis.very_weak_topics or []:
        s = (t or "").strip()
        if s and s not in weak_topics:
            weak_topics.append(s)
    for t in topic_analysis.weak_topics or []:
        s = (t or "").strip()
        if s and s not in weak_topics:
            weak_topics.append(s)
    suggested: Optional[str] = None
    if wrong_items:
        c = Counter((f.topic or "").strip() for f in wrong_items if (f.topic or "").strip())
        if c:
            suggested = c.most_common(1)[0][0]
    if not suggested and weak_topics:
        suggested = weak_topics[0]
    return listed_wrong_count, wrong_items, weak_topics, suggested


# fn: _performance_comment | tr: puana göre kısa performans yorumu (tr/en) / en: short performance comment by score (tr/en)
def _performance_comment(score_pct: float, locale: str, topic_analysis: TopicAnalysisResponse) -> str:
    tr = (locale or "en").strip().lower().startswith("tr")
    topics = list(topic_analysis.topics or [])
    weakest = ""
    if topics:
        worst = min(topics, key=lambda tp: (float(tp.success_rate or 1.0), (tp.topic or "").lower()))
        weakest = (worst.topic or "").strip()
    label = f"«{weakest}»" if weakest else ""

    if score_pct < 40:
        if tr:
            variants_tr = (
                (
                    f"Bu deneme zorlayıcı geçti; {label} ve benzer zayıf başlıkları önce toparlaman en hızlı yol olur."
                    if weakest
                    else "Bu deneme zorlayıcı geçti; zayıf başlıkları önce toparlaman en hızlı yol olur."
                ),
                (
                    f"Skor düşük ama düzeltilebilir — {label} için kısa özet + birkaç soru genelde işe yarar."
                    if weakest
                    else "Skor düşük ama düzeltilebilir — zayıf konular için kısa özet + birkaç soru genelde işe yarar."
                ),
                (
                    f"{label} önceliği zayıf konulara ver; her yanlışta neyin karıştığını tek cümlede yaz."
                    if weakest
                    else "Önceliği zayıf konulara ver; her yanlışta neyin karıştığını tek cümlede yaz."
                ),
            )
        else:
            variants_en = (
                (
                    f"Tough round — rebuild {label} and the other weak labels first; that is the fastest lift."
                    if weakest
                    else "Tough round — rebuild the weak topics first; that is the fastest lift."
                ),
                (
                    f"Score is low but fixable: a tight recap on {label or 'weak topics'} plus a few questions usually helps."
                ),
                "Prioritize weak areas — after each miss, write one line on what you mixed up.",
            )
        i = int(score_pct) % 3
        return variants_tr[i] if tr else variants_en[i]
    if score_pct < 55:
        return (
            f"Temel fikirler var; {label + ' üzerinde ' if weakest else ''}biraz daha netleşmen yeter."
            if tr
            else f"You are close — a little more clarity on {label or 'the shaky topics'} will show up fast."
        )
    if score_pct < 70:
        return (
            f"İyi bir taban; {label + ' gibi ' if weakest else ''}ara bölgelerde pratikle 75%+ rahat görünür."
            if tr
            else f"Solid base — short drills on {label or 'mid topics'} usually push you past 75%."
        )
    if score_pct < 85:
        return (
            "Güçlü performans; zayıf kalan tek iki başlığı mikro quiz ile kapat."
            if tr
            else "Strong run — micro-quizzes on the one or two dips will lock this in."
        )
    return (
        "Çok iyi — güçlü konuları haftada bir karışık soruyla taze tutman yeter."
        if tr
        else "Excellent — light mixed review weekly keeps your strong topics automatic."
    )


# fn: _recommended_mini_quiz_count | tr: puana göre önerilen mini quiz soru sayısı (3–6) / en: recommended mini quiz count by score (3–6)
def _recommended_mini_quiz_count(score_pct: float, user_id: Optional[int] = None) -> int:
    _ = user_id
    if score_pct < 38:
        base = 5
    elif score_pct < 55:
        base = 4
    elif score_pct < 70:
        base = 3
    else:
        base = 3
    return max(3, min(6, base))


# fn: summarize_topics | tr: konu analizini quiz_result_pipeline'a devret / en: delegate topic analysis to quiz_result_pipeline
def summarize_topics(
    questions: List[QuizQuestion],
    correctness: List[bool],
    selected_answers: Optional[List[str]] = None,
):
    return build_topic_analysis(questions, correctness, selected_answers)


# fn: check_answers | tr: her soru için doğru/yanlış listesi döndür / en: return per-question correct/incorrect list
def check_answers(questions: List[QuizQuestion], selected_answers: List[str]) -> List[bool]:
    if len(questions) != len(selected_answers):
        raise ValueError("questions and selected_answers must be the same length.")

    out: List[bool] = []
    for q, selected in zip(questions, selected_answers):
        if not (selected or "").strip():
            out.append(False)
        else:
            out.append(_selection_matches_correct(q, selected))
    return out


# fn: score_submission | tr: quiz gönderimini tam sonuç paketine dönüştür (ana fonksiyon) / en: turn quiz submission into full result payload (main entry)
def score_submission(
    questions: List[QuizQuestion],
    selected_answers: List[str],
    *,
    locale: str = "en",
    user_id: Optional[int] = None,
    duration_seconds: Optional[int] = None,
    quiz_source: Optional[str] = None,
) -> QuizSubmissionResponse:
    if len(questions) != len(selected_answers):
        raise ValueError("questions and selected_answers must be the same length.")

    correctness: List[bool] = []
    for q, raw in zip(questions, selected_answers):
        if not (raw or "").strip():
            correctness.append(False)
        else:
            correctness.append(_selection_matches_correct(q, raw))

    total = len(correctness)
    total_correct = sum(1 for x in correctness if x)
    total_unanswered = sum(
        1 for q, raw in zip(questions, selected_answers) if not (raw or "").strip()
    )
    total_wrong = total - total_correct - total_unanswered
    score_percentage = (total_correct / total * 100.0) if total > 0 else 0.0

    topic_analysis = summarize_topics(questions, correctness, list(selected_answers))

    # tr: gönderimde ollama varsayılan kapalı (hızlı yanıt) / en: ollama off by default on submit (fast response)
    ollama_on_submit = os.getenv("QUIZ_FEEDBACK_OLLAMA_ON_SUBMIT", "0").strip().lower() in ("1", "true", "yes", "on")
    qfb, err_summary, confused_ranked, follow = build_quiz_feedback_bundle(
        questions,
        selected_answers,
        correctness,
        locale=locale or "en",
        ollama_enrich=ollama_on_submit,
    )

    listed_wrong, wrong_items, weak_topics, sugg_topic = _coaching_wrong_extras(qfb, topic_analysis)
    if listed_wrong != total_wrong:
        _log.warning(
            "quiz feedback wrong-count mismatch: total_wrong=%s listed_wrong_count=%s (attempt_len=%s)",
            total_wrong,
            listed_wrong,
            len(qfb),
        )

    loc = locale or "en"
    perf = _performance_comment(score_percentage, loc, topic_analysis)
    mini_n = _recommended_mini_quiz_count(score_percentage, user_id=user_id)
    follow2 = list(follow)
    if perf and (not follow2 or follow2[0] != perf):
        follow2.insert(0, perf)

    teach_tasks = [
        str(fb.teach_back_prompt).strip()
        for fb in qfb
        if (not fb.is_correct)
        and (not getattr(fb, "is_unanswered", False))
        and getattr(fb, "teach_back_prompt", None)
        and str(fb.teach_back_prompt).strip()
    ]
    teach_tasks = teach_tasks[:8]

    src = (quiz_source or "").strip()[:24] or None
    dur = int(duration_seconds) if duration_seconds is not None and int(duration_seconds) >= 0 else None
    if dur is not None and dur > 86400:
        dur = 86400

    base = QuizSubmissionResponse(
        total_correct=total_correct,
        total_wrong=total_wrong,
        total_unanswered=total_unanswered,
        total_question_count=total,
        total_duration_seconds=dur,
        quiz_source=src,
        score_percentage=round(score_percentage, 2),
        performance_comment=perf,
        recommended_mini_quiz_count=mini_n,
        topic_analysis=topic_analysis,
        question_feedback=qfb,
        error_type_summary=err_summary,
        confused_topics_ranked=confused_ranked,
        follow_up_actions=follow2,
        listed_wrong_count=listed_wrong,
        wrong_items=wrong_items,
        weak_topics=weak_topics,
        suggested_mini_quiz_topic=sugg_topic,
        teach_back_tasks=teach_tasks,
    )
    brief = build_learning_brief(base, loc)
    return base.model_copy(update={"learning_brief": brief})

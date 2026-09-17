# svc: quiz_results_service | tr: sonuçlar sayfası için db'den quiz verilerini yükle ve birleştir / en: load and assemble quiz data from db for results page

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document_model import Document
from app.models.quiz import (
    QuizAttempt,
    QuizAttemptQuestionResult,
    QuizAttemptTopicResult,
)
from app.schemas.topic_schema import TopicAnalysisResponse, TopicPerformance
from app.schemas.quiz_results_schema import QuizResultsPageResponse, TopicProgressItem
from app.schemas.quiz_schema import QuizQuestionFeedback, QuizSubmissionResponse, UserQuizMemory
from app.services.quiz_scores import (
    _coaching_wrong_extras,
    _performance_comment,
    _recommended_mini_quiz_count,
    topic_band_status,
)
from app.services.stats_service import load_study_stats
from app.services.summary_service import normalize_output_locale
from app.services.quiz_learning_brief import build_learning_brief
from app.services.suggestion_service import build_suggestions

_log = logging.getLogger(__name__)


# fn: _topic_analysis_from_rows | tr: db konu satırlarından TopicAnalysisResponse oluştur / en: build TopicAnalysisResponse from db topic rows
def _topic_analysis_from_rows(rows: List[QuizAttemptTopicResult]) -> TopicAnalysisResponse:
    if not rows:
        return TopicAnalysisResponse(
            topics=[],
            topics_under_half=[],
            very_weak_topics=[],
            weak_topics=[],
            developing_topics=[],
            good_topics=[],
            strong_topics=[],
            moderate_topics=[],
        )
    perf_models: List[TopicPerformance] = []
    for row in rows:
        topic = row.topic or "General"
        correct = int(row.correct_count or 0)
        wrong = int(row.wrong_count or 0)
        total = int(row.total_attempts or 0)
        if total <= 0:
            continue
        success_rate = float(row.success_rate) if row.success_rate is not None else (correct / total)
        status = topic_band_status(success_rate)
        wu = success_rate < 0.5
        perf_models.append(
            TopicPerformance(
                topic=topic,
                correct_count=correct,
                wrong_count=wrong,
                total_attempts=total,
                success_rate=success_rate,
                status=status,
                is_weak_under_half=wu,
            )
        )
    perf_models.sort(key=lambda tp: (tp.success_rate, (tp.topic or "").lower()))
    very_weak: List[str] = []
    weak: List[str] = []
    developing: List[str] = []
    good: List[str] = []
    strong: List[str] = []
    under_half: List[str] = []
    for tp in perf_models:
        if tp.is_weak_under_half:
            under_half.append(tp.topic)
        st = tp.status
        if st == "very_weak":
            very_weak.append(tp.topic)
        elif st == "weak":
            weak.append(tp.topic)
        elif st == "developing":
            developing.append(tp.topic)
        elif st == "good":
            good.append(tp.topic)
        elif st == "strong":
            strong.append(tp.topic)
        else:
            developing.append(tp.topic)

    return TopicAnalysisResponse(
        topics=perf_models,
        topics_under_half=under_half,
        very_weak_topics=very_weak,
        weak_topics=weak,
        developing_topics=developing,
        good_topics=good,
        strong_topics=strong,
        moderate_topics=list(developing),
    )


# fn: load_attempt_topic_analysis | tr: tek denemenin konu analizini db'den yükle / en: load topic analysis for one attempt from db
def load_attempt_topic_analysis(db: Session, attempt_id: int) -> TopicAnalysisResponse:
    rows = (
        db.query(QuizAttemptTopicResult)
        .filter(QuizAttemptTopicResult.quiz_attempt_id == attempt_id)
        .all()
    )
    return _topic_analysis_from_rows(rows)


# fn: topic_analysis_from_question_feedback | tr: eski kayıtlar için geri bildirimden konu analizi türet / en: derive topic analysis from feedback when db rows missing
def topic_analysis_from_question_feedback(qfb: List[QuizQuestionFeedback]) -> TopicAnalysisResponse:
    by_topic: Dict[str, Dict[str, int]] = {}
    for fb in qfb:
        t = (fb.topic or "General").strip() or "General"
        if t not in by_topic:
            by_topic[t] = {"correct": 0, "wrong": 0, "total": 0}
        by_topic[t]["total"] += 1
        if fb.is_correct:
            by_topic[t]["correct"] += 1
        elif getattr(fb, "is_unanswered", False):
            pass
        else:
            by_topic[t]["wrong"] += 1
    topics_sorted = sorted(
        by_topic.keys(),
        key=lambda t: (
            (by_topic[t]["correct"] / by_topic[t]["total"]) if by_topic[t]["total"] else 0.0,
            t.lower(),
        ),
    )
    very_weak_topics: List[str] = []
    weak_topics: List[str] = []
    developing_topics: List[str] = []
    good_topics: List[str] = []
    strong_topics: List[str] = []
    topics_under_half: List[str] = []
    perf_models: List[TopicPerformance] = []
    for topic in topics_sorted:
        b = by_topic[topic]
        correct = b["correct"]
        wrong = b["wrong"]
        total = b["total"]
        success_rate = (correct / total) if total else 0.0
        status = topic_band_status(success_rate)
        wu = success_rate < 0.5
        if wu:
            topics_under_half.append(topic)
        perf_models.append(
            TopicPerformance(
                topic=topic,
                correct_count=correct,
                wrong_count=wrong,
                total_attempts=total,
                success_rate=success_rate,
                status=status,
                is_weak_under_half=wu,
            )
        )
        if status == "very_weak":
            very_weak_topics.append(topic)
        elif status == "weak":
            weak_topics.append(topic)
        elif status == "developing":
            developing_topics.append(topic)
        elif status == "good":
            good_topics.append(topic)
        elif status == "strong":
            strong_topics.append(topic)
        else:
            developing_topics.append(topic)
    perf_models.sort(key=lambda tp: (tp.success_rate, (tp.topic or "").lower()))
    return TopicAnalysisResponse(
        topics=perf_models,
        topics_under_half=topics_under_half,
        very_weak_topics=very_weak_topics,
        weak_topics=weak_topics,
        developing_topics=developing_topics,
        good_topics=good_topics,
        strong_topics=strong_topics,
        moderate_topics=list(developing_topics),
    )


# fn: aggregate_topic_analysis | tr: kullanıcının tüm quizlerinden ömür boyu konu analizi / en: lifetime per-topic analysis across all user quizzes
def aggregate_topic_analysis(db: Session, user_id: int) -> TopicAnalysisResponse:
    q = (
        db.query(
            QuizAttemptTopicResult.topic.label("topic"),
            func.sum(QuizAttemptTopicResult.correct_count).label("c_correct"),
            func.sum(QuizAttemptTopicResult.wrong_count).label("c_wrong"),
            func.sum(QuizAttemptTopicResult.total_attempts).label("c_total"),
        )
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptTopicResult.quiz_attempt_id)
        .filter(QuizAttempt.user_id == user_id)
        .group_by(QuizAttemptTopicResult.topic)
    )
    rows = q.all()
    if not rows:
        return TopicAnalysisResponse(
            topics=[],
            topics_under_half=[],
            very_weak_topics=[],
            weak_topics=[],
            developing_topics=[],
            good_topics=[],
            strong_topics=[],
            moderate_topics=[],
        )

    by_topic = {r.topic: r for r in rows if r.topic}
    topics_sorted = sorted(
        by_topic.keys(),
        key=lambda t: (
            (int(by_topic[t].c_correct or 0) / int(by_topic[t].c_total or 1))
            if int(by_topic[t].c_total or 0) > 0
            else 0.0,
            str(t).lower(),
        ),
    )
    topic_performance: List[Dict[str, Any]] = []
    very_weak_topics: List[str] = []
    weak_topics: List[str] = []
    developing_topics: List[str] = []
    good_topics: List[str] = []
    strong_topics: List[str] = []
    topics_under_half_life: List[str] = []

    for topic in topics_sorted:
        row = by_topic[topic]
        correct = int(row.c_correct or 0)
        wrong = int(row.c_wrong or 0)
        total = int(row.c_total or 0)
        if total <= 0:
            continue
        success_rate = correct / total
        status = topic_band_status(success_rate)
        wu = success_rate < 0.5
        if wu:
            topics_under_half_life.append(topic)
        topic_performance.append(
            {
                "topic": topic,
                "correct_count": correct,
                "wrong_count": wrong,
                "total_attempts": total,
                "success_rate": success_rate,
                "status": status,
                "is_weak_under_half": wu,
            }
        )
        if status == "very_weak":
            very_weak_topics.append(topic)
        elif status == "weak":
            weak_topics.append(topic)
        elif status == "developing":
            developing_topics.append(topic)
        elif status == "good":
            good_topics.append(topic)
        elif status == "strong":
            strong_topics.append(topic)
        else:
            developing_topics.append(topic)

    topic_performance.sort(key=lambda d: (float(d["success_rate"]), str(d["topic"]).lower()))
    perf_models = [TopicPerformance(**tp) for tp in topic_performance]
    return TopicAnalysisResponse(
        topics=perf_models,
        topics_under_half=topics_under_half_life,
        very_weak_topics=very_weak_topics,
        weak_topics=weak_topics,
        developing_topics=developing_topics,
        good_topics=good_topics,
        strong_topics=strong_topics,
        moderate_topics=list(developing_topics),
    )


# fn: _feedback_from_row | tr: db soru satırını QuizQuestionFeedback nesnesine çevir / en: convert db question row to QuizQuestionFeedback
def _feedback_from_row(row: QuizAttemptQuestionResult) -> QuizQuestionFeedback:
    extra: Dict[str, Any] = {}
    if row.feedback_json:
        try:
            parsed = json.loads(row.feedback_json)
            if isinstance(parsed, dict):
                extra = parsed
        except (json.JSONDecodeError, TypeError):
            extra = {}
    err_t = extra.get("error_type") if isinstance(extra.get("error_type"), str) else None
    qt_raw = extra.get("question_type")
    qt = str(qt_raw).strip()[:32] if isinstance(qt_raw, str) and qt_raw.strip() else None
    qtext = (row.question_text or "").strip() or None
    is_unanswered = bool(extra.get("is_unanswered")) or (err_t == "unanswered")
    return QuizQuestionFeedback(
        question_index=int(row.question_index),
        topic=row.topic or "General",
        question_text=qtext,
        is_correct=bool(row.is_correct),
        is_unanswered=is_unanswered,
        selected_answer=row.selected_answer or "",
        correct_answer=row.correct_answer or "",
        question_type=qt,
        error_type=err_t,
        confused_concepts=list(extra.get("confused_concepts") or []),
        why_wrong=extra.get("why_wrong"),
        teaching_snippet=extra.get("teaching_snippet"),
        your_mistake=extra.get("your_mistake") if isinstance(extra.get("your_mistake"), str) else None,
        why_incorrect=extra.get("why_incorrect") if isinstance(extra.get("why_incorrect"), str) else None,
        correct_thinking=extra.get("correct_thinking") if isinstance(extra.get("correct_thinking"), str) else None,
        correct_answer_explained=(
            extra.get("correct_answer_explained") if isinstance(extra.get("correct_answer_explained"), str) else None
        ),
        hint=extra.get("hint"),
        teach_back_prompt=extra.get("teach_back_prompt") if isinstance(extra.get("teach_back_prompt"), str) else None,
    )


# fn: load_last_attempt_question_feedback | tr: denemenin soru bazlı geri bildirimlerini yükle / en: load per-question feedback for one attempt
def load_last_attempt_question_feedback(
    db: Session, attempt_id: int
) -> Tuple[List[QuizQuestionFeedback], Dict[str, int]]:
    rows = (
        db.query(QuizAttemptQuestionResult)
        .filter(QuizAttemptQuestionResult.quiz_attempt_id == attempt_id)
        .order_by(QuizAttemptQuestionResult.question_index.asc())
        .all()
    )
    out: List[QuizQuestionFeedback] = []
    err_counts: Counter[str] = Counter()
    for r in rows:
        fb = _feedback_from_row(r)
        out.append(fb)
        if not fb.is_correct and fb.error_type:
            err_counts[fb.error_type] += 1
    return out, dict(err_counts)


# fn: _attempt_topic_rates | tr: denemedeki konu başarı oranlarını sözlük olarak döndür / en: return per-topic success rates for one attempt
def _attempt_topic_rates(db: Session, attempt_id: int) -> Dict[str, float]:
    rows = (
        db.query(QuizAttemptTopicResult)
        .filter(QuizAttemptTopicResult.quiz_attempt_id == attempt_id)
        .all()
    )
    out: Dict[str, float] = {}
    for row in rows:
        t = row.topic or "General"
        total = int(row.total_attempts or 0)
        if total <= 0:
            continue
        correct = int(row.correct_count or 0)
        out[t] = correct / total
    return out


# fn: compute_topic_progress | tr: son iki deneme arasında konu ilerlemesini hesapla / en: compute topic progress between last two attempts
def compute_topic_progress(db: Session, user_id: int, latest_attempt_id: int) -> List[TopicProgressItem]:
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == user_id)
        .order_by(QuizAttempt.created_at.desc())
        .limit(2)
        .all()
    )
    if len(attempts) < 2 or attempts[0].id != latest_attempt_id:
        return []
    cur_id = attempts[0].id
    prev_id = attempts[1].id
    prev_rates = _attempt_topic_rates(db, prev_id)
    cur_rates = _attempt_topic_rates(db, cur_id)
    items: List[TopicProgressItem] = []
    for topic, cur in sorted(cur_rates.items()):
        prev = prev_rates.get(topic)
        if prev is None:
            items.append(
                TopicProgressItem(
                    topic=topic,
                    previous_success_rate=None,
                    current_success_rate=cur,
                    delta_points=None,
                    trend="new",
                )
            )
            continue
        delta_pct = round((cur - prev) * 100.0, 1)
        if abs(delta_pct) < 0.05:
            trend = "stable"
        elif cur > prev:
            trend = "improved"
        elif cur < prev:
            trend = "declined"
        else:
            trend = "stable"
        items.append(
            TopicProgressItem(
                topic=topic,
                previous_success_rate=prev,
                current_success_rate=cur,
                delta_points=delta_pct,
                trend=trend,
            )
        )
    return items


# fn: build_user_results_page | tr: sonuçlar sayfası için tüm veriyi birleştir ve döndür / en: assemble and return full results page payload
def build_user_results_page(
    db: Session,
    user_id: int,
    *,
    locale: str = "en",
) -> QuizResultsPageResponse:
    loc = normalize_output_locale(locale)
    last = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == user_id)
        .order_by(QuizAttempt.created_at.desc())
        .first()
    )
    lifetime_agg = aggregate_topic_analysis(db, user_id)

    summary = load_study_stats(db, user_id)
    mem = UserQuizMemory(
        total_quiz_count=int(summary.get("total_quiz_count") or 0),
        average_quiz_score=summary.get("average_quiz_score"),
        weak_topics_lifetime=list(summary.get("weak_topics") or []),
        strong_topics_lifetime=list(summary.get("strong_topics") or []),
    )

    if last is None:
        return QuizResultsPageResponse(has_data=False, user_quiz_memory=mem)

    qfb, err_summary = load_last_attempt_question_feedback(db, last.id)

    attempt_topics = load_attempt_topic_analysis(db, last.id)
    if not attempt_topics.topics and qfb:
        attempt_topics = topic_analysis_from_question_feedback(qfb)

    progress = compute_topic_progress(db, user_id, last.id)

    confused_ranked = [t for t, _ in Counter(fb.topic for fb in qfb if not fb.is_correct).most_common()]

    listed_wrong, wrong_items, weak_topics, sugg_topic = _coaching_wrong_extras(qfb, attempt_topics)
    tw = int(last.total_wrong or 0)
    if listed_wrong != tw:
        _log.warning(
            "persisted attempt wrong-count mismatch: attempt.total_wrong=%s listed_wrong_count=%s attempt_id=%s",
            tw,
            listed_wrong,
            last.id,
        )

    sc = float(last.score_percentage or 0.0)
    total_qn = int(last.total_questions or 0)
    un_ct = int(getattr(last, "unanswered_count", 0) or 0)
    if un_ct <= 0 and qfb:
        un_ct = sum(1 for f in qfb if getattr(f, "is_unanswered", False))
    if total_qn <= 0 and qfb:
        total_qn = len(qfb)
    dur = getattr(last, "duration_seconds", None)
    dur_i = int(dur) if dur is not None and int(dur) >= 0 else None
    # tr: db kaydından QuizSubmissionResponse benzeri sentetik yanıt / en: synthetic QuizSubmissionResponse from db row
    synthetic = QuizSubmissionResponse(
        total_correct=last.total_correct,
        total_wrong=last.total_wrong,
        total_unanswered=un_ct,
        total_question_count=total_qn or (last.total_correct + last.total_wrong + un_ct),
        total_duration_seconds=dur_i,
        quiz_source=None,
        score_percentage=sc,
        performance_comment=_performance_comment(sc, loc, attempt_topics),
        recommended_mini_quiz_count=_recommended_mini_quiz_count(sc),
        topic_analysis=attempt_topics,
        question_feedback=qfb,
        error_type_summary=err_summary,
        confused_topics_ranked=confused_ranked,
        follow_up_actions=[],
        user_quiz_memory=mem,
        listed_wrong_count=listed_wrong,
        wrong_items=wrong_items,
        weak_topics=weak_topics,
        suggested_mini_quiz_topic=sugg_topic,
    )
    synthetic = synthetic.model_copy(update={"learning_brief": build_learning_brief(synthetic, loc)})

    doc_id = None
    if last.document_db_id:
        drow = db.query(Document).filter(Document.id == last.document_db_id).first()
        if drow and drow.session_key:
            doc_id = str(drow.session_key).strip() or None

    suggestions = build_suggestions(
        synthetic, user_id=user_id, output_locale=loc, document_id=doc_id
    )

    return QuizResultsPageResponse(
        has_data=True,
        attempt_id=last.id,
        document_id=doc_id,
        total_correct=last.total_correct,
        total_wrong=last.total_wrong,
        total_unanswered=un_ct,
        total_question_count=int(synthetic.total_question_count or 0),
        total_duration_seconds=synthetic.total_duration_seconds,
        score_percentage=sc,
        performance_comment=synthetic.performance_comment,
        recommended_mini_quiz_count=int(synthetic.recommended_mini_quiz_count or 3),
        topic_analysis=attempt_topics,
        lifetime_topic_analysis=lifetime_agg,
        topic_progress=progress,
        question_feedback=qfb,
        error_type_summary=err_summary,
        confused_topics_ranked=confused_ranked,
        user_quiz_memory=mem,
        learning_brief=synthetic.learning_brief,
        suggestions=suggestions,
        listed_wrong_count=listed_wrong,
        wrong_items=wrong_items,
        weak_topics=weak_topics,
        suggested_mini_quiz_topic=sugg_topic,
    )

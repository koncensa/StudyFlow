# svc: quiz_attempt | tr: quiz cevaplarını puanla, db'ye kaydet, xp tetikle / en: score quiz answers, persist to db, trigger xp

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.quiz import (
    QuizAttempt,
    QuizAttemptQuestionResult,
    QuizAttemptTopicResult,
)
from app.schemas.quiz_schema import QuizQuestion, QuizSubmissionResponse, UserQuizMemory
from app.services.document_db_service import resolve_document_pk
from app.services.quiz_scores import score_submission
from app.services.stats_service import load_study_stats


# fn: _persist_question_feedback | tr: soru bazlı geri bildirimi db'ye yaz / en: save per-question feedback to db
def _persist_question_feedback(db: Session, attempt_id: int, result: QuizSubmissionResponse) -> None:
    rows: List[QuizAttemptQuestionResult] = []
    for fb in result.question_feedback or []:
        payload = {}
        if fb.confused_concepts:
            payload["confused_concepts"] = list(fb.confused_concepts)
        if fb.why_wrong:
            payload["why_wrong"] = fb.why_wrong
        if getattr(fb, "your_mistake", None):
            payload["your_mistake"] = fb.your_mistake
        if getattr(fb, "why_incorrect", None):
            payload["why_incorrect"] = fb.why_incorrect
        if getattr(fb, "correct_thinking", None):
            payload["correct_thinking"] = fb.correct_thinking
        if getattr(fb, "correct_answer_explained", None):
            payload["correct_answer_explained"] = fb.correct_answer_explained
        if fb.teaching_snippet:
            payload["teaching_snippet"] = fb.teaching_snippet
        if fb.hint:
            payload["hint"] = fb.hint
        if fb.error_type:
            payload["error_type"] = fb.error_type[:31]
        if getattr(fb, "question_type", None):
            payload["question_type"] = str(fb.question_type).strip()[:40]
        if getattr(fb, "is_unanswered", False):
            payload["is_unanswered"] = True
        if getattr(fb, "teach_back_prompt", None):
            payload["teach_back_prompt"] = str(fb.teach_back_prompt).strip()[:500]
        fj = json.dumps(payload, ensure_ascii=False) if payload else None
        topic = (fb.topic or "General").strip() or "General"
        topic = topic[:255]
        qtxt = (fb.question_text or "").strip() if getattr(fb, "question_text", None) else None
        rows.append(
            QuizAttemptQuestionResult(
                quiz_attempt_id=attempt_id,
                question_index=int(fb.question_index),
                topic=topic,
                question_text=qtxt,
                is_correct=bool(fb.is_correct),
                selected_answer=fb.selected_answer or "",
                correct_answer=fb.correct_answer or "",
                feedback_json=fj,
            )
        )
    if rows:
        db.add_all(rows)
        db.commit()


# fn: record_quiz_attempt_from_result | tr: hesaplanmış sonucu quiz_attempt tablolarına kaydet / en: persist scored result to quiz_attempt tables
def record_quiz_attempt_from_result(
    db: Session,
    user_id: int,
    result: QuizSubmissionResponse,
    *,
    document_id: Optional[str] = None,
) -> int:
    doc = (document_id or "").strip() or None
    if doc and len(doc) > 128:
        doc = doc[:128]
    doc_pk = resolve_document_pk(db, user_id, doc) if doc else None
    total_q = int(getattr(result, "total_question_count", 0) or 0)
    if total_q <= 0:
        total_q = int(result.total_correct + result.total_wrong + int(getattr(result, "total_unanswered", 0) or 0))
    un_ct = int(getattr(result, "total_unanswered", 0) or 0)
    dur = getattr(result, "total_duration_seconds", None)
    dur_i = int(dur) if dur is not None and int(dur) >= 0 else None
    if dur_i is not None and dur_i > 86400:
        dur_i = 86400
    attempt = QuizAttempt(
        user_id=user_id,
        document_db_id=doc_pk,
        total_questions=total_q,
        total_correct=result.total_correct,
        total_wrong=result.total_wrong,
        score_percentage=float(result.score_percentage),
        unanswered_count=un_ct,
        duration_seconds=dur_i,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    # tr: konu bazlı sonuçlar / en: per-topic results
    topic_rows: List[QuizAttemptTopicResult] = []
    for topic_perf in result.topic_analysis.topics:
        tlabel = (topic_perf.topic or "General").strip() or "General"
        if len(tlabel) > 255:
            tlabel = tlabel[:252] + "…"
        topic_rows.append(
            QuizAttemptTopicResult(
                quiz_attempt_id=attempt.id,
                topic=tlabel,
                correct_count=topic_perf.correct_count,
                wrong_count=topic_perf.wrong_count,
                total_attempts=topic_perf.total_attempts,
                success_rate=topic_perf.success_rate,
                status=topic_perf.status,
            )
        )

    db.add_all(topic_rows)
    db.commit()

    _persist_question_feedback(db, attempt.id, result)

    # tr: quiz sonrası xp + rozet / en: xp and badges after quiz
    try:
        from app.services import profile_service

        profile_service.apply_after_quiz_saved(db, user_id)
    except Exception:
        pass

    return attempt.id


# fn: record_quiz_attempt | tr: puanla ve kaydet, sadece attempt id dön / en: score, persist, return attempt id only
def record_quiz_attempt(
    db: Session,
    user_id: int,
    questions: List[QuizQuestion],
    selected_answers: List[str],
    *,
    locale: str = "en",
    document_id: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    quiz_source: Optional[str] = None,
) -> int:
    result = score_submission(
        questions,
        selected_answers,
        locale=locale or "en",
        user_id=user_id,
        duration_seconds=duration_seconds,
        quiz_source=quiz_source,
    )
    return record_quiz_attempt_from_result(db, user_id, result, document_id=document_id)


# fn: record_quiz_attempt_and_build_response | tr: puanla, kaydet, tam api cevabı dön / en: score, persist, return full api response
def record_quiz_attempt_and_build_response(
    db: Session,
    user_id: int,
    questions: List[QuizQuestion],
    selected_answers: List[str],
    *,
    locale: str = "en",
    document_id: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    quiz_source: Optional[str] = None,
) -> QuizSubmissionResponse:
    result = score_submission(
        questions,
        selected_answers,
        locale=locale or "en",
        user_id=user_id,
        duration_seconds=duration_seconds,
        quiz_source=quiz_source,
    )
    attempt_id = record_quiz_attempt_from_result(db, user_id, result, document_id=document_id)

    data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
    data["attempt_id"] = attempt_id

    # tr: kullanicinin tum quiz gecmiş ozeti / en: lifetime quiz stats summary
    try:
        summary = load_study_stats(db, user_id)
        data["user_quiz_memory"] = UserQuizMemory(
            total_quiz_count=int(summary.get("total_quiz_count") or 0),
            average_quiz_score=summary.get("average_quiz_score"),
            weak_topics_lifetime=list(summary.get("weak_topics") or []),
            strong_topics_lifetime=list(summary.get("strong_topics") or []),
        )
    except Exception:
        data["user_quiz_memory"] = None

    return QuizSubmissionResponse(**data)

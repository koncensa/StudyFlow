# svc: quiz_history_service | tr: kullanıcının kayıtlı quiz geçmişini listele (sonuçlar / analiz) / en: list persisted quiz attempts for one user (results / analysis)

from __future__ import annotations

import json
from collections import Counter
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.quiz import QuizAttempt, QuizAttemptQuestionResult, QuizAttemptTopicResult
from app.schemas.quiz_results_schema import QuizHistoryListItem, QuizHistoryListResponse
from app.services.quiz_scores import topic_band_status


# fn: _error_summary_for_attempt | tr: denemedeki hata türlerini say (concept_mixup vb.) / en: count error types for one attempt
def _error_summary_for_attempt(db: Session, attempt_id: int) -> Dict[str, int]:
    rows = (
        db.query(QuizAttemptQuestionResult)
        .filter(QuizAttemptQuestionResult.quiz_attempt_id == int(attempt_id))
        .all()
    )
    counts: Counter[str] = Counter()
    for row in rows:
        if row.is_correct:
            continue
        err_t = None
        if row.feedback_json:
            try:
                parsed = json.loads(row.feedback_json)
                if isinstance(parsed, dict) and isinstance(parsed.get("error_type"), str):
                    err_t = parsed["error_type"].strip()
            except (json.JSONDecodeError, TypeError):
                pass
        if err_t:
            counts[err_t] += 1
    return {k: int(v) for k, v in counts.items() if v > 0}


# fn: _topic_rows_for_attempt | tr: deneme için konu bazlı doğru/yanlış satırları hazırla / en: build per-topic correct/wrong rows for one attempt
def _topic_rows_for_attempt(db: Session, attempt_id: int) -> List[dict]:
    rows = (
        db.query(QuizAttemptTopicResult)
        .filter(QuizAttemptTopicResult.quiz_attempt_id == int(attempt_id))
        .order_by(QuizAttemptTopicResult.topic.asc())
        .all()
    )
    out: List[dict] = []
    for row in rows:
        topic = (row.topic or "General").strip() or "General"
        correct = int(row.correct_count or 0)
        wrong = int(row.wrong_count or 0)
        total = int(row.total_attempts or 0)
        if total <= 0:
            continue
        rate = float(row.success_rate) if row.success_rate is not None else (correct / total)
        out.append(
            {
                "topic": topic,
                "correct": correct,
                "total": total,
                "pct": max(0, min(100, round(rate * 100))),
                "status": (row.status or topic_band_status(rate)).strip() or topic_band_status(rate),
                "wrong": wrong,
            }
        )
    return out


# fn: list_user_quiz_history | tr: kullanıcının quiz geçmiş listesini api için döndür / en: return quiz history list for api
def list_user_quiz_history(
    db: Session,
    user_id: int,
    *,
    limit: int = 60,
) -> QuizHistoryListResponse:
    cap = max(1, min(int(limit or 60), 100))
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == int(user_id))
        .order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
        .limit(cap)
        .all()
    )
    items: List[QuizHistoryListItem] = []
    for att in attempts:
        total_q = int(att.total_questions or 0)
        if total_q <= 0:
            total_q = int(att.total_correct or 0) + int(att.total_wrong or 0) + int(att.unanswered_count or 0)
        total_q = max(1, total_q)
        un_ct = int(att.unanswered_count or 0)
        # tr: 6 ve altı soru = mini quiz, üzeri = standart / en: <=6 questions = mini quiz, else standard
        quiz_kind = "mini_adaptive" if total_q <= 6 else "standard"
        # tr: pdf oturumundan mı yapıldı / en: was quiz tied to a pdf session
        quiz_source = "pdf_session" if att.document_db_id else "other"
        topic_rows = _topic_rows_for_attempt(db, int(att.id))
        # tr: başarı %60 altı konular = zayıf konu önerisi / en: topics below 60% = weak focus suggestions
        weak_topics = [r["topic"] for r in topic_rows if int(r.get("pct") or 0) < 60][:8]
        items.append(
            QuizHistoryListItem(
                id=f"db-{int(att.id)}",
                attempt_id=int(att.id),
                solved_at_iso=att.created_at.isoformat() if att.created_at else "",
                quiz_kind=quiz_kind,
                quiz_source=quiz_source,
                score=float(att.score_percentage or 0.0),
                correct=int(att.total_correct or 0),
                wrong=int(att.total_wrong or 0),
                unanswered=un_ct,
                total_questions=total_q,
                duration_seconds=int(att.duration_seconds)
                if att.duration_seconds is not None and int(att.duration_seconds) >= 0
                else None,
                recommended_mini_count=3,
                suggested_mini_quiz_topic=weak_topics[0] if weak_topics else None,
                focus_topics=weak_topics,
                recommendation_lines=[],
                topic_rows=topic_rows,
                error_summary=_error_summary_for_attempt(db, int(att.id)),
            )
        )
    return QuizHistoryListResponse(items=items)

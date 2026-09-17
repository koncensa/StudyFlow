# route: stats_routes | tr: ilerleme/stats paneli toplu çalışma istatistikleri http endpoint'i — main.py'de /stats prefix ile bağlanır / en: progress stats panel aggregated study metrics endpoint mounted at /stats prefix in main.py

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.deps.auth import require_user_id
from app.schemas.stats_schema import StudyStatsResponse
from app.services.stats_service import load_study_stats

router = APIRouter()


# fn: study_stats_summary | tr: GET /stats/summary — pomodoro + quiz ömür boyu istatistikleri / en: GET lifetime pomodoro and quiz study statistics
@router.get("/summary", response_model=StudyStatsResponse)
def study_stats_summary(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> StudyStatsResponse:
    uid = require_user_id(authorization)
    if db is None:
        # tr: db yokken sıfır metrik + boş kanıt şablonu / en: zero metrics and empty proof template when db unavailable
        return StudyStatsResponse(
            user_id=uid,
            total_study_time_seconds=0,
            pomodoro_sessions_count=0,
            total_quiz_count=0,
            average_quiz_score=None,
            weak_topics=[],
            strong_topics=[],
            quiz_sum_correct=0,
            quiz_sum_wrong=0,
            quiz_sum_unanswered=0,
            quiz_sum_question_slots=0,
            quiz_lifetime_accuracy_percent=None,
            pomodoro_by_topic_seconds={},
            pomodoro_recent_sessions=[],
            calculation_proof={
                "formula_total_focus_time": "sum(duration_seconds) for completed pomodoro sessions",
                "formula_avg_attempt_score": "sum(score_percentage per attempt) / attempts_with_score",
                "formula_lifetime_accuracy": "100 * sum(total_correct) / sum(total_questions)",
                "pomodoro_completed_sessions_count": 0,
                "pomodoro_total_seconds_from_sessions": 0,
                "pomodoro_total_seconds_from_topic_rollup": 0,
                "pomodoro_sample_limit": 25,
                "pomodoro_sample_truncated": False,
                "pomodoro_sample_sessions": [],
                "quiz_attempts_count": 0,
                "quiz_correct_numerator": 0,
                "quiz_accuracy_denominator": 0,
                "quiz_lifetime_accuracy_percent": None,
                "quiz_avg_score_sum_percent": 0.0,
                "quiz_avg_score_attempt_count": 0,
                "quiz_avg_score_percent": None,
                "quiz_attempt_sample_limit": 25,
                "quiz_attempt_sample_truncated": False,
                "quiz_attempt_sample": [],
            },
        )

    data = load_study_stats(db=db, user_id=uid)
    return StudyStatsResponse(**data)

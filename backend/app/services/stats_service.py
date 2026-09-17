"""Roll up pomodoro time and quiz topic history for one user."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.quiz import QuizAttempt, QuizAttemptQuestionResult, QuizAttemptTopicResult
from app.models.pomodoro_model import PomodoroSession
from app.services.quiz_scores import DEVELOPING_MIN, STRONG_MIN  # lifetime weak <60%, strong >=75%


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return None


def load_study_stats(db: Session, user_id: int) -> dict:
    proof_pomodoro_sample_limit = 25
    proof_quiz_attempt_sample_limit = 25
    completed = (
        db.query(PomodoroSession)
        .filter(
            PomodoroSession.user_id == user_id,
            PomodoroSession.status == "completed",
            PomodoroSession.duration_seconds.isnot(None),
        )
        .order_by(asc(PomodoroSession.id))
        .all()
    )
    session_scoped_id_by_id: Dict[int, str] = {
        int(row.id): f"P-{idx:04d}" for idx, row in enumerate(completed, start=1)
    }

    pomodoro_sessions_count = len(completed)
    total_study_time_seconds = sum(s.duration_seconds or 0 for s in completed)

    by_topic: Dict[str, int] = {}
    for s in completed:
        key = (s.topic or "").strip() or "General"
        by_topic[key] = by_topic.get(key, 0) + int(s.duration_seconds or 0)

    pomodoro_total_seconds_from_topic_rollup = int(sum(int(v or 0) for v in by_topic.values()))
    pomodoro_sample_sessions = [
        {
            "scoped_id": session_scoped_id_by_id.get(int(s.id), f"P-{idx + 1:04d}"),
            "session_id": int(s.id),
            "topic": (s.topic or "").strip() or None,
            "duration_seconds": int(s.duration_seconds or 0),
            "end_time": _dt_iso(s.end_time),
        }
        for idx, s in enumerate(completed[:proof_pomodoro_sample_limit])
    ]

    recent_pomodoro = (
        db.query(PomodoroSession)
        .filter(
            PomodoroSession.user_id == user_id,
            PomodoroSession.status == "completed",
            PomodoroSession.duration_seconds.isnot(None),
        )
        .order_by(desc(PomodoroSession.end_time), desc(PomodoroSession.id))
        .limit(40)
        .all()
    )
    pomodoro_recent_sessions = [
        {
            "id": int(row.id),
            "topic": (row.topic or "").strip() or None,
            "duration_seconds": int(row.duration_seconds or 0),
            "start_time": _dt_iso(row.start_time) or "",
            "end_time": _dt_iso(row.end_time),
        }
        for row in recent_pomodoro
    ]

    total_quiz_count = db.query(func.count(QuizAttempt.id)).filter(QuizAttempt.user_id == user_id).scalar() or 0

    average_quiz_score = db.query(func.avg(QuizAttempt.score_percentage)).filter(QuizAttempt.user_id == user_id).scalar()

    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == user_id)
        .order_by(asc(QuizAttempt.id))
        .all()
    )
    attempt_scoped_id_by_id: Dict[int, str] = {
        int(row.id): f"Q-{idx:04d}" for idx, row in enumerate(attempts, start=1)
    }
    quiz_sum_correct = 0
    quiz_sum_wrong = 0
    quiz_sum_unanswered = 0
    quiz_sum_question_slots = 0
    quiz_avg_score_sum_percent = 0.0
    quiz_avg_score_attempt_count = 0
    quiz_attempt_sample = []
    for a in attempts:
        tq = int(a.total_questions or 0)
        tc = int(a.total_correct or 0)
        tw = int(a.total_wrong or 0)
        score_pct = getattr(a, "score_percentage", None)
        quiz_sum_question_slots += tq
        quiz_sum_correct += tc
        quiz_sum_wrong += tw
        raw_u = getattr(a, "unanswered_count", None)
        if raw_u is None:
            u = max(0, tq - tc - tw)
        else:
            u = max(0, int(raw_u))
        quiz_sum_unanswered += u
        if score_pct is not None:
            quiz_avg_score_sum_percent += float(score_pct or 0.0)
            quiz_avg_score_attempt_count += 1
        if len(quiz_attempt_sample) < proof_quiz_attempt_sample_limit:
            quiz_attempt_sample.append(
                {
                    "scoped_id": attempt_scoped_id_by_id.get(int(a.id), f"Q-{len(quiz_attempt_sample) + 1:04d}"),
                    "attempt_id": int(a.id),
                    "total_questions": tq,
                    "total_correct": tc,
                    "total_wrong": tw,
                    "total_unanswered": u,
                    "score_percentage": float(score_pct) if score_pct is not None else None,
                }
            )

    quiz_lifetime_accuracy_percent: Optional[float] = None
    if quiz_sum_question_slots > 0:
        quiz_lifetime_accuracy_percent = round(100.0 * float(quiz_sum_correct) / float(quiz_sum_question_slots), 2)

    topic_totals: dict = {}
    topic_rows = (
        db.query(QuizAttemptTopicResult)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptTopicResult.quiz_attempt_id)
        .filter(QuizAttempt.user_id == user_id)
        .all()
    )

    for row in topic_rows:
        if row.topic not in topic_totals:
            topic_totals[row.topic] = {"correct": 0, "total": 0}
        topic_totals[row.topic]["correct"] += row.correct_count
        topic_totals[row.topic]["total"] += row.total_attempts

    topic_success = []
    for topic, totals in topic_totals.items():
        total_attempts = totals["total"]
        success_rate = (totals["correct"] / total_attempts) if total_attempts else 0.0
        topic_success.append((topic, success_rate))

    weak_topics = [t for t, r in sorted(topic_success, key=lambda x: x[1]) if r < DEVELOPING_MIN]
    strong_topics = [t for t, r in sorted(topic_success, key=lambda x: x[1], reverse=True) if r >= STRONG_MIN]

    quiz_avg_score_percent: Optional[float] = None
    if quiz_avg_score_attempt_count > 0:
        quiz_avg_score_percent = round(quiz_avg_score_sum_percent / float(quiz_avg_score_attempt_count), 6)

    calculation_proof = {
        "formula_total_focus_time": "sum(duration_seconds) for completed pomodoro sessions",
        "formula_avg_attempt_score": "sum(score_percentage per attempt) / attempts_with_score",
        "formula_lifetime_accuracy": "100 * sum(total_correct) / sum(total_questions)",
        "pomodoro_completed_sessions_count": int(pomodoro_sessions_count),
        "pomodoro_total_seconds_from_sessions": int(total_study_time_seconds),
        "pomodoro_total_seconds_from_topic_rollup": int(pomodoro_total_seconds_from_topic_rollup),
        "pomodoro_sample_limit": int(proof_pomodoro_sample_limit),
        "pomodoro_sample_truncated": bool(pomodoro_sessions_count > proof_pomodoro_sample_limit),
        "pomodoro_sample_sessions": pomodoro_sample_sessions,
        "quiz_attempts_count": int(total_quiz_count),
        "quiz_correct_numerator": int(quiz_sum_correct),
        "quiz_accuracy_denominator": int(quiz_sum_question_slots),
        "quiz_lifetime_accuracy_percent": quiz_lifetime_accuracy_percent,
        "quiz_avg_score_sum_percent": float(round(quiz_avg_score_sum_percent, 6)),
        "quiz_avg_score_attempt_count": int(quiz_avg_score_attempt_count),
        "quiz_avg_score_percent": quiz_avg_score_percent,
        "quiz_attempt_sample_limit": int(proof_quiz_attempt_sample_limit),
        "quiz_attempt_sample_truncated": bool(len(attempts) > proof_quiz_attempt_sample_limit),
        "quiz_attempt_sample": quiz_attempt_sample,
    }

    return {
        "user_id": user_id,
        "total_study_time_seconds": total_study_time_seconds,
        "pomodoro_sessions_count": pomodoro_sessions_count,
        "total_quiz_count": int(total_quiz_count),
        "average_quiz_score": float(average_quiz_score) if average_quiz_score is not None else None,
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
        "quiz_sum_correct": int(quiz_sum_correct),
        "quiz_sum_wrong": int(quiz_sum_wrong),
        "quiz_sum_unanswered": int(quiz_sum_unanswered),
        "quiz_sum_question_slots": int(quiz_sum_question_slots),
        "quiz_lifetime_accuracy_percent": quiz_lifetime_accuracy_percent,
        "pomodoro_by_topic_seconds": by_topic,
        "pomodoro_recent_sessions": pomodoro_recent_sessions,
        "calculation_proof": calculation_proof,
    }


def adaptive_focus_challenge_topics(
    db: Session,
    user_id: int,
    *,
    max_weak: int = 8,
    max_strong: int = 6,
) -> Tuple[List[str], List[str]]:
    """
    Lifetime topic aggregates for quiz generation: weak topics → more questions,
    strong topics → harder / synthesis bias (challenge_topics).
    """
    topic_rows = (
        db.query(QuizAttemptTopicResult)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptTopicResult.quiz_attempt_id)
        .filter(QuizAttempt.user_id == user_id)
        .all()
    )
    agg: Dict[str, Tuple[int, int]] = {}
    for row in topic_rows:
        t = (row.topic or "").strip()
        if not t:
            continue
        c, w = agg.get(t, (0, 0))
        agg[t] = (c + int(row.correct_count or 0), w + int(row.wrong_count or 0))

    scored: List[Tuple[str, float, int, int]] = []
    for t, (c, w) in agg.items():
        tot = c + w
        if tot <= 0:
            continue
        rate = c / tot
        scored.append((t, rate, tot, w))

    scored.sort(key=lambda x: (x[1], -x[3], x[0].lower()))
    weak_out: List[str] = []
    for t, rate, tot, w in scored:
        if rate >= STRONG_MIN:
            continue
        if rate < DEVELOPING_MIN or (rate < 0.65 and w >= 2) or w >= 3:
            weak_out.append(t)
        if len(weak_out) >= max_weak:
            break

    strong_out: List[str] = []
    for t, rate, tot, _ in sorted(scored, key=lambda x: (-x[1], x[0].lower())):
        if rate >= STRONG_MIN and tot >= 2:
            strong_out.append(t)
        if len(strong_out) >= max_strong:
            break

    return weak_out, strong_out


@dataclass
class QuizGenerationProfile:
    weak_topics: List[str]
    strong_topics: List[str]
    recent_score_avg: Optional[float]
    adaptive_difficulty: str
    recent_stems: List[str]
    blocked_topic_types: Dict[str, List[str]]


def _difficulty_from_recent_score(score_avg: Optional[float]) -> str:
    """
    Mandatory adaptive difficulty policy:
    - low score -> beginner
    - medium score -> normal
    - high score -> technical
    """
    if score_avg is None:
        return "normal"
    if score_avg < 45.0:
        return "beginner"
    if score_avg < 75.0:
        return "normal"
    return "technical"


def build_quiz_generation_profile(
    db: Session,
    user_id: int,
    *,
    document_db_id: Optional[int] = None,
    max_weak: int = 10,
    max_strong: int = 8,
    recent_attempts: int = 8,
    recent_stems_cap: int = 120,
) -> QuizGenerationProfile:
    """
    Personalized quiz generation profile for adaptive pipelines:
    - weak/strong topic priorities
    - recent performance -> adaptive difficulty
    - anti-repetition guardrails (recent stems + topic->question_type blocks)
    """
    weak_topics, strong_topics = adaptive_focus_challenge_topics(
        db, user_id, max_weak=max_weak, max_strong=max_strong
    )

    base_attempt_q = db.query(QuizAttempt).filter(QuizAttempt.user_id == int(user_id))
    if document_db_id is not None:
        base_attempt_q = base_attempt_q.filter(QuizAttempt.document_db_id == int(document_db_id))

    recent_attempt_rows = (
        base_attempt_q.order_by(desc(QuizAttempt.created_at), desc(QuizAttempt.id))
        .limit(max(1, min(recent_attempts, 24)))
        .all()
    )
    recent_attempt_ids = [int(a.id) for a in recent_attempt_rows if getattr(a, "id", None) is not None]

    recent_score_avg: Optional[float] = None
    if recent_attempt_rows:
        vals = [float(a.score_percentage or 0.0) for a in recent_attempt_rows]
        recent_score_avg = sum(vals) / float(len(vals))

    recent_stems: List[str] = []
    blocked_topic_types: Dict[str, Set[str]] = {}
    if recent_attempt_ids:
        q_rows = (
            db.query(QuizAttemptQuestionResult)
            .filter(QuizAttemptQuestionResult.quiz_attempt_id.in_(recent_attempt_ids))
            .order_by(desc(QuizAttemptQuestionResult.id))
            .limit(max(16, min(recent_stems_cap, 300)))
            .all()
        )
        for row in q_rows:
            stem = (getattr(row, "question_text", None) or "").strip()
            if stem:
                recent_stems.append(stem)
            topic = (getattr(row, "topic", None) or "").strip().lower()
            if not topic:
                continue
            fjson = getattr(row, "feedback_json", None) or ""
            qtype = ""
            if fjson:
                try:
                    import json

                    payload = json.loads(str(fjson))
                    if isinstance(payload, dict):
                        qtype = str(payload.get("question_type") or "").strip().lower()
                except Exception:
                    qtype = ""
            if qtype:
                blocked_topic_types.setdefault(topic, set()).add(qtype)

    uniq_stems: List[str] = []
    seen: Set[str] = set()
    for s in recent_stems:
        k = " ".join((s or "").strip().lower().split())
        if not k or k in seen:
            continue
        seen.add(k)
        uniq_stems.append(s)
        if len(uniq_stems) >= max(12, min(recent_stems_cap, 200)):
            break

    blocked_out: Dict[str, List[str]] = {
        t: sorted(list(v))[:4] for t, v in blocked_topic_types.items() if v
    }

    return QuizGenerationProfile(
        weak_topics=weak_topics,
        strong_topics=strong_topics,
        recent_score_avg=recent_score_avg,
        adaptive_difficulty=_difficulty_from_recent_score(recent_score_avg),
        recent_stems=uniq_stems,
        blocked_topic_types=blocked_out,
    )

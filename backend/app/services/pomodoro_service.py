# svc: pomodoro | tr: pomodoro oturumu başlat,bitir,iptal, çalışma istatistigi / en: start/end/cancel pomodoro sessions, study stats

from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.pomodoro_model import PomodoroSession


# fn: start_session | tr: yeni pomodoro oturumu baslat (status=running) / en: start new pomodoro session
def start_session(
    db: Session,
    user_id: int,
    topic: Optional[str],
    start_time: Optional[datetime] = None,
) -> PomodoroSession:
    session_start = start_time or datetime.utcnow()

    db_session = PomodoroSession(
        user_id=user_id,
        topic=topic,
        start_time=session_start,
        status="running",
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session


# fn: end_session | tr: oturumu bitir, süre hesapla (completed_now = xp icin) / en: end session, compute duration
def end_session(
    db: Session,
    session_id: int,
    end_time: Optional[datetime] = None,
) -> Tuple[PomodoroSession, bool]:
    db_session = db.query(PomodoroSession).filter(PomodoroSession.id == session_id).first()
    if not db_session:
        raise ValueError("Pomodoro session not found.")
    if db_session.status != "running":
        return db_session, False

    session_end = end_time or datetime.utcnow()
    duration = int((session_end - db_session.start_time).total_seconds())
    duration = max(duration, 0)

    db_session.end_time = session_end
    db_session.duration_seconds = duration
    db_session.status = "completed"

    db.commit()
    db.refresh(db_session)
    # tr: completed_now true ise route xp verir / en: completed_now true triggers xp in route
    return db_session, True


# fn: cancel_session | tr: oturumu iptal et, süre sayılmaz / en: cancel session, no duration counted
def cancel_session(
    db: Session,
    session_id: int,
    end_time: Optional[datetime] = None,
) -> Tuple[PomodoroSession, bool]:
    db_session = db.query(PomodoroSession).filter(PomodoroSession.id == session_id).first()
    if not db_session:
        raise ValueError("Pomodoro session not found.")
    if db_session.status == "cancelled":
        return db_session, False
    if db_session.status not in ("running", "completed"):
        return db_session, False

    session_end = end_time or datetime.utcnow()
    db_session.end_time = session_end
    db_session.duration_seconds = None
    db_session.status = "cancelled"

    db.commit()
    db.refresh(db_session)
    return db_session, True


# fn: get_user_stats | tr: tamamlanan oturum sayısı ve konuya göre süre / en: completed session count and time by topic
def get_user_stats(db: Session, user_id: int) -> Dict:
    completed = db.query(PomodoroSession).filter(
        PomodoroSession.user_id == user_id,
        PomodoroSession.status == "completed",
        PomodoroSession.duration_seconds.isnot(None),
    ).all()

    sessions_count = len(completed)
    total_seconds = sum(s.duration_seconds or 0 for s in completed)

    by_topic: Dict[str, int] = {}
    for s in completed:
        key = s.topic or "General"
        by_topic[key] = by_topic.get(key, 0) + (s.duration_seconds or 0)

    return {
        "user_id": user_id,
        "sessions_count": sessions_count,
        "total_study_time_seconds": total_seconds,
        "by_topic_seconds": by_topic,
    }

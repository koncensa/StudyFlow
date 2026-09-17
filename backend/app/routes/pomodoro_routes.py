# route: pomodoro_routes | tr: pomodoro çalışma oturumu http endpoint'leri — main.py'de /pomodoro prefix ile bağlanır / en: pomodoro study session http endpoints mounted at /pomodoro prefix in main.py

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.deps.auth import require_user_id, require_user_id_matching
from app.models.pomodoro_model import PomodoroSession
from app.schemas.pomodoro_schema import (
    PomodoroCancelRequest,
    PomodoroEndRequest,
    PomodoroEndResponse,
    PomodoroStartRequest,
    PomodoroStartResponse,
    PomodoroStatsResponse,
)
from app.services.pomodoro_service import cancel_session, end_session, get_user_stats, start_session

router = APIRouter()


# fn: pomodoro_start | tr: POST /pomodoro/start — yeni pomodoro oturumu başlat / en: POST start new pomodoro session
@router.post("/start", response_model=PomodoroStartResponse)
def pomodoro_start(
    payload: PomodoroStartRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PomodoroStartResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id_matching(
        authorization, int(payload.user_id) if payload.user_id is not None else None
    )
    try:
        session = start_session(
            db=db,
            user_id=uid,
            topic=payload.topic,
            start_time=payload.start_time,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return PomodoroStartResponse(session_id=session.id, start_time=session.start_time)


# fn: pomodoro_end | tr: POST /pomodoro/end — oturumu tamamla, süre kaydet, xp tetikle / en: POST complete session save duration trigger xp
@router.post("/end", response_model=PomodoroEndResponse)
def pomodoro_end(
    payload: PomodoroEndRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PomodoroEndResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    row = db.query(PomodoroSession).filter(PomodoroSession.id == payload.session_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")
    if int(row.user_id) != int(uid):
        raise HTTPException(status_code=403, detail="This Pomodoro session belongs to another user.")
    try:
        session, completed_now = end_session(
            db=db,
            session_id=payload.session_id,
            end_time=payload.end_time,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if completed_now:
        try:
            from app.services import profile_service

            profile_service.apply_after_pomodoro_completed(db, session.user_id)
        except Exception:
            pass

    return PomodoroEndResponse(
        session_id=session.id,
        end_time=session.end_time,
        duration_seconds=session.duration_seconds or 0,
    )


# fn: pomodoro_cancel | tr: POST /pomodoro/cancel — oturumu iptal et, süre sayılmaz / en: POST cancel session duration not counted
@router.post("/cancel", response_model=PomodoroEndResponse)
def pomodoro_cancel(
    payload: PomodoroCancelRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PomodoroEndResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    row = db.query(PomodoroSession).filter(PomodoroSession.id == payload.session_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")
    if int(row.user_id) != int(uid):
        raise HTTPException(status_code=403, detail="This Pomodoro session belongs to another user.")
    try:
        session, _ = cancel_session(
            db=db,
            session_id=payload.session_id,
            end_time=payload.end_time,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return PomodoroEndResponse(
        session_id=session.id,
        end_time=session.end_time or row.end_time or row.start_time,
        duration_seconds=0,
    )


# fn: pomodoro_stats | tr: GET /pomodoro/stats — kullanıcı pomodoro istatistikleri / en: GET user pomodoro statistics
@router.get("/stats", response_model=PomodoroStatsResponse)
def pomodoro_stats(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PomodoroStatsResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    stats = get_user_stats(db=db, user_id=uid)
    return PomodoroStatsResponse(**stats)

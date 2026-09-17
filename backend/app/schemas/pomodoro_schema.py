# svc: pomodoro_schema | tr: pomodoro oturumu api istek/yanıt pydantic şemaları / en: pydantic request/response schemas for pomodoro session api

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel


# schema: PomodoroStartRequest | tr: pomodoro oturumu başlatma isteği / en: start pomodoro session request
class PomodoroStartRequest(BaseModel):
    user_id: int = 1
    topic: Optional[str] = None
    start_time: Optional[datetime] = None  # tr: boşsa backend şimdi kullanır / en: if None backend uses now


# schema: PomodoroStartResponse | tr: başlatılan oturum kimliği ve başlangıç zamanı / en: started session id and start time
class PomodoroStartResponse(BaseModel):
    session_id: int
    start_time: datetime


# schema: PomodoroEndRequest | tr: pomodoro oturumunu tamamlama isteği / en: complete pomodoro session request
class PomodoroEndRequest(BaseModel):
    session_id: int
    end_time: Optional[datetime] = None  # tr: boşsa backend şimdi kullanır / en: if None backend uses now


# schema: PomodoroCancelRequest | tr: pomodoro oturumunu iptal isteği / en: cancel pomodoro session request
class PomodoroCancelRequest(BaseModel):
    session_id: int
    end_time: Optional[datetime] = None  # tr: boşsa backend şimdi kullanır / en: if None backend uses now


# schema: PomodoroEndResponse | tr: biten/iptal oturum süresi yanıtı / en: ended or cancelled session duration response
class PomodoroEndResponse(BaseModel):
    session_id: int
    end_time: datetime
    duration_seconds: int


# schema: PomodoroStatsResponse | tr: kullanıcı pomodoro istatistikleri / en: user pomodoro statistics
class PomodoroStatsResponse(BaseModel):
    user_id: int
    sessions_count: int
    total_study_time_seconds: int
    by_topic_seconds: Dict[str, int] = {}

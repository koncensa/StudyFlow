# model: pomodoro_model | tr: pomodoro çalışma oturumu mysql tablo tanımı / en: pomodoro study session mysql table definition

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.database.connection import Base


# model: PomodoroSession | tr: tek pomodoro oturumu — başlangıç, bitiş, süre, durum / en: single pomodoro session start end duration status
class PomodoroSession(Base):
    __tablename__ = "pomodoro_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    # tr: isteğe bağlı konu etiketi (ör. Calculus) / en: optional topic label e.g. Calculus
    topic = Column(String(255), nullable=True, index=True)

    start_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    # tr: tamamlanan oturumda saniye cinsinden süre; iptalde null / en: duration in seconds when completed null when cancelled
    duration_seconds = Column(Integer, nullable=True)

    # tr: running | completed | cancelled / en: running completed cancelled
    status = Column(String(20), nullable=False, default="running")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

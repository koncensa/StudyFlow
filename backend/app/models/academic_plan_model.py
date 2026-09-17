# model: academic_plan_model | tr: akademik çalışma planı mysql tablo tanımı (sqlalchemy orm) / en: academic study plan mysql table definition sqlalchemy orm

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.database.connection import Base


# model: AcademicStudyPlan | tr: kullanıcı deadline planı + günlük görevler (tasks_json) / en: user deadline plan plus daily tasks in tasks_json
class AcademicStudyPlan(Base):
    """User-defined deadline study plan with generated daily tasks (JSON)."""

    __tablename__ = "academic_study_plans"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # tr: plan sahibi kullanıcı — users.id ile bağlı / en: plan owner user linked to users.id
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    course_name = Column(String(255), nullable=False)
    goal_text = Column(Text, nullable=False)
    deadline_date = Column(Date, nullable=False)
    daily_hours = Column(Float, nullable=False)
    # tr: haftalık çalışma günleri json dizisi (0=pzt … 6=paz) / en: weekly study weekdays json array 0=mon .. 6=sun
    study_days_json = Column(String(256), nullable=False)
    # tr: zayıf konular json listesi / en: weak topics json list
    weak_topics_json = Column(Text, nullable=False)
    # tr: güvenilen konular json listesi / en: confident topics json list
    confident_topics_json = Column(Text, nullable=False, default="[]")
    # tr: konu taslağı / bölüm sırası json listesi / en: topic outline chapter order json list
    topic_outline_json = Column(Text, nullable=False, default="[]")
    # tr: üretilmiş günlük görevler json (task_id, date, kind, status…) / en: generated daily tasks json task_id date kind status
    tasks_json = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    # tr: plan kilitli mi (finish sonrası düzenlenemez) / en: plan locked after finish
    is_finished = Column(Boolean, nullable=False, default=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    completed_count = Column(Integer, nullable=False, default=0)
    missed_count = Column(Integer, nullable=False, default=0)
    total_task_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

# svc: academic_plan_schema | tr: akademik çalışma planı api istek/yanıt pydantic şemaları / en: pydantic request/response schemas for academic study plan api

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# schema: AcademicPlanGenerateRequest | tr: yeni plan üretme isteği (ders, hedef, deadline, günler) / en: generate new plan request course goal deadline days
class AcademicPlanGenerateRequest(BaseModel):
    course_name: str = Field(..., min_length=1, max_length=255)
    goal_text: str = Field(..., min_length=1, max_length=4000)
    deadline_date: date
    daily_hours: float = Field(..., gt=0, le=16)
    study_days: List[int] = Field(..., min_length=1, max_length=7)
    weak_topics: List[str] = Field(default_factory=list)
    confident_topics: List[str] = Field(default_factory=list)
    topic_outline: List[str] = Field(
        default_factory=list,
        description="Optional ordered chapter/topic names to spread across the plan.",
    )
    start_date: Optional[date] = None
    locale: Literal["en", "tr"] = "en"

    # fn: study_days_range | tr: study_days 0–6 aralığında benzersiz sıralı mı / en: validate study_days unique sorted 0=mon..6=sun
    @field_validator("study_days")
    @classmethod
    def study_days_range(cls, v: List[int]) -> List[int]:
        out = sorted({int(x) for x in v})
        for d in out:
            if d < 0 or d > 6:
                raise ValueError("study_days must be weekday integers 0=Mon .. 6=Sun")
        return out


# schema: AcademicPlanTaskOut | tr: tek günlük görev satırı (tarih, başlık, tür, süre, durum) / en: single daily task row date title kind duration status
class AcademicPlanTaskOut(BaseModel):
    task_id: str
    date_iso: str
    title: str
    kind: Literal["study", "review", "quiz", "buffer"]
    minutes_estimate: int
    status: Literal["pending", "completed", "missed"]
    topic_focus: Optional[str] = None


# schema: AcademicPlanView | tr: tam plan görünümü (görevler, öncelikler, bugün önerisi) / en: full plan view tasks priorities today recommendation
class AcademicPlanView(BaseModel):
    plan_id: int
    course_name: str
    goal_text: str
    deadline_date: str
    daily_hours: float
    study_days: List[int]
    weak_topics: List[str]
    confident_topics: List[str]
    topic_outline: List[str]
    days_until_deadline: int
    study_slots_remaining: int
    tasks: List[AcademicPlanTaskOut]
    priority_high: List[str]
    priority_medium: List[str]
    priority_strong: List[str]
    today_iso: str
    today_recommendation: Optional[AcademicPlanTaskOut] = None
    is_finished: bool = False
    finished_at: Optional[str] = None
    completed_count: int = 0
    missed_count: int = 0
    total_task_count: int = 0


# schema: AcademicPlanLatestResponse | tr: kullanıcının güncel planı var mı yanıtı / en: whether user has a latest plan response
class AcademicPlanLatestResponse(BaseModel):
    has_plan: bool
    plan: Optional[AcademicPlanView] = None


# schema: AcademicPlanTaskPatch | tr: görev durumu güncelleme isteği / en: update task status request
class AcademicPlanTaskPatch(BaseModel):
    status: Literal["pending", "completed", "missed"]


# schema: AcademicPlanCatchUpRequest | tr: kaçırılan gün için telafi planı isteği / en: catch-up plan request for missed date
class AcademicPlanCatchUpRequest(BaseModel):
    missed_date: date


# schema: AcademicPlanHistoryItem | tr: tamamlanan plan geçmişi tek satır / en: single finished plan history row
class AcademicPlanHistoryItem(BaseModel):
    plan_id: int
    course_name: str
    goal_text: str
    finished_at: str
    completed_count: int
    missed_count: int
    total_task_count: int


# schema: AcademicPlanHistoryResponse | tr: plan geçmişi listesi yanıtı / en: plan history list response
class AcademicPlanHistoryResponse(BaseModel):
    items: List[AcademicPlanHistoryItem]

# route: academic_plan_routes | tr: akademik çalışma planı http endpoint'leri — main.py'de /study prefix ile bağlanır / en: academic study plan http endpoints mounted at /study prefix in main.py

from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.deps.auth import require_user_id
from app.schemas.academic_plan_schema import (
    AcademicPlanCatchUpRequest,
    AcademicPlanGenerateRequest,
    AcademicPlanHistoryResponse,
    AcademicPlanLatestResponse,
    AcademicPlanTaskPatch,
    AcademicPlanView,
)
from app.services import academic_plan_service as aps

router = APIRouter()


# fn: _today | tr: istemci as_of tarihi yoksa bugünü kullan / en: use client as_of date or today
def _today(as_of: Optional[date]) -> date:
    return as_of or date.today()


# fn: academic_plan_latest | tr: GET /study/academic-plan/latest — aktif son planı getir / en: GET latest active plan for user
@router.get("/academic-plan/latest", response_model=AcademicPlanLatestResponse)
def academic_plan_latest(
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query(description="Client local 'today' for recommendations (YYYY-MM-DD).")] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanLatestResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    view = aps.get_latest_plan(db, uid, _today(as_of))
    if not view:
        return AcademicPlanLatestResponse(has_plan=False, plan=None)
    return AcademicPlanLatestResponse(has_plan=True, plan=view)


# fn: academic_plan_generate | tr: POST /study/academic-plan/generate — yeni plan üret (eski aktif plan arşivlenir) / en: POST generate new plan archives previous active
@router.post("/academic-plan/generate", response_model=AcademicPlanView)
def academic_plan_generate(
    body: AcademicPlanGenerateRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query()] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    start = body.start_date or _today(as_of)
    if body.deadline_date < start:
        raise HTTPException(status_code=400, detail="deadline_date must be on or after start_date / today.")
    body = body.model_copy(update={"start_date": start})
    try:
        return aps.create_plan(db, uid, body, _today(as_of))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# fn: academic_plan_patch_task | tr: PATCH görev durumu güncelle (pending/completed/missed) / en: PATCH update single task status
@router.patch("/academic-plan/{plan_id}/tasks/{task_id}", response_model=AcademicPlanView)
def academic_plan_patch_task(
    plan_id: int,
    task_id: str,
    body: AcademicPlanTaskPatch,
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query()] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    try:
        view = aps.update_task_status(db, uid, plan_id, task_id, body.status, _today(as_of))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not view:
        raise HTTPException(status_code=404, detail="Plan or task not found.")
    return view


# fn: academic_plan_catch_up | tr: POST kaçırılan günü sonraki bekleyen slota telafi et / en: POST merge missed day into next pending slot
@router.post("/academic-plan/{plan_id}/catch-up", response_model=AcademicPlanView)
def academic_plan_catch_up(
    plan_id: int,
    body: AcademicPlanCatchUpRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query()] = None,
    locale: Annotated[Literal["en", "tr"], Query()] = "en",
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    try:
        view = aps.catch_up_missed_day(db, uid, plan_id, body.missed_date, _today(as_of), locale=locale)
    except ValueError as e:
        msg = str(e)
        status_code = 409 if "locked" in msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=msg)
    if not view:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return view


# fn: academic_plan_finish | tr: POST planı bitir — tüm görevler completed veya missed olmalı / en: POST finish plan all tasks must be resolved
@router.post("/academic-plan/{plan_id}/finish", response_model=AcademicPlanView)
def academic_plan_finish(
    plan_id: int,
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query()] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    try:
        view = aps.finish_plan(db, uid, plan_id, _today(as_of))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not view:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return view


# fn: academic_plan_history | tr: GET bitmiş plan geçmişi listesi / en: GET list of finished plans
@router.get("/academic-plan/history", response_model=AcademicPlanHistoryResponse)
def academic_plan_history(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanHistoryResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    return AcademicPlanHistoryResponse(items=aps.list_history(db, uid))


# fn: academic_plan_history_item | tr: GET bitmiş plan detayı (görevler dahil) / en: GET finished plan detail including tasks
@router.get("/academic-plan/history/{plan_id}", response_model=AcademicPlanView)
def academic_plan_history_item(
    plan_id: int,
    authorization: Annotated[Optional[str], Header()] = None,
    as_of: Annotated[Optional[date], Query()] = None,
    db: Optional[Session] = Depends(get_db),
) -> AcademicPlanView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    view = aps.get_finished_plan_by_id(db, uid, plan_id, _today(as_of))
    if not view:
        raise HTTPException(status_code=404, detail="History plan not found.")
    return view


# fn: academic_plan_delete | tr: DELETE planı kalıcı sil (204 yanıt) / en: DELETE permanently remove plan returns 204
@router.delete("/academic-plan/{plan_id}", status_code=204)
def academic_plan_delete(
    plan_id: int,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> None:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured yet.")
    uid = require_user_id(authorization)
    if not aps.delete_plan(db, uid, plan_id):
        raise HTTPException(status_code=404, detail="Plan not found.")
    return None

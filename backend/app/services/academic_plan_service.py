# svc: academic_plan | tr: günlük calisma planı üret, kaydet, güncelle / en: build, save, update daily study plans

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.models.academic_plan_model import AcademicStudyPlan
from app.schemas.academic_plan_schema import (
    AcademicPlanGenerateRequest,
    AcademicPlanHistoryItem,
    AcademicPlanTaskOut,
    AcademicPlanView,
)


# fn: _dumps | tr: nesneyi json string yap / en: object to json string
def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# fn: _loads | tr: json oku, bos ise [] / en: parse json, empty -> []
def _loads(s: str) -> Any:
    return json.loads(s) if s else []


# fn: _iter_study_dates | tr: seçili hafta gunlerindeki tarihleri listele / en: list dates on chosen weekdays
def _iter_study_dates(start: date, end: date, weekdays: Set[int]) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
    return out


# fn: _priority_bands | tr: konuları yüksek/orta/güclü gruplara ayir / en: split topics into priority bands
def _priority_bands(weak: Sequence[str], outline: Sequence[str], confident: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    w = [str(x).strip() for x in weak if str(x).strip()]
    outline_clean = [str(x).strip() for x in outline if str(x).strip()]
    conf = [str(x).strip() for x in confident if str(x).strip()]

    if not w and outline_clean:
        n = max(1, len(outline_clean) // 3)
        high = outline_clean[:n]
        medium = outline_clean[n : 2 * n]
        # tr: guclu = sadece confident konular / en: strong = confident topics only
        strong = list(conf)
        return high, medium, strong

    if not w:
        return [], outline_clean[: max(1, len(outline_clean) // 2)], list(conf)

    hi_n = max(1, (len(w) + 1) // 2)
    high = w[:hi_n]
    medium = w[hi_n:]
    strong = list(conf)
    return high, medium, strong


# fn: _pick_topic_rotating | tr: gün sırasına göre konu seç / en: pick topic by day rotation
def _pick_topic_rotating(
    day_index: int,
    high: Sequence[str],
    medium: Sequence[str],
    outline: Sequence[str],
) -> str:
    pool: List[str] = []
    if high:
        pool.append(high[day_index % len(high)])
    if medium:
        pool.append(medium[day_index % len(medium)])
    if outline:
        pool.append(outline[day_index % len(outline)])
    if not pool:
        return ""
    return pool[day_index % len(pool)]


# fn: _labels | tr: görev başlık etiketleri (tr/en) / en: task title labels by locale
def _labels(locale: str) -> Dict[str, str]:
    if locale == "tr":
        return {
            "study": "Çalışma",
            "review": "Tekrar",
            "quiz": "Mini quiz",
            "buffer": "Dinlenme / tampon",
            "weak_block": "Zayıf konu ağırlığı",
            "outline": "Konu özeti",
            "goal": "Hedef",
            "questions": "soru",
        }
    return {
        "study": "Study block",
        "review": "Review",
        "quiz": "Mini quiz",
        "buffer": "Rest / buffer",
        "weak_block": "Weak-topic focus",
        "outline": "Topic digest",
        "goal": "Goal",
        "questions": "questions",
    }


# fn: build_tasks_from_inputs | tr: formdan gunluk gorev listesi uret / en: build daily tasks from form
def build_tasks_from_inputs(body: AcademicPlanGenerateRequest) -> List[Dict[str, Any]]:
    locale = body.locale or "en"
    lab = _labels(locale)
    start = body.start_date or date.today()
    # tr: son calisma gunu sinavdan 1 gun once / en: last study day is 1 day before deadline
    last = body.deadline_date - timedelta(days=1)
    if last < start:
        last = body.deadline_date

    weekdays = set(body.study_days)
    study_dates = _iter_study_dates(start, last, weekdays)
    high, medium, _ = _priority_bands(body.weak_topics, body.topic_outline, body.confident_topics)
    outline = [str(x).strip() for x in body.topic_outline if str(x).strip()]

    hours = float(body.daily_hours)
    # tr: gunluk dakika 30-240 arasi / en: daily minutes clamped 30-240
    minutes_cap = max(30, min(240, int(round(hours * 60))))

    tasks: List[Dict[str, Any]] = []
    for i, d in enumerate(study_dates):
        topic = _pick_topic_rotating(i, high, medium, outline)
        cycle = i % 7  # tr: 7 gunluk dongu / en: 7-day cycle

        if cycle == 6:
            # kind: buffer | tr: dinlenme gunu / en: rest day
            kind = "buffer"
            title = f"{lab['buffer']} — {body.course_name}"
            mins = max(20, minutes_cap // 4)
            focus = None
        elif cycle == 3:
            # kind: review | tr: tekrar gunu / en: review day
            kind = "review"
            title = f"{lab['review']}: {topic or body.course_name}"
            mins = max(25, minutes_cap // 2)
            focus = topic or None
        elif cycle == 5:
            # kind: quiz | tr: mini quiz gunu / en: mini quiz day
            kind = "quiz"
            n = max(5, min(20, 5 + (i % 6) * 2))
            title = f"{lab['quiz']}: {topic or body.course_name} ({n} {lab['questions']})"
            mins = max(25, int(minutes_cap * 0.45))
            focus = topic or None
        else:
            # kind: study | tr: normal calisma / en: regular study
            kind = "study"
            if high and topic in high:
                title = f"{lab['weak_block']}: {topic}"
            elif topic:
                title = f"{lab['outline']}: {topic}"
            else:
                title = f"{lab['study']}: {body.course_name}"
            mins = minutes_cap
            focus = topic or None

        tasks.append(
            {
                "task_id": str(uuid.uuid4()),
                "date_iso": d.isoformat(),
                "title": title,
                "kind": kind,
                "minutes_estimate": int(mins),
                "status": "pending",
                "topic_focus": focus,
            }
        )
    return tasks


# fn: _days_until | tr: sinava kalan gun / en: days until deadline
def _days_until(deadline: date, today: date) -> int:
    return (deadline - today).days


# fn: _task_date_iso_key | tr: iso'dan yyyy-mm-dd al / en: get yyyy-mm-dd from iso
def _task_date_iso_key(raw: Any) -> str:
    s = str(raw or "").strip()
    return s[:10] if len(s) >= 10 else s


# fn: _parse_date_from_iso_raw | tr: iso -> date / en: iso to date
def _parse_date_from_iso_raw(raw: Any) -> Optional[date]:
    key = _task_date_iso_key(raw)
    if len(key) < 10:
        return None
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


# fn: _parse_task_date | tr: gorev dict'inden tarih / en: date from task dict
def _parse_task_date(task: Dict[str, Any]) -> Optional[date]:
    return _parse_date_from_iso_raw(task.get("date_iso"))


# fn: _view_from_row | tr: db satirini api view'a cevir / en: db row to api view
def _view_from_row(row: AcademicStudyPlan, today: date) -> AcademicPlanView:
    tasks_raw = _loads(row.tasks_json)
    tasks: List[AcademicPlanTaskOut] = []
    for t in tasks_raw:
        tasks.append(
            AcademicPlanTaskOut(
                task_id=str(t.get("task_id", "")),
                date_iso=str(t.get("date_iso", "")),
                title=str(t.get("title", "")),
                kind=t.get("kind", "study"),
                minutes_estimate=int(t.get("minutes_estimate", 45)),
                status=t.get("status", "pending"),
                topic_focus=t.get("topic_focus"),
            )
        )

    study_days = [int(x) for x in _loads(row.study_days_json)]
    weak = [str(x) for x in _loads(row.weak_topics_json)]
    confident = [str(x) for x in _loads(row.confident_topics_json)]
    outline = [str(x) for x in _loads(getattr(row, "topic_outline_json", None) or "[]")]
    # tr: oncelik listeleri formdan gelir / en: priority lists come from form fields
    high, medium, strong = list(weak), [], list(confident)

    slots_left = 0
    completed_count = 0
    missed_count = 0
    for t in tasks:
        if t.status == "completed":
            completed_count += 1
        if t.status == "missed":
            missed_count += 1
        if t.status != "pending":
            continue
        d_t = _parse_date_from_iso_raw(t.date_iso)
        if d_t is not None and d_t >= today:
            slots_left += 1

    # tr: bugunun gorevi, yoksa en yakin gelecek / en: today's task, else nearest upcoming
    today_key = today.isoformat()
    today_rec: Optional[AcademicPlanTaskOut] = None
    for t in tasks:
        if t.status == "pending" and _task_date_iso_key(t.date_iso) == today_key:
            today_rec = t
            break
    if today_rec is None:
        upcoming: List[Tuple[date, AcademicPlanTaskOut]] = []
        for t in tasks:
            if t.status != "pending":
                continue
            d_t = _parse_date_from_iso_raw(t.date_iso)
            if d_t is None or d_t < today:
                continue
            upcoming.append((d_t, t))
        upcoming.sort(key=lambda pair: pair[0])
        if upcoming:
            today_rec = upcoming[0][1]

    return AcademicPlanView(
        plan_id=int(row.id),
        course_name=row.course_name,
        goal_text=row.goal_text,
        deadline_date=row.deadline_date.isoformat(),
        daily_hours=float(row.daily_hours),
        study_days=study_days,
        weak_topics=weak,
        confident_topics=confident,
        topic_outline=outline,
        days_until_deadline=_days_until(row.deadline_date, today),
        study_slots_remaining=slots_left,
        tasks=tasks,
        priority_high=high,
        priority_medium=medium,
        priority_strong=strong,
        today_iso=today.isoformat(),
        today_recommendation=today_rec,
        is_finished=bool(getattr(row, "is_finished", False)),
        finished_at=row.finished_at.isoformat() if getattr(row, "finished_at", None) else None,
        completed_count=completed_count,
        missed_count=missed_count,
        total_task_count=len(tasks),
    )


# fn: create_plan | tr: yeni plan oluştur, eski aktif planı arşivle / en: create plan, archive old active
def create_plan(db: Session, user_id: int, body: AcademicPlanGenerateRequest, today: date) -> AcademicPlanView:
    # tr: önceki aktif planları bitir / en: finish previous active plans
    prev_active_rows = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.user_id == int(user_id), AcademicStudyPlan.is_finished.is_(False))
        .all()
    )
    if prev_active_rows:
        finished_at = datetime.utcnow()
        for prev in prev_active_rows:
            prev_tasks: List[Dict[str, Any]] = _loads(prev.tasks_json)
            prev.completed_count = sum(1 for t in prev_tasks if t.get("status") == "completed")
            prev.missed_count = sum(1 for t in prev_tasks if t.get("status") == "missed")
            prev.total_task_count = len(prev_tasks)
            prev.is_finished = True
            prev.finished_at = finished_at
            db.add(prev)

    tasks = build_tasks_from_inputs(body)
    if not tasks:
        raise ValueError(
            "No study slots fit between the start date and the day before your deadline. "
            "Try a later deadline, an earlier start, or more study weekdays."
        )
    row = AcademicStudyPlan(
        user_id=user_id,
        course_name=body.course_name.strip(),
        goal_text=body.goal_text.strip(),
        deadline_date=body.deadline_date,
        daily_hours=float(body.daily_hours),
        study_days_json=_dumps(body.study_days),
        weak_topics_json=_dumps([str(x).strip() for x in body.weak_topics if str(x).strip()]),
        confident_topics_json=_dumps([str(x).strip() for x in body.confident_topics if str(x).strip()]),
        topic_outline_json=_dumps([str(x).strip() for x in body.topic_outline if str(x).strip()]),
        tasks_json=_dumps(tasks),
        total_task_count=len(tasks),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_from_row(row, today)


# fn: delete_plan | tr: planı sil / en: delete plan
def delete_plan(db: Session, user_id: int, plan_id: int) -> bool:
    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.id == int(plan_id), AcademicStudyPlan.user_id == int(user_id))
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


# fn: get_latest_plan | tr: aktif son planı getir / en: get latest active plan
def get_latest_plan(db: Session, user_id: int, today: date) -> Optional[AcademicPlanView]:
    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.user_id == int(user_id), AcademicStudyPlan.is_finished.is_(False))
        .order_by(AcademicStudyPlan.updated_at.desc(), AcademicStudyPlan.id.desc())
        .first()
    )
    if not row:
        return None
    return _view_from_row(row, today)


# fn: get_plan_by_id | tr: plan id ile getir / en: get plan by id
def get_plan_by_id(db: Session, user_id: int, plan_id: int, today: date) -> Optional[AcademicPlanView]:
    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.id == int(plan_id), AcademicStudyPlan.user_id == int(user_id))
        .first()
    )
    if not row:
        return None
    return _view_from_row(row, today)


# fn: _persist_tasks | tr: görev listesini db'ye yaz / en: save task list to db
def _persist_tasks(db: Session, row: AcademicStudyPlan, tasks: List[Dict[str, Any]]) -> None:
    row.tasks_json = _dumps(tasks)
    db.add(row)
    db.commit()


# fn: update_task_status | tr: görev durumu güncelle (pending/completed/missed) / en: update task status
def update_task_status(db: Session, user_id: int, plan_id: int, task_id: str, status: str, today: date) -> Optional[AcademicPlanView]:
    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.id == int(plan_id), AcademicStudyPlan.user_id == int(user_id))
        .first()
    )
    if not row:
        return None
    if row.is_finished:
        raise ValueError("This plan is locked because it is already finished.")
    tasks: List[Dict[str, Any]] = _loads(row.tasks_json)
    found = False
    for t in tasks:
        if str(t.get("task_id")) == str(task_id):
            t["status"] = status
            found = True
            break
    if not found:
        return None
    _persist_tasks(db, row, tasks)
    db.refresh(row)
    row.completed_count = sum(1 for t in tasks if t.get("status") == "completed")
    row.missed_count = sum(1 for t in tasks if t.get("status") == "missed")
    row.total_task_count = len(tasks)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_from_row(row, today)


# fn: catch_up_missed_day | tr: kaçırılan günü sonraki güne telafi et / en: catch up missed day to next slot
def catch_up_missed_day(
    db: Session,
    user_id: int,
    plan_id: int,
    missed: date,
    today: date,
    locale: str = "en",
) -> Optional[AcademicPlanView]:
    def _msg(en: str, tr: str) -> str:
        return tr if (locale or "en") == "tr" else en

    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.id == int(plan_id), AcademicStudyPlan.user_id == int(user_id))
        .first()
    )
    if not row:
        return None
    if row.is_finished:
        raise ValueError("This plan is locked because it is already finished.")
    tasks: List[Dict[str, Any]] = _loads(row.tasks_json)
    picked_iso = missed.isoformat()
    on_picked_day = [t for t in tasks if _task_date_iso_key(t.get("date_iso")) == picked_iso]
    # tr: missed dahil, x isaretinden sonra da calissin / en: include missed after x mark in table
    same_day = [t for t in on_picked_day if t.get("status") in ("pending", "missed")]
    source_iso = picked_iso
    source_date = missed

    if not same_day:
        if on_picked_day:
            raise ValueError(
                _msg(
                    "That calendar day is already fully completed — nothing pending to move.",
                    "Bu gündeki tüm görevler tamamlanmış; taşınacak bekleyen iş yok.",
                )
            )
        # tr: seçilen gunde görev yoksa önceki son açık günü bul / en: no task on day -> find last open slot before
        best: Optional[date] = None
        for t in tasks:
            if t.get("status") not in ("pending", "missed"):
                continue
            d_t = _parse_task_date(t)
            if d_t is None or d_t > missed:
                continue
            if best is None or d_t > best:
                best = d_t
        if best is None:
            raise ValueError(
                _msg(
                    "No pending or missed tasks on or before that date — pick a day that still has open work.",
                    "Bu tarih veya öncesinde bekleyen/kaçırılmış görev yok — açık iş olan bir gün seç.",
                )
            )
        source_date = best
        source_iso = source_date.isoformat()
        same_day = [
            t
            for t in tasks
            if _task_date_iso_key(t.get("date_iso")) == source_iso and t.get("status") in ("pending", "missed")
        ]
        if not same_day:
            raise ValueError(
                _msg(
                    "Could not resolve a source study day to merge from.",
                    "Birleştirilecek kaynak çalışma günü bulunamadı.",
                )
            )

    try:
        weekdays = {int(x) for x in _loads(row.study_days_json)}
    except (TypeError, ValueError):
        weekdays = set()
    deadline = row.deadline_date

    def _weekday_ok(d: date) -> bool:
        return not weekdays or d.weekday() in weekdays

    # tr: kaynak gunden sonraki ilk bekleyen goreve birlestir / en: merge into first pending after source day
    def _merge_candidates() -> List[Tuple[date, int]]:
        merge_candidates: List[Tuple[date, int]] = []
        for idx, t in enumerate(tasks):
            if t.get("status") != "pending":
                continue
            d_task = _parse_task_date(t)
            if d_task is None:
                continue
            if d_task <= source_date:
                continue
            if d_task > deadline:
                continue
            if not _weekday_ok(d_task):
                continue
            merge_candidates.append((d_task, idx))
        merge_candidates.sort(key=lambda pair: pair[0])
        return merge_candidates

    merge_candidates = _merge_candidates()
    pending_on_source = [t for t in same_day if t.get("status") == "pending"]
    if pending_on_source and not merge_candidates:
        raise ValueError(
            _msg(
                "No later pending study slot to merge into — leave at least one upcoming day pending, or extend the deadline.",
                "Birleştirilecek ileri tarihte bekleyen çalışma günü yok — en az bir sonraki günü bekliyor bırakın veya sınav tarihini uzatın.",
            )
        )
    if not pending_on_source and not merge_candidates:
        raise ValueError(
            _msg(
                "No later pending study slot to attach catch-up notes to.",
                "Telafi notunun ekleneceği ileri tarihte bekleyen görev yok.",
            )
        )

    titles = [str(t.get("title", "")).strip() for t in same_day if str(t.get("title", "")).strip()]
    for t in tasks:
        if _task_date_iso_key(t.get("date_iso")) == source_iso and t.get("status") == "pending":
            t["status"] = "missed"

    blob = " · ".join(titles[:4])
    if len(titles) > 4:
        blob += " · …"

    if merge_candidates:
        _, idx = merge_candidates[0]
        t = tasks[idx]
        prefix = "[Catch-up]" if locale == "en" else "[Telafi]"
        t["title"] = f"{prefix} {blob} — {t.get('title', '')}".strip()
        try:
            base_m = int(t.get("minutes_estimate", 45))
        except (TypeError, ValueError):
            base_m = 45
        t["minutes_estimate"] = min(240, base_m + 25)

    _persist_tasks(db, row, tasks)
    db.refresh(row)
    row.completed_count = sum(1 for t in tasks if t.get("status") == "completed")
    row.missed_count = sum(1 for t in tasks if t.get("status") == "missed")
    row.total_task_count = len(tasks)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_from_row(row, today)


# fn: finish_plan | tr: planı bitir, tüm gorevler çözülmüş olmalı  / en: finish plan, all tasks resolved
def finish_plan(db: Session, user_id: int, plan_id: int, today: date) -> Optional[AcademicPlanView]:
    row = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.id == int(plan_id), AcademicStudyPlan.user_id == int(user_id))
        .first()
    )
    if not row:
        return None
    if row.is_finished:
        return _view_from_row(row, today)
    tasks: List[Dict[str, Any]] = _loads(row.tasks_json)
    if any(str(t.get("status", "pending")) == "pending" for t in tasks):
        raise ValueError("All tasks must be resolved (completed or missed) before finishing the plan.")
    row.is_finished = True
    row.finished_at = datetime.utcnow()
    row.completed_count = sum(1 for t in tasks if t.get("status") == "completed")
    row.missed_count = sum(1 for t in tasks if t.get("status") == "missed")
    row.total_task_count = len(tasks)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_from_row(row, today)


# fn: list_history | tr: bitmiş plan gecmişi / en: finished plan history
def list_history(db: Session, user_id: int) -> List[AcademicPlanHistoryItem]:
    rows = (
        db.query(AcademicStudyPlan)
        .filter(AcademicStudyPlan.user_id == int(user_id), AcademicStudyPlan.is_finished.is_(True))
        .order_by(AcademicStudyPlan.finished_at.desc(), AcademicStudyPlan.updated_at.desc(), AcademicStudyPlan.id.desc())
        .all()
    )
    items: List[AcademicPlanHistoryItem] = []
    for row in rows:
        items.append(
            AcademicPlanHistoryItem(
                plan_id=int(row.id),
                course_name=row.course_name,
                goal_text=row.goal_text,
                finished_at=(row.finished_at or row.updated_at).isoformat(),
                completed_count=int(row.completed_count or 0),
                missed_count=int(row.missed_count or 0),
                total_task_count=int(row.total_task_count or 0),
            )
        )
    return items


# fn: get_finished_plan_by_id | tr: bitmiş plan detayı / en: finished plan detail
def get_finished_plan_by_id(db: Session, user_id: int, plan_id: int, today: date) -> Optional[AcademicPlanView]:
    row = (
        db.query(AcademicStudyPlan)
        .filter(
            AcademicStudyPlan.id == int(plan_id),
            AcademicStudyPlan.user_id == int(user_id),
            AcademicStudyPlan.is_finished.is_(True),
        )
        .first()
    )
    if not row:
        return None
    return _view_from_row(row, today)

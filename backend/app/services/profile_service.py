# svc: profile | tr: kullanıcı profili, xp, rozet, misafir kullanıcı / en: user profile, xp, badges, guest user

import os
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.academic_plan_model import AcademicStudyPlan
from app.models.badge_model import Badge, UserBadge
from app.models.pomodoro_model import PomodoroSession
from app.models.quiz import QuizAttempt, QuizAttemptTopicResult
from app.models.user_model import User
from app.models.user_profile_model import UserProfile
from app.services import auth_service

# cfg: tüm rozet tanımları (db'ye seed) / en: all badge definitions seeded to db
BADGE_SEED: List[Dict[str, str]] = [
    {
        "slug": "first_pdf",
        "title": "First Upload",
        "description": "Uploaded your first PDF",
    },
    {
        "slug": "first_quiz",
        "title": "First Quiz",
        "description": "Completed your first quiz",
    },
    {
        "slug": "focus",
        "title": "First Focus",
        "description": "Finished your first study session",
    },
    {
        "slug": "quiz_rookie",
        "title": "Quiz Rookie",
        "description": "Completed 5 quizzes",
    },
    {
        "slug": "quiz_marathon",
        "title": "Quiz Marathon",
        "description": "Completed 20 quizzes",
    },
    {
        "slug": "deep_focus",
        "title": "Deep Focus",
        "description": "Completed 5 Pomodoro sessions",
    },
    {
        "slug": "focus_legend",
        "title": "Focus Legend",
        "description": "Completed 25 Pomodoro sessions",
    },
    {
        "slug": "sharp_mind",
        "title": "Sharp Mind",
        "description": "Scored 90% or above in a quiz",
    },
    {
        "slug": "comeback",
        "title": "Comeback",
        "description": "Improved your score",
    },
    {
        "slug": "weak_topic_crusher",
        "title": "Weak Topic Crusher",
        "description": "Improved a weak topic",
    },
    {
        "slug": "consistent_learner",
        "title": "Consistent Learner",
        "description": "Studied consistently",
    },
    {
        "slug": "smart_improver",
        "title": "Smart Improver",
        "description": "Followed advice and improved",
    },
    {
        "slug": "planner",
        "title": "Planner",
        "description": "Created a study plan",
    },
]


# fn: ensure_badges_seeded | tr: rozet tablosunu BADGE_SEED ile doldur / en: seed badges table from BADGE_SEED
def ensure_badges_seeded(db: Session) -> None:
    changed = False
    for row in BADGE_SEED:
        current = db.query(Badge).filter(Badge.slug == row["slug"]).order_by(Badge.id.asc()).first()
        if current is None:
            db.add(Badge(**row))
            changed = True
            continue
        title = row["title"]
        description = row["description"]
        if current.title != title or current.description != description:
            current.title = title
            current.description = description
            changed = True
    if changed:
        db.commit()


# fn: ensure_guest_user | tr: kayıt öncesi fk icin placeholder kullanıcı / en: placeholder user row before signup for fks
def ensure_guest_user(db: Session, user_id: int) -> None:
    if db.query(User).filter(User.id == int(user_id)).first():
        return
    email = f"studyflow_guest_{int(user_id)}@internal.local"
    if db.query(User).filter(User.email == email).first():
        return
    placeholder_pw = auth_service.hash_password(os.urandom(24).hex())
    guest = User(
        id=int(user_id),
        email=email,
        hashed_password=placeholder_pw,
        full_name="Guest",
    )
    db.add(guest)
    db.commit()


# fn: get_or_create_profile | tr: profil yoksa olustur (xp, locale) / en: get or create user profile
def get_or_create_profile(db: Session, user_id: int) -> UserProfile:
    ensure_guest_user(db, user_id)
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile:
        return profile
    profile = UserProfile(user_id=user_id, xp=0, locale="en")
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


# fn: _badge_slug_to_id | tr: rozet slug -> id / en: badge slug to id
def _badge_slug_to_id(db: Session, slug: str) -> Optional[int]:
    b = db.query(Badge).filter(Badge.slug == slug).first()
    return b.id if b else None


# fn: award_badge_if_missing | tr: rozet yoksa kullanıcıya ver / en: award badge if user does not have it
def award_badge_if_missing(db: Session, user_id: int, slug: str) -> None:
    ensure_badges_seeded(db)
    badge_id = _badge_slug_to_id(db, slug)
    if badge_id is None:
        return
    exists = (
        db.query(UserBadge)
        .filter(UserBadge.user_id == user_id, UserBadge.badge_id == badge_id)
        .first()
    )
    if exists:
        return
    db.add(UserBadge(user_id=user_id, badge_id=badge_id))
    db.commit()


# fn: add_xp | tr: xp ekle / en: add xp points
def add_xp(db: Session, user_id: int, delta: int) -> None:
    if delta <= 0:
        return
    profile = get_or_create_profile(db, user_id)
    profile.xp = int(profile.xp or 0) + delta
    db.commit()


# fn: _award_if | tr: koşul true ise rozet ver / en: award badge if condition true
def _award_if(db: Session, user_id: int, slug: str, cond: bool) -> None:
    if cond:
        award_badge_if_missing(db, user_id, slug)


# fn: _has_weak_topic_comeback | tr: zayıf konuda iyilesme var mi / en: improved on a previously weak topic
def _has_weak_topic_comeback(db: Session, user_id: int) -> bool:
    rows: List[Tuple[str, float]] = (
        db.query(QuizAttemptTopicResult.topic, QuizAttemptTopicResult.success_rate)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptTopicResult.quiz_attempt_id)
        .filter(QuizAttempt.user_id == int(user_id))
        .order_by(QuizAttemptTopicResult.topic.asc(), QuizAttempt.created_at.asc(), QuizAttempt.id.asc())
        .all()
    )
    per_topic: Dict[str, List[float]] = {}
    for topic, rate in rows:
        t = str(topic or "").strip()
        if not t:
            continue
        per_topic.setdefault(t, []).append(float(rate or 0.0))
    for rates in per_topic.values():
        if len(rates) < 2:
            continue
        first = rates[0]
        last = rates[-1]
        if first < 0.6 and last >= (first + 0.2):
            return True
    return False


# fn: sync_dynamic_badges | tr: quiz/pomodoro/plan verisine göre rozetleri güncelle / en: sync badges from activity data
def sync_dynamic_badges(db: Session, user_id: int) -> None:
    ensure_badges_seeded(db)
    uid = int(user_id)

    attempt_count = int(db.query(QuizAttempt).filter(QuizAttempt.user_id == uid).count() or 0)
    pomodoro_count = int(
        db.query(PomodoroSession)
        .filter(PomodoroSession.user_id == uid, PomodoroSession.status == "completed")
        .count()
        or 0
    )
    best_score = db.query(func.max(QuizAttempt.score_percentage)).filter(QuizAttempt.user_id == uid).scalar()
    latest_two = (
        db.query(QuizAttempt.score_percentage)
        .filter(QuizAttempt.user_id == uid)
        .order_by(desc(QuizAttempt.created_at), desc(QuizAttempt.id))
        .limit(2)
        .all()
    )
    latest_six = (
        db.query(QuizAttempt.score_percentage)
        .filter(QuizAttempt.user_id == uid)
        .order_by(desc(QuizAttempt.created_at), desc(QuizAttempt.id))
        .limit(6)
        .all()
    )
    quiz_days = (
        db.query(func.count(func.distinct(func.date(QuizAttempt.created_at))))
        .filter(QuizAttempt.user_id == uid)
        .scalar()
        or 0
    )
    has_plan = (
        db.query(AcademicStudyPlan.id).filter(AcademicStudyPlan.user_id == uid).first()
        is not None
    )

    improved_last_attempt = False
    if len(latest_two) == 2:
        cur = float(latest_two[0][0] or 0.0)
        prev = float(latest_two[1][0] or 0.0)
        improved_last_attempt = cur > prev

    smart_improver = False
    if len(latest_six) >= 4:
        recent = [float(x[0] or 0.0) for x in latest_six[:3]]
        older = [float(x[0] or 0.0) for x in latest_six[3:6]]
        if older:
            smart_improver = (sum(recent) / float(len(recent))) >= (sum(older) / float(len(older))) + 5.0

    _award_if(db, uid, "first_quiz", attempt_count >= 1)
    _award_if(db, uid, "quiz_rookie", attempt_count >= 5)
    _award_if(db, uid, "quiz_marathon", attempt_count >= 20)

    _award_if(db, uid, "focus", pomodoro_count >= 1)
    _award_if(db, uid, "deep_focus", pomodoro_count >= 5)
    _award_if(db, uid, "focus_legend", pomodoro_count >= 25)

    _award_if(db, uid, "sharp_mind", best_score is not None and float(best_score or 0.0) >= 90.0)
    _award_if(db, uid, "comeback", improved_last_attempt)
    _award_if(db, uid, "weak_topic_crusher", _has_weak_topic_comeback(db, uid))
    _award_if(db, uid, "consistent_learner", int(quiz_days) >= 3)
    _award_if(db, uid, "smart_improver", smart_improver)
    _award_if(db, uid, "planner", has_plan)


# fn: build_profile_payload | tr: api için xp + locale + rozet listesi / en: xp + locale + badges for api response
def build_profile_payload(db: Session, user_id: int) -> Dict[str, Any]:
    ensure_badges_seeded(db)
    sync_dynamic_badges(db, user_id)
    profile = get_or_create_profile(db, user_id)
    earned_ids = {
        ub.badge_id
        for ub in db.query(UserBadge).filter(UserBadge.user_id == user_id).all()
    }
    badges_out: List[Dict[str, Any]] = []
    for b in db.query(Badge).order_by(Badge.id).all():
        badges_out.append(
            {
                "slug": b.slug,
                "title": b.title,
                "description": b.description,
                "earned": b.id in earned_ids,
            }
        )
    return {
        "xp": int(profile.xp or 0),
        "locale": profile.locale or "en",
        "badges": badges_out,
    }


# fn: static_profile_fallback | tr: db yokken boş profil / en: empty profile when db unavailable
def static_profile_fallback() -> Dict[str, Any]:
    badges_out = [
        {**row, "earned": False} for row in BADGE_SEED
    ]
    return {"xp": 0, "locale": "en", "badges": badges_out}


# fn: apply_after_quiz_saved | tr: quiz sonrası +15 xp, rozet kontrol / en: after quiz +15 xp, sync badges
def apply_after_quiz_saved(db: Session, user_id: int) -> None:
    add_xp(db, user_id, 15)
    sync_dynamic_badges(db, user_id)


# fn: apply_after_pdf_upload | tr: pdf yükleme +5 xp, first_pdf rozeti / en: after pdf upload +5 xp, first_pdf badge
def apply_after_pdf_upload(db: Session, user_id: int) -> None:
    add_xp(db, user_id, 5)
    award_badge_if_missing(db, user_id, "first_pdf")


# fn: apply_after_pomodoro_completed | tr: pomodoro bitince +10 xp, rozet kontrol / en: after pomodoro +10 xp, sync badges
def apply_after_pomodoro_completed(db: Session, user_id: int) -> None:
    add_xp(db, user_id, 10)
    sync_dynamic_badges(db, user_id)

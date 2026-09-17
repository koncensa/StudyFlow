# route: profile_routes | tr: kullanıcı oyunlaştırma profili (xp, rozet, dil) http endpoint'leri — main.py'de /study prefix ile bağlanır / en: gamification profile xp badges locale endpoints mounted at /study prefix in main.py

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.deps.auth import require_user_id
from app.schemas.profile_schema import ProfileLocaleUpdate, ProfileResponse
from app.services import profile_service

router = APIRouter()


# fn: get_profile | tr: GET /study/profile — xp, rozetler ve arayüz dili / en: GET xp badges and ui locale
@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> ProfileResponse:
    uid = require_user_id(authorization)
    if db is None:
        data = profile_service.static_profile_fallback()
        return ProfileResponse(**data)
    data = profile_service.build_profile_payload(db, uid)
    return ProfileResponse(**data)


# fn: patch_locale | tr: PATCH /study/profile/locale — arayüz dilini güncelle (tr/en) / en: PATCH update ui locale tr or en
@router.patch("/profile/locale", response_model=ProfileResponse)
def patch_locale(
    body: ProfileLocaleUpdate,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> ProfileResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    profile = profile_service.get_or_create_profile(db, uid)
    profile.locale = body.locale.strip().lower()[:10] or "en"
    db.commit()
    data = profile_service.build_profile_payload(db, uid)
    return ProfileResponse(**data)

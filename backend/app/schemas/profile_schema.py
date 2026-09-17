# svc: profile_schema | tr: kullanıcı profil (xp, rozet, dil) api pydantic şemaları / en: pydantic schemas for user profile xp badges locale api

from typing import List, Optional

from pydantic import BaseModel, Field


# schema: BadgeItem | tr: tek rozet kartı (slug, başlık, kazanıldı mı) / en: single badge card slug title earned flag
class BadgeItem(BaseModel):
    slug: str
    title: str
    description: str
    earned: bool = False


# schema: ProfileResponse | tr: profil özeti yanıtı (xp, dil, rozet listesi) / en: profile summary response xp locale badges
class ProfileResponse(BaseModel):
    xp: int = 0
    locale: str = Field(default="en", max_length=10)
    badges: List[BadgeItem] = Field(default_factory=list)


# schema: ProfileLocaleUpdate | tr: arayüz dili güncelleme isteği / en: ui locale update request
class ProfileLocaleUpdate(BaseModel):
    locale: str = Field(..., max_length=10)
    user_id: Optional[int] = Field(
        default=None,
        description="Deprecated; ignored. User is taken from the Bearer token.",
    )

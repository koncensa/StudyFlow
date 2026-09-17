# dep: auth | tr: jwt token çözme ve route'larda kullanıcı kimliği doğrulama yardımcıları / en: jwt decode and user identity verification helpers for routes

import os
from typing import Optional

from fastapi import HTTPException
from jose import JWTError, jwt

# cfg: JWT_SECRET | tr: jwt imza anahtarı (prod'da env'den uzun secret) / en: jwt signing secret use long env secret in production
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-me-use-a-long-secret-in-production")
# cfg: JWT_ALGORITHM | tr: jwt imza algoritması / en: jwt signing algorithm
JWT_ALGORITHM = "HS256"


# fn: decode_token_user_id | tr: Authorization Bearer header'dan user_id çıkar; geçersizse None / en: extract user_id from Bearer header or None if invalid
def decode_token_user_id(authorization: Optional[str]) -> Optional[int]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (JWTError, ValueError, TypeError):
        return None


# fn: require_user_id | tr: geçerli jwt yoksa 401 fırlat, varsa user_id döndür / en: raise 401 without valid jwt else return user_id
def require_user_id(authorization: Optional[str]) -> int:
    uid = decode_token_user_id(authorization)
    if uid is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please log in.",
        )
    return uid


# fn: resolve_user_id | tr: jwt varsa onu kullan; yoksa claimed/default (legacy misafir) / en: prefer jwt subject else claimed or default legacy guest
def resolve_user_id(authorization: Optional[str], claimed: Optional[int], *, default: int = 1) -> int:
    tid = decode_token_user_id(authorization)
    if tid is not None:
        if claimed is not None and int(claimed) != tid:
            raise HTTPException(
                status_code=403,
                detail="Bearer token does not match the requested user_id.",
            )
        return tid
    if claimed is not None:
        return int(claimed)
    return default


# fn: require_user_id_matching | tr: jwt zorunlu + body/query user_id token ile eşleşmeli / en: jwt required and body query user_id must match token
def require_user_id_matching(
    authorization: Optional[str], claimed: Optional[int]
) -> int:
    uid = require_user_id(authorization)
    if claimed is not None and int(claimed) != uid:
        raise HTTPException(
            status_code=403,
            detail="Bearer token does not match the requested user_id.",
        )
    return uid

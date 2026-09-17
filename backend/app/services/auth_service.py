# svc: auth | tr: şifre hash, doğrulama, jwt token / en: password hash, verify, jwt token

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt

from app.deps.auth import JWT_ALGORITHM, JWT_SECRET

# cfg: token süresi 7 gün  / en: access token expires in 7 days
ACCESS_TOKEN_EXPIRE_DAYS = 7

# cfg: bcrypt max 72 byte şifre / en: bcrypt only uses first 72 bytes of password
_BCRYPT_MAX = 72


# fn: _password_bytes | tr: sifreyi byte'a cevir, 72 byte kes / en: encode password, truncate to 72 bytes
def _password_bytes(plain: str) -> bytes:
    raw = plain.encode("utf-8")
    return raw[:_BCRYPT_MAX] if len(raw) > _BCRYPT_MAX else raw


# fn: hash_password | tr: kayıt/şifre degiştirmede şifreyi hash'le / en: hash plain password for storage
def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_password_bytes(plain), salt).decode("ascii")


# fn: verify_password | tr: giriş/şifre degiştirmede şifre doğrula / en: verify plain password against hash
def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(_password_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# fn: create_access_token | tr: login/kayıt sonrası jwt üret / en: create jwt after login or register
def create_access_token(*, user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

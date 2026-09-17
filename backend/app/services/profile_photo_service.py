# svc: profile_photo | tr: profil fotoğrafı yükle, doğrula, sil / en: upload, validate, delete profile photos

import re
import uuid
from pathlib import Path
from typing import Optional, Tuple

# cfg: backend/uploads/profile_photos altında saklanır / en: stored under backend/uploads/profile_photos
PHOTO_SUBDIR = "profile_photos"
# cfg: max 2mb / en: max file size 2mb
_MAX_BYTES = 2 * 1024 * 1024

_ALLOWED_EXT = {".jpg", ".jpeg", ".png"}


# fn: _backend_root | tr: backend klasör yolu / en: backend directory path
def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# fn: _legacy_app_root | tr: eski upload yolu (uyumluluk) / en: legacy upload path for compatibility
def _legacy_app_root() -> Path:
    return Path(__file__).resolve().parent.parent


# fn: upload_base_dir | tr: fotoğrafların kaydedilecegi klasör / en: directory where photos are saved
def upload_base_dir() -> Path:
    return _backend_root() / "uploads" / PHOTO_SUBDIR


# fn: public_url_path | tr: db yolunu /uploads/... url'e cevir / en: db path to public /uploads/ url
def public_url_path(stored: str) -> str:
    if not stored:
        return ""
    s = str(stored).replace("\\", "/").lstrip("/")
    if s.startswith("uploads/"):
        s = s[len("uploads/") :]
    return f"/uploads/{s}"


# fn: delete_stored_file | tr: diskten eski fotografı silme / en: delete old photo from disk
def delete_stored_file(stored_path: Optional[str]) -> None:
    if not stored_path:
        return
    s = str(stored_path).replace("\\", "/").lstrip("/")
    if s.startswith("uploads/"):
        s = s[len("uploads/") :]
    primary = _backend_root() / "uploads" / s
    legacy = _legacy_app_root() / "uploads" / s
    for full in (primary, legacy):
        try:
            if full.is_file():
                full.unlink()
                return
        except OSError:
            pass


# fn: _sniff_is_jpeg_or_png | tr: dosya içeriğinden gerçek jpeg/png mi / en: sniff bytes for real jpeg/png
def _sniff_is_jpeg_or_png(data: bytes) -> bool:
    if len(data) < 8:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    return False


# fn: validate_image_file | tr: boyut, uzanti ve icerik dogrula / en: validate size, extension, content
def validate_image_file(*, original_filename: str, content: bytes) -> Tuple[bool, str]:
    if not content or len(content) > _MAX_BYTES:
        return False, "File must be 2MB or smaller."
    name = (original_filename or "").lower().strip()
    if "." not in name:
        return False, "Use JPG, JPEG, or PNG."
    ext = "." + name.rsplit(".", 1)[-1]
    if ext not in _ALLOWED_EXT:
        return False, "Use JPG, JPEG, or PNG."
    if not _sniff_is_jpeg_or_png(content):
        return False, "Invalid image file. Use JPG, JPEG, or PNG only."
    return True, ""


# fn: save_new_profile_image | tr: yeni fotografı diske kaydet, db yolu dön / en: save new image to disk, return db path
def save_new_profile_image(*, user_id: int, content: bytes, ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    safe = re.sub(r"[^a-z0-9]", "", ext)
    if safe not in ("jpg", "png"):
        safe = "jpg"
    d = upload_base_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"u{user_id}_{uuid.uuid4().hex}.{safe}"
    full = d / name
    with open(full, "wb") as f:
        f.write(content)
    return f"{PHOTO_SUBDIR}/{name}"

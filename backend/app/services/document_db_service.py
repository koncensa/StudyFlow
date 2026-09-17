# svc: document_db | tr: pdf oturumunu, pdften çıkarılan metni ve oturum bilgisini mysql'de kaydet, pk bul, ram'den geri yukle / en: persist pdf session in mysql, resolve pk, restore to ram

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from app.models.document_model import Document
from app.services import profile_service

if TYPE_CHECKING:
    from app.services.document_store import StoredDocument


# fn: register_document | tr: pdf yüklenince documents tablosuna kaydet/güncelle / en: insert or update documents row on pdf upload
def register_document(
    db: Session,
    user_id: int,
    session_key: str,
    title: Optional[str] = None,
    study_text: Optional[str] = None,
) -> int:
    profile_service.ensure_guest_user(db, user_id)
    sk = (session_key or "").strip()
    if not sk:
        raise ValueError("session_key required")
    text_blob: Optional[str] = None
    if study_text is not None:
        st = str(study_text).strip()
        if st:
            # tr: metin max 15mb / en: study text capped at 15mb
            text_blob = st[:15_000_000]

    row = db.query(Document).filter(Document.session_key == sk).first()
    if row:
        # tr: varsa başlık/metin güncelle / en: update title/text if row exists
        if title and not row.title:
            row.title = title[:255]
        if text_blob is not None:
            row.study_text = text_blob
        db.commit()
        db.refresh(row)
        return int(row.id)
    t = (title or "").strip()[:255] if title else None
    d = Document(user_id=int(user_id), session_key=sk, title=t or None, study_text=text_blob)
    db.add(d)
    db.commit()
    db.refresh(d)
    return int(d.id)


# fn: try_restore_document_to_store | tr: ram'de yoksa mysql'den pdf oturumunu geri yükle / en: restore pdf session from mysql if not in ram
def try_restore_document_to_store(db: Session, session_key: str, user_id: int) -> Optional["StoredDocument"]:
    from app.services.document_store import get_document, restore_document_session

    sk = (session_key or "").strip()
    if not sk:
        return None
    mem = get_document(sk)
    if mem is not None:
        # tr: ram'de var ama baska kullanıcıya aitse reddet / en: reject if ram copy belongs to another user
        return mem if mem.user_id == int(user_id) else None
    row = (
        db.query(Document)
        .filter(Document.session_key == sk, Document.user_id == int(user_id))
        .first()
    )
    if not row:
        return None
    body = getattr(row, "study_text", None)
    if not body or not str(body).strip():
        return None
    try:
        return restore_document_session(
            sk,
            int(user_id),
            str(body).strip(),
            ((row.title or "").strip() or "document.pdf")[:255],
        )
    except ValueError:
        return None


# fn: resolve_document_pk | tr: session_key -> documents.id (chat/quiz fk icin) / en: session_key to documents.id for chat/quiz fk
def resolve_document_pk(db: Session, user_id: int, session_key: str) -> Optional[int]:
    sk = (session_key or "").strip()
    if not sk:
        return None
    row = (
        db.query(Document)
        .filter(Document.session_key == sk, Document.user_id == int(user_id))
        .first()
    )
    return int(row.id) if row else None

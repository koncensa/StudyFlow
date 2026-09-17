# svc: document_summary_history | tr: üretilen pdf özetlerini mysql'de sakla ve listele / en: save and list generated pdf summaries in mysql

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.document_model import Document
from app.models.document_summary_model import DocumentSummaryHistory


# fn: save_summary_history | tr: yeni özeti document_summary_history tablosuna kaydet / en: save new summary to document_summary_history table
def save_summary_history(
    db: Session,
    *,
    user_id: int,
    session_key: str,
    mode: str,
    level: str,
    output_locale: str,
    content: str,
) -> Optional[DocumentSummaryHistory]:
    sk = (session_key or "").strip()
    body = (content or "").strip()
    if not sk or not body:
        return None
    # tr: session_key -> documents satırı bul / en: resolve session_key to documents row
    doc = (
        db.query(Document)
        .filter(Document.session_key == sk, Document.user_id == int(user_id))
        .first()
    )
    if not doc:
        return None
    row = DocumentSummaryHistory(
        user_id=int(user_id),
        document_id=int(doc.id),
        mode=(mode or "summary").strip().lower()[:24],
        level=(level or "normal").strip().lower()[:24],
        output_locale=(output_locale or "en").strip().lower()[:10],
        content=body[:20000],  # tr: max 20k karakter / en: max 20k chars
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# fn: list_summary_history | tr: bir pdf için geçmiş özetleri listele / en: list past summaries for one pdf
def list_summary_history(
    db: Session,
    *,
    user_id: int,
    session_key: str,
    limit: int = 24,
) -> List[DocumentSummaryHistory]:
    sk = (session_key or "").strip()
    if not sk:
        return []
    doc = (
        db.query(Document)
        .filter(Document.session_key == sk, Document.user_id == int(user_id))
        .first()
    )
    if not doc:
        return []
    cap = max(1, min(int(limit or 24), 100))
    return (
        db.query(DocumentSummaryHistory)
        .filter(
            DocumentSummaryHistory.user_id == int(user_id),
            DocumentSummaryHistory.document_id == int(doc.id),
        )
        .order_by(DocumentSummaryHistory.created_at.desc(), DocumentSummaryHistory.id.desc())
        .limit(cap)
        .all()
    )

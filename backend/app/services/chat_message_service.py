# svc: chat_message | tr: pdf tutor/ chatbot sohbetini mysql'de sakla ve listele / en: persist and list pdf tutor chat in mysql

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional

from sqlalchemy.orm import Session

from app.models.document_model import Document
from app.models.message_model import ChatMessage
from app.services.document_db_service import resolve_document_pk

# type: sohbet rolü user veya assistant / en: chat role user or assistant
ChatRole = Literal["user", "assistant"]


# cls: PdfChatHistoryMessage | tr: tek mesaj (rol + icerik) / en: single message role + content
@dataclass
class PdfChatHistoryMessage:
    role: ChatRole
    content: str


# cls: PdfSessionHistoryItem | tr: bir pdf oturumunun tüm sohbet gecmişi / en: full chat history for one pdf session
@dataclass
class PdfSessionHistoryItem:
    key: str
    document_id: str
    filename: str
    messages: List[PdfChatHistoryMessage]
    updated_at: datetime


# fn: _normalize_sender | tr: db sender değerini user/assistant yap / en: normalize db sender to user/assistant
def _normalize_sender(role: str) -> Optional[ChatRole]:
    r = (role or "").strip().lower()
    if r in ("user", "assistant"):
        return r  # type: ignore[return-value]
    if r == "human":
        return "user"
    return None


# fn: append_chat_turn | tr: kullanıcı + asistan mesajını db'ye kaydet / en: save user + assistant turn to db
def append_chat_turn(
    db: Session,
    *,
    user_id: int,
    session_key: str,
    user_content: str,
    assistant_content: str,
    mode: str = "tutor_chat",
) -> None:
    doc_pk = resolve_document_pk(db, int(user_id), session_key)
    if doc_pk is None:
        return
    uid = int(user_id)
    did = int(doc_pk)
    mode_val = (mode or "tutor_chat").strip()[:20] or "tutor_chat"
    user_text = (user_content or "").strip()
    assistant_text = (assistant_content or "").strip()
    if not user_text or not assistant_text:
        return
    db.add(
        ChatMessage(
            user_id=uid,
            document_id=did,
            sender="user",
            mode=mode_val,
            content=user_text[:32000],
        )
    )
    db.add(
        ChatMessage(
            user_id=uid,
            document_id=did,
            sender="assistant",
            mode=mode_val,
            content=assistant_text[:32000],
        )
    )
    db.commit()


# fn: list_user_pdf_sessions | tr: kullanıcının pdf sohbet oturumlarını listele / en: list user's pdf chat sessions
def list_user_pdf_sessions(
    db: Session,
    user_id: int,
    *,
    limit: int = 32,
) -> List[PdfSessionHistoryItem]:
    uid = int(user_id)
    cap = max(1, min(int(limit or 32), 64))

    # tr: kullanıcının son pdf dökümanlarını al / en: fetch user's recent pdf documents
    doc_rows = (
        db.query(Document)
        .filter(Document.user_id == uid)
        .order_by(Document.created_at.desc())
        .limit(cap * 4)
        .all()
    )
    if not doc_rows:
        return []

    doc_by_pk = {int(d.id): d for d in doc_rows}
    doc_pks = list(doc_by_pk.keys())
    if not doc_pks:
        return []

    # tr: bu dökümanlara ait tüm mesajları al / en: fetch all messages for those documents
    msg_rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == uid, ChatMessage.document_id.in_(doc_pks))
        .order_by(ChatMessage.document_id.asc(), ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    if not msg_rows:
        return []

    # tr: mesajları dökuüan id'sine göre grupla / en: group messages by document id
    grouped: dict[int, List[ChatMessage]] = {}
    for m in msg_rows:
        did = int(m.document_id)
        grouped.setdefault(did, []).append(m)

    items: List[PdfSessionHistoryItem] = []
    for doc_pk, msgs in grouped.items():
        doc = doc_by_pk.get(doc_pk)
        if not doc:
            continue
        session_key = (doc.session_key or "").strip()
        if not session_key:
            continue
        history: List[PdfChatHistoryMessage] = []
        for m in msgs:
            role = _normalize_sender(str(m.sender or ""))
            body = (m.content or "").strip()
            if not role or not body:
                continue
            history.append(PdfChatHistoryMessage(role=role, content=body))
        if not history:
            continue
        last_at = msgs[-1].created_at or datetime.utcnow()
        title = (doc.title or "").strip() or "document.pdf"
        items.append(
            PdfSessionHistoryItem(
                key=session_key,
                document_id=session_key,
                filename=title[:255],
                messages=history,
                updated_at=last_at,
            )
        )

    # tr: en son güncellenen oturum önce / en: most recently updated session first
    items.sort(key=lambda x: x.updated_at, reverse=True)
    return items[:cap]


# fn: delete_messages_for_user | tr: kullanıcının tüm sohbet mesajlarını sil / en: delete all chat messages for user
def delete_messages_for_user(db: Session, user_id: int) -> None:
    db.query(ChatMessage).filter(ChatMessage.user_id == int(user_id)).delete(
        synchronize_session=False
    )

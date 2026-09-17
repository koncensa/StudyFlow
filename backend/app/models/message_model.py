# model: message_model | tr: pdf tutor sohbet mesajları mysql tablo tanımı / en: pdf tutor chat messages mysql table definition

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.connection import Base


# model: ChatMessage | tr: tek sohbet satırı — user veya assistant mesajı / en: single chat row user or assistant message
class ChatMessage(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # tr: documents.id — hangi pdf oturumuna ait / en: documents.id which pdf session this message belongs to
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # tr: gönderen: user veya assistant / en: sender user or assistant
    sender = Column(String(20), nullable=False)
    # tr: çalışma modu: tutor_chat, quiz_coach vb. / en: study mode tutor_chat quiz_coach etc
    mode = Column(String(20), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# model: recommendation_model | tr: pdf/konu bazlı kalıcı öneri kaydı mysql tablo tanımı (şema hazır; aktif yazma az) / en: persistent pdf topic recommendation mysql table schema ready active writes minimal

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.connection import Base


# model: Recommendation | tr: tek öneri satırı — kullanıcı, pdf, konu, tür, metin / en: single recommendation row user pdf topic type content
class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # tr: documents.id — hangi pdf belgesine ait / en: documents.id which pdf document
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic = Column(String(255), nullable=False)
    # tr: öneri türü (db sütunu: type) örn. review, resource, mini_quiz / en: recommendation kind db column type e.g. review resource mini_quiz
    rec_type = Column("type", String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

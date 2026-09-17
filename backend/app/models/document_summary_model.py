# model: document_summary_model | tr: pdf için üretilmiş özet/explain geçmişi mysql tablo tanımı / en: generated pdf summary explain history mysql table definition

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.connection import Base


# model: DocumentSummaryHistory | tr: tek özet kaydı — hangi pdf, hangi mod, metin içeriği / en: single summary record which pdf mode and text content
class DocumentSummaryHistory(Base):
    __tablename__ = "document_summary_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # tr: documents.id — hangi pdf belgesine ait / en: documents.id which pdf document this summary belongs to
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # tr: çalışma modu: summary, explain_simple, exam_focus vb. / en: study mode summary explain_simple exam_focus etc
    mode = Column(String(24), nullable=False, index=True)
    # tr: explain seviyesi: beginner, normal, technical / en: explain level beginner normal technical
    level = Column(String(24), nullable=False, index=True)
    output_locale = Column(String(10), nullable=False, default="en")
    # tr: üretilen özet/explain metni (max ~20k service tarafında) / en: generated summary explain text capped ~20k in service
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

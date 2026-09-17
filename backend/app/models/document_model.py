# model: document_model | tr: yüklenen pdf oturumu mysql kalıcı kaydı (session_key = api document_id) / en: uploaded pdf session persistent mysql row session_key equals api document_id

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.database.connection import Base


# model: Document | tr: kullanıcının pdf belgesi — başlık, oturum anahtarı, normalize metin / en: user pdf document title session key normalized text
class Document(Base):
    """Persisted PDF session row; session_key is the API document_id string."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # tr: belge sahibi — users.id ile bağlı / en: document owner linked to users.id
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    # tr: api'deki document_id ile aynı benzersiz oturum anahtarı / en: unique session key same as api document_id
    session_key = Column(String(128), nullable=False, unique=True, index=True)
    # tr: normalize pdf metni — sunucu restart sonrası bellek oturumunu geri yüklemek için / en: normalized pdf text to rehydrate in-memory session after restart
    study_text = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# model: badge_model | tr: oyunlaştırma rozetleri mysql tablo tanımları (katalog + kullanıcı kazanımları) / en: gamification badges mysql table definitions catalog plus user earn records

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database.connection import Base


# model: Badge | tr: rozet kataloğu — slug, başlık, açıklama (tüm kullanıcılar için tanım) / en: badge catalog slug title description shared definition
class Badge(Base):
    __tablename__ = "badges"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # tr: benzersiz kod (db sütunu: code) örn. first_pdf, quiz_rookie / en: unique code db column code e.g. first_pdf quiz_rookie
    slug = Column("code", String(50), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)


# model: UserBadge | tr: kullanıcının kazandığı rozet kaydı (kim, hangi rozet, ne zaman) / en: user earned badge record who which badge when
class UserBadge(Base):
    __tablename__ = "user_badges"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    badge_id = Column(Integer, ForeignKey("badges.id"), nullable=False, index=True)
    earned_at = Column(DateTime, nullable=False, default=datetime.utcnow)

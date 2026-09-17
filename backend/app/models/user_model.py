# model: user_model | tr: kayıtlı kullanıcı hesabı mysql tablo tanımı (giriş kimliği) / en: registered user account mysql table definition login identity

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.database.connection import Base


# model: User | tr: tek kullanıcı hesabı — email, şifre hash, görünen ad / en: single user account email password hash display name
class User(Base):
    """Registered account; id aligns with profiles.user_id and related tables."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # tr: bcrypt hash — db sütunu password_hash / en: bcrypt hash db column password_hash
    hashed_password = Column("password_hash", String(255), nullable=False)
    # tr: görünen ad (auth /me username alanı) / en: display name used as username in auth me
    full_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# model: user_profile_model | tr: kullanıcı oyunlaştırma profili mysql tablo tanımı (xp, dil, bio, foto) / en: user gamification profile mysql table xp locale bio photo

from sqlalchemy import Column, Integer, String, Text

from app.database.connection import Base


# model: UserProfile | tr: users.id ile 1:1 profil — xp, arayüz dili, biyografi, foto yolu / en: one-to-one profile per users.id xp locale bio photo path
class UserProfile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # tr: users.id ile eşleşir (her kullanıcıda tek profil) / en: matches users.id one profile per user
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    xp = Column(Integer, nullable=False, default=0)
    # tr: arayüz dili tr/en — db sütunu language_code / en: ui locale tr or en db column language_code
    locale = Column("language_code", String(10), nullable=False, default="en")
    bio = Column(Text, nullable=True)
    # tr: backend/uploads/ altında göreli yol örn. profile_photos/u1_abc.jpg / en: relative path under backend/uploads e.g. profile_photos/u1_abc.jpg
    profile_photo_path = Column(String(512), nullable=True)

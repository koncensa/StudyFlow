# svc: auth_schema | tr: kimlik doğrulama api istek/yanıt pydantic şemaları / en: pydantic request/response schemas for authentication api

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# schema: RegisterRequest | tr: yeni kullanıcı kayıt isteği (email, şifre, ad) / en: new user registration request email password name
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: Optional[str] = Field(None, max_length=120)


# schema: LoginRequest | tr: giriş isteği (email + şifre) / en: login request email and password
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


# schema: TokenResponse | tr: başarılı giriş/kayıt jwt yanıtı / en: successful login/register jwt response
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    username: Optional[str] = None


# schema: MeResponse | tr: oturum açmış kullanıcı profil özeti / en: logged-in user profile summary
class MeResponse(BaseModel):
    user_id: int
    email: str
    username: Optional[str] = None
    biography: Optional[str] = None
    profile_photo_url: Optional[str] = None


# schema: UpdateMeRequest | tr: profil güncelleme isteği (görünen ad, biyografi) / en: profile update request display name bio
class UpdateMeRequest(BaseModel):
    """Display name + profile bio updates."""

    username: Optional[str] = Field(default=None, max_length=120)
    biography: Optional[str] = Field(default=None, max_length=1000)


# schema: ChangePasswordRequest | tr: şifre değiştirme isteği (mevcut + yeni + onay) / en: change password request current new confirm
class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)
    confirm_new_password: str = Field(..., min_length=8, max_length=200)

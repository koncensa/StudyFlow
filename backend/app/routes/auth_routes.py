# route: auth_routes | tr: kimlik doğrulama ve hesap yönetimi http endpoint'leri — main.py'de /auth prefix ile bağlanır / en: auth and account management http endpoints mounted at /auth prefix in main.py

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.deps.auth import decode_token_user_id, require_user_id
from app.models.academic_plan_model import AcademicStudyPlan
from app.models.badge_model import UserBadge
from app.models.document_model import Document
from app.models.document_summary_model import DocumentSummaryHistory
from app.models.message_model import ChatMessage
from app.models.pomodoro_model import PomodoroSession
from app.models.quiz import QuizAttempt, QuizAttemptQuestionResult, QuizAttemptTopicResult
from app.models.recommendation_model import Recommendation
from app.models.user_model import User
from app.models.user_profile_model import UserProfile
from app.schemas.auth_schema import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UpdateMeRequest,
)
from app.services import auth_service
from app.services import profile_service
from app.services import profile_photo_service

router = APIRouter()


# fn: _me_payload_from_user_and_profile | tr: User + UserProfile -> MeResponse json yükü / en: build MeResponse from user and profile rows
def _me_payload_from_user_and_profile(
    user: User, profile: Optional[UserProfile]
) -> MeResponse:
    photo: Optional[str] = None
    if profile and getattr(profile, "profile_photo_path", None):
        photo = profile_photo_service.public_url_path(profile.profile_photo_path)
    return MeResponse(
        user_id=int(user.id),
        email=str(user.email),
        username=(str(user.full_name).strip() if user.full_name else None) or None,
        biography=(str(profile.bio).strip() if profile and profile.bio else None) or None,
        profile_photo_url=photo,
    )


# fn: _normalize_email | tr: email küçük harf ve trim / en: lowercase and trim email
def _normalize_email(email: str) -> str:
    return email.strip().lower()


# fn: _normalized_username | tr: görünen adı trim et, boşsa None / en: trim display name empty to None
def _normalized_username(full_name: Optional[str]) -> Optional[str]:
    return (str(full_name).strip() if full_name else None) or None


# fn: register | tr: POST /auth/register — yeni hesap oluştur, jwt döndür / en: POST create account and return jwt
@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Optional[Session] = Depends(get_db)) -> TokenResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")

    email = _normalize_email(str(body.email))
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    fn = (body.full_name or "").strip() or None
    user = User(
        email=email,
        hashed_password=auth_service.hash_password(body.password),
        full_name=fn,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    profile_service.get_or_create_profile(db, user.id)

    token = auth_service.create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        username=_normalized_username(user.full_name),
    )


# fn: login | tr: POST /auth/login — email/şifre doğrula, jwt döndür / en: POST verify credentials and return jwt
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Optional[Session] = Depends(get_db)) -> TokenResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")

    email = _normalize_email(str(body.email))
    user = db.query(User).filter(User.email == email).first()
    if not user or not auth_service.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = auth_service.create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        username=_normalized_username(user.full_name),
    )


# fn: me | tr: GET /auth/me — oturum açmış kullanıcı bilgisi / en: GET current authenticated user profile
@router.get("/me", response_model=MeResponse)
def me(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> MeResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = decode_token_user_id(authorization)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    prof = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
    if prof is None:
        prof = profile_service.get_or_create_profile(db, uid)
    return _me_payload_from_user_and_profile(user, prof)


# fn: update_me | tr: PATCH /auth/me — görünen ad ve biyografi güncelle / en: PATCH update display name and biography
@router.patch("/me", response_model=MeResponse)
def update_me(
    body: UpdateMeRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> MeResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    prof = profile_service.get_or_create_profile(db, uid)
    name = (body.username if body.username is not None else "").strip() or None
    if name is not None and len(name) > 120:
        raise HTTPException(status_code=400, detail="Username is too long.")
    biography = (body.biography if body.biography is not None else "").strip() or None
    if biography is not None and len(biography) > 1000:
        raise HTTPException(status_code=400, detail="Biography is too long.")
    user.full_name = name
    prof.bio = biography
    db.add(user)
    db.add(prof)
    db.commit()
    db.refresh(user)
    db.refresh(prof)
    return _me_payload_from_user_and_profile(user, prof)


# fn: change_password | tr: POST /auth/me/password — mevcut şifre ile yeni şifre belirle / en: POST change password with current password check
@router.post("/me/password", response_model=MeResponse)
def change_password(
    body: ChangePasswordRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> MeResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    if body.new_password != body.confirm_new_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    if not auth_service.verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current password.")
    user.hashed_password = auth_service.hash_password(body.new_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    prof = profile_service.get_or_create_profile(db, uid)
    return _me_payload_from_user_and_profile(user, prof)


# fn: upload_profile_photo | tr: POST /auth/me/profile-photo — profil fotoğrafı yükle / en: POST upload profile photo image file
@router.post("/me/profile-photo", response_model=MeResponse)
async def upload_profile_photo(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
    file: UploadFile = File(...),
) -> MeResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    content = await file.read()
    fn = file.filename or "photo"
    valid, err = profile_photo_service.validate_image_file(original_filename=fn, content=content)
    if not valid:
        raise HTTPException(status_code=400, detail=err)
    ext = "jpg"
    if "." in fn:
        e = fn.rsplit(".", 1)[-1].lower()
        if e in ("jpg", "jpeg", "png"):
            ext = e
    profile = profile_service.get_or_create_profile(db, uid)
    if getattr(profile, "profile_photo_path", None):
        profile_photo_service.delete_stored_file(profile.profile_photo_path)
    new_path = profile_photo_service.save_new_profile_image(user_id=uid, content=content, ext=ext)
    profile.profile_photo_path = new_path
    db.add(profile)
    db.commit()
    db.refresh(profile)
    db.refresh(user)
    return _me_payload_from_user_and_profile(user, profile)


# fn: delete_profile_photo | tr: DELETE /auth/me/profile-photo — profil fotoğrafını kaldır / en: DELETE remove profile photo
@router.delete("/me/profile-photo", response_model=MeResponse)
def delete_profile_photo(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> MeResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    profile = profile_service.get_or_create_profile(db, uid)
    if getattr(profile, "profile_photo_path", None):
        profile_photo_service.delete_stored_file(profile.profile_photo_path)
        profile.profile_photo_path = None
        db.add(profile)
        db.commit()
        db.refresh(profile)
    db.refresh(user)
    return _me_payload_from_user_and_profile(user, profile)


# fn: delete_my_account | tr: DELETE /auth/me — hesabı ve tüm ilişkili veriyi kalıcı sil / en: DELETE permanently remove account and all related data
@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> Response:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")

    uid = decode_token_user_id(authorization)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        # tr: kullanıcı zaten yok — idempotent 204 / en: user already gone idempotent 204
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # tr: db silmeden önce diskteki profil fotoğrafını kaldır / en: remove profile photo file from disk before db delete
    p = db.query(UserProfile).filter(UserProfile.user_id == uid).first()
    if p and getattr(p, "profile_photo_path", None):
        profile_photo_service.delete_stored_file(p.profile_photo_path)

    try:
        quiz_attempt_ids = [
            int(row[0])
            for row in db.query(QuizAttempt.id).filter(QuizAttempt.user_id == uid).all()
            if row and row[0] is not None
        ]
        document_ids = [
            int(row[0])
            for row in db.query(Document.id).filter(Document.user_id == uid).all()
            if row and row[0] is not None
        ]

        if quiz_attempt_ids:
            db.query(QuizAttemptTopicResult).filter(
                QuizAttemptTopicResult.quiz_attempt_id.in_(quiz_attempt_ids)
            ).delete(synchronize_session=False)
            db.query(QuizAttemptQuestionResult).filter(
                QuizAttemptQuestionResult.quiz_attempt_id.in_(quiz_attempt_ids)
            ).delete(synchronize_session=False)
        db.query(QuizAttempt).filter(QuizAttempt.user_id == uid).delete(
            synchronize_session=False
        )

        db.query(ChatMessage).filter(ChatMessage.user_id == uid).delete(synchronize_session=False)
        if document_ids:
            db.query(DocumentSummaryHistory).filter(
                DocumentSummaryHistory.document_id.in_(document_ids)
            ).delete(synchronize_session=False)
            db.query(Recommendation).filter(
                Recommendation.document_id.in_(document_ids)
            ).delete(synchronize_session=False)
        db.query(Document).filter(Document.user_id == uid).delete(
            synchronize_session=False
        )

        db.query(PomodoroSession).filter(PomodoroSession.user_id == uid).delete(
            synchronize_session=False
        )
        db.query(UserBadge).filter(UserBadge.user_id == uid).delete(
            synchronize_session=False
        )
        db.query(UserProfile).filter(UserProfile.user_id == uid).delete(
            synchronize_session=False
        )
        db.query(AcademicStudyPlan).filter(AcademicStudyPlan.user_id == uid).delete(
            synchronize_session=False
        )
        db.query(Recommendation).filter(Recommendation.user_id == uid).delete(
            synchronize_session=False
        )
        db.delete(user)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Could not delete account. Please try again."
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)

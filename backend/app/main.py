# svc: main | tr: studyflow fastapi uygulama girişi, router mount, profil foto statik sunum / en: studyflow fastapi app entry routers mount profile photo static serving

import os
from pathlib import Path
from contextlib import asynccontextmanager

# tr: ml kütüphaneleri yüklenmeden önce hf/transformers gürültüsünü kapat / en: quiet hf/transformers noise before ml imports
from app.services.ml_runtime_env import configure_ml_runtime_env

configure_ml_runtime_env()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes.pdf_routes import router as pdf_router
from app.routes.quiz_routes import router as quiz_router
from app.routes.pomodoro_routes import router as pomodoro_router
from app.routes.stats_routes import router as stats_router
from app.routes.profile_routes import router as profile_router
from app.routes.resource_routes import router as resource_router
from app.routes.auth_routes import router as auth_router
from app.routes.academic_plan_routes import router as academic_plan_router
from app.database.connection import init_db
from app.models.pomodoro_model import PomodoroSession  # noqa: F401
from app.models.quiz import (  # noqa: F401
    QuizAttempt,
    QuizAttemptQuestionResult,
    QuizAttemptTopicResult,
)
from app.models.user_profile_model import UserProfile  # noqa: F401
from app.models.user_model import User  # noqa: F401
from app.models.badge_model import Badge, UserBadge  # noqa: F401
from app.models.document_model import Document  # noqa: F401
from app.models.academic_plan_model import AcademicStudyPlan  # noqa: F401
from app.models.document_summary_model import DocumentSummaryHistory  # noqa: F401
from app.models.message_model import ChatMessage  # noqa: F401
from app.models.recommendation_model import Recommendation  # noqa: F401
try:
    from app.services.hybrid_llm_service import provider_status as hybrid_provider_status
except Exception:
    # fn: hybrid_provider_status | tr: hybrid llm yoksa boş durum döndür / en: return empty status when hybrid llm missing
    def hybrid_provider_status() -> dict:
        return {
            "status": "unavailable",
            "detail": "hybrid_llm_service module not found",
        }


# fn: lifespan | tr: uygulama başlangıç/kapanış: db init, uploads klasörleri / en: app startup shutdown db init upload dirs
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    from app.database import connection as dbconn

    _log = logging.getLogger(__name__)
    if dbconn.engine is None:
        _log.warning(
            "Database not configured: quiz scores are not saved and GET /quiz/user-results stays empty. %s",
            (dbconn._import_error or "").strip(),
        )
    init_db()
    # tr: profil fotoğrafları backend/uploads/profile_photos — /uploads/... ile sunulur / en: profile photos served at /uploads/...
    _uploads = Path(__file__).resolve().parent.parent / "uploads"
    _uploads.mkdir(parents=True, exist_ok=True)
    (_uploads / "profile_photos").mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(lifespan=lifespan)

# tr: cors — frontend (angular ng serve) farklı origin'den api çağırabilsin / en: cors for frontend angular dev origins
cors_origins = os.getenv("CORS_ORIGINS")
if cors_origins:
    allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    allow_origin_regex = None
else:
    allowed_origins = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:4210",
        "http://127.0.0.1:4210",
    ]
    allow_origin_regex = (
        r"https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?$"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# tr: api router'ları prefix ile bağla / en: mount api routers with prefixes
app.include_router(pdf_router, prefix="/pdf", tags=["PDF"])
app.include_router(quiz_router, prefix="/quiz", tags=["Quiz"])
app.include_router(pomodoro_router, prefix="/pomodoro", tags=["Pomodoro"])
app.include_router(stats_router, prefix="/stats", tags=["Stats"])
app.include_router(profile_router, prefix="/study", tags=["Study"])
app.include_router(academic_plan_router, prefix="/study", tags=["Study"])
app.include_router(resource_router, prefix="/study", tags=["Study"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])

# tr: uploads klasörünü statik sun — profil foto url'leri /uploads/profile_photos/... / en: static mount for profile photo urls
_uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")


# fn: home | tr: kök endpoint sağlık mesajı / en: root endpoint health message
@app.get("/")
def home():
    return {"message": "StudyFlow backend is running 🚀"}


# fn: health | tr: mysql, ollama ve hybrid llm durum kontrolü / en: mysql ollama and hybrid llm health check
@app.get("/health")
def health():
    """MySQL connectivity and optional Ollama probe."""
    from sqlalchemy import text

    from app.database import connection as dbconn

    out = {
        "database": "unknown",
        "database_detail": None,
        "ollama": {"reachable": False, "detail": None, "base_url": None},
        "hybrid_fallback": {},
    }

    if dbconn.engine is None:
        out["database"] = "not_configured"
        out["database_detail"] = dbconn._import_error
    else:
        try:
            with dbconn.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            out["database"] = "ok"
        except Exception as e:
            out["database"] = "error"
            out["database_detail"] = str(e)

    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    out["ollama"]["base_url"] = base
    if os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes"):
        try:
            import httpx

            r = httpx.get(f"{base}/api/tags", timeout=2.0)
            out["ollama"]["reachable"] = r.status_code == 200
            if r.status_code != 200:
                out["ollama"]["detail"] = f"HTTP {r.status_code}"
        except Exception as e:
            out["ollama"]["reachable"] = False
            out["ollama"]["detail"] = str(e)
    else:
        out["ollama"]["detail"] = "OLLAMA_ENABLED=false"

    out["hybrid_fallback"] = hybrid_provider_status()
    return out

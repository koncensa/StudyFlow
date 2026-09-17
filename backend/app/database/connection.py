# db: connection | tr: mysql bağlantısı, sqlalchemy engine/session, tablo oluşturma ve şema migration yardımcıları / en: mysql connection sqlalchemy engine session table creation and schema migration helpers

import os
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

try:
    from dotenv import load_dotenv

    # tr: backend/.env dosyasını cwd'den bağımsız yükle / en: load backend/.env regardless of process cwd
    _BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_BACKEND_ROOT / ".env", override=True)
    load_dotenv(override=True)
except ImportError:
    pass

# tr: tüm model sınıflarının miras aldığı sqlalchemy taban sınıfı / en: sqlalchemy declarative base for all model classes
Base = declarative_base()


# fn: _build_database_url | tr: env'den mysql bağlantı url'si üret / en: build mysql connection url from env vars
def _build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    name = os.getenv("DB_NAME")

    if not user or not password or not name:
        raise RuntimeError(
            "MySQL configuration missing. Set either `DATABASE_URL` or "
            "the env vars: `DB_USER`, `DB_PASSWORD`, `DB_NAME` (see backend/.env.example)."
        )

    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"


_import_error: Optional[str] = None

try:
    DATABASE_URL = _build_database_url()
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception as e:
    # tr: db yapılandırılmamışsa uygulama yine de açılsın / en: allow app startup when db is not configured
    DATABASE_URL = None
    engine = None
    SessionLocal = None
    _import_error = str(e)


# fn: get_db | tr: fastapi dependency — istek başına sqlalchemy session ver, sonra kapat / en: fastapi dependency yield sqlalchemy session per request then close
def get_db() -> Generator[Optional[object], None, None]:
    if SessionLocal is None:
        yield None
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# fn: _ensure_documents_study_text_column | tr: documents.study_text sütunu yoksa ekle / en: add documents.study_text column if missing
def _ensure_documents_study_text_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "documents" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("documents")}
        if "study_text" in col_names:
            return
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE documents ADD COLUMN study_text LONGTEXT NULL"))
    except Exception:
        pass


# fn: _ensure_documents_study_text_longtext_mysql | tr: study_text TEXT ise LONGTEXT'e genişlet (büyük pdf) / en: widen study_text from TEXT to LONGTEXT for large pdfs
def _ensure_documents_study_text_longtext_mysql() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        if engine.dialect.name != "mysql":
            return
        insp = inspect(engine)
        if "documents" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("documents")}
        if "study_text" not in col_names:
            return
        with engine.begin() as conn:
            db_name = conn.execute(text("SELECT DATABASE()")).scalar()
            if not db_name:
                return
            row = conn.execute(
                text(
                    "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'documents' AND COLUMN_NAME = 'study_text'"
                ),
                {"db": db_name},
            ).fetchone()
            if not row:
                return
            data_type = (row[0] or "").lower()
            if data_type == "longtext":
                return
            if data_type in ("text", "mediumtext", "varchar", "tinytext"):
                conn.execute(text("ALTER TABLE documents MODIFY COLUMN study_text LONGTEXT NULL"))
    except Exception:
        pass


# fn: _ensure_quizzes_extra_columns | tr: quizzes tablosuna unanswered_count ve duration_seconds ekle / en: add unanswered_count and duration_seconds to quizzes
def _ensure_quizzes_extra_columns() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "quizzes" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("quizzes")}
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if "unanswered_count" not in col_names:
                if dialect == "mysql":
                    conn.execute(
                        text(
                            "ALTER TABLE quizzes ADD COLUMN unanswered_count INT NOT NULL DEFAULT 0"
                        )
                    )
                else:
                    conn.execute(text("ALTER TABLE quizzes ADD COLUMN unanswered_count INTEGER DEFAULT 0"))
            if "duration_seconds" not in col_names:
                if dialect == "mysql":
                    conn.execute(text("ALTER TABLE quizzes ADD COLUMN duration_seconds INT NULL"))
                else:
                    conn.execute(text("ALTER TABLE quizzes ADD COLUMN duration_seconds INTEGER NULL"))
    except Exception:
        pass


# fn: _ensure_profiles_profile_photo_path_column | tr: profiles.profile_photo_path sütunu yoksa ekle / en: add profiles.profile_photo_path if missing
def _ensure_profiles_profile_photo_path_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "profiles" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("profiles")}
        if "profile_photo_path" in col_names:
            return
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if dialect == "mysql":
                conn.execute(
                    text("ALTER TABLE profiles ADD COLUMN profile_photo_path VARCHAR(512) NULL")
                )
            else:
                conn.execute(text("ALTER TABLE profiles ADD COLUMN profile_photo_path VARCHAR(512) NULL"))
    except Exception:
        pass


# fn: _ensure_profiles_bio_column | tr: profiles.bio sütunu yoksa ekle / en: add profiles.bio column if missing
def _ensure_profiles_bio_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "profiles" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("profiles")}
        if "bio" in col_names:
            return
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE profiles ADD COLUMN bio TEXT NULL"))
    except Exception:
        pass


# fn: _ensure_academic_study_plans_topic_outline_column | tr: academic_study_plans.topic_outline_json ekle / en: add academic_study_plans.topic_outline_json column
def _ensure_academic_study_plans_topic_outline_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "academic_study_plans" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("academic_study_plans")}
        if "topic_outline_json" in col_names:
            return
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if dialect == "mysql":
                conn.execute(
                    text(
                        "ALTER TABLE academic_study_plans ADD COLUMN topic_outline_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE academic_study_plans ADD COLUMN topic_outline_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )
    except Exception:
        pass


# fn: _ensure_academic_study_plans_history_columns | tr: plan bitiş/geçmiş sütunlarını ekle (is_finished vb.) / en: add plan finish history columns is_finished etc
def _ensure_academic_study_plans_history_columns() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "academic_study_plans" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("academic_study_plans")}
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if "is_finished" not in col_names:
                if dialect == "mysql":
                    conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN is_finished TINYINT(1) NOT NULL DEFAULT 0"))
                else:
                    conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN is_finished BOOLEAN NOT NULL DEFAULT 0"))
            if "finished_at" not in col_names:
                conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN finished_at DATETIME NULL"))
            if "completed_count" not in col_names:
                conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN completed_count INT NOT NULL DEFAULT 0"))
            if "missed_count" not in col_names:
                conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN missed_count INT NOT NULL DEFAULT 0"))
            if "total_task_count" not in col_names:
                conn.execute(text("ALTER TABLE academic_study_plans ADD COLUMN total_task_count INT NOT NULL DEFAULT 0"))
    except Exception:
        pass


# fn: _ensure_document_summary_history_user_id_column | tr: özet geçmişine user_id ekle ve doldur / en: add and backfill user_id on document_summary_history
def _ensure_document_summary_history_user_id_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "document_summary_history" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("document_summary_history")}
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if "user_id" not in col_names:
                if dialect == "mysql":
                    conn.execute(text("ALTER TABLE document_summary_history ADD COLUMN user_id INT NULL"))
                else:
                    conn.execute(text("ALTER TABLE document_summary_history ADD COLUMN user_id INTEGER NULL"))
                if "documents" in insp.get_table_names():
                    conn.execute(
                        text(
                            "UPDATE document_summary_history h "
                            "INNER JOIN documents d ON h.document_id = d.id "
                            "SET h.user_id = d.user_id "
                            "WHERE h.user_id IS NULL"
                        )
                        if dialect == "mysql"
                        else text(
                            "UPDATE document_summary_history SET user_id = ("
                            "SELECT d.user_id FROM documents d "
                            "WHERE d.id = document_summary_history.document_id"
                            ") WHERE user_id IS NULL AND document_id IS NOT NULL"
                        )
                    )
                conn.execute(text("DELETE FROM document_summary_history WHERE user_id IS NULL"))
                if dialect == "mysql":
                    conn.execute(
                        text("ALTER TABLE document_summary_history MODIFY COLUMN user_id INT NOT NULL")
                    )
            if dialect == "mysql":
                idx_names = {idx["name"] for idx in insp.get_indexes("document_summary_history")}
                if "idx_dsh_user_id" not in idx_names:
                    try:
                        conn.execute(
                            text("CREATE INDEX idx_dsh_user_id ON document_summary_history (user_id)")
                        )
                    except Exception:
                        pass
                if "idx_dsh_user_document" not in idx_names:
                    try:
                        conn.execute(
                            text(
                                "CREATE INDEX idx_dsh_user_document ON document_summary_history (user_id, document_id)"
                            )
                        )
                    except Exception:
                        pass
    except Exception:
        pass


# fn: _ensure_messages_user_id_column | tr: sohbet mesajlarına user_id ekle ve doldur / en: add and backfill user_id on messages table
def _ensure_messages_user_id_column() -> None:
    if engine is None:
        return
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "messages" not in insp.get_table_names():
            return
        col_names = {c["name"] for c in insp.get_columns("messages")}
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if "user_id" not in col_names:
                if dialect == "mysql":
                    conn.execute(text("ALTER TABLE messages ADD COLUMN user_id INT NULL"))
                else:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN user_id INTEGER NULL"))
                if "documents" in insp.get_table_names():
                    conn.execute(
                        text(
                            "UPDATE messages m "
                            "INNER JOIN documents d ON m.document_id = d.id "
                            "SET m.user_id = d.user_id "
                            "WHERE m.user_id IS NULL"
                        )
                        if dialect == "mysql"
                        else text(
                            "UPDATE messages SET user_id = ("
                            "SELECT d.user_id FROM documents d "
                            "WHERE d.id = messages.document_id"
                            ") WHERE user_id IS NULL AND document_id IS NOT NULL"
                        )
                    )
                conn.execute(text("DELETE FROM messages WHERE user_id IS NULL"))
                if dialect == "mysql":
                    conn.execute(text("ALTER TABLE messages MODIFY COLUMN user_id INT NOT NULL"))
            if dialect == "mysql":
                idx_names = {idx["name"] for idx in insp.get_indexes("messages")}
                if "idx_messages_user_id" not in idx_names:
                    try:
                        conn.execute(text("CREATE INDEX idx_messages_user_id ON messages (user_id)"))
                    except Exception:
                        pass
                if "idx_messages_user_document" not in idx_names:
                    try:
                        conn.execute(
                            text(
                                "CREATE INDEX idx_messages_user_document ON messages (user_id, document_id)"
                            )
                        )
                    except Exception:
                        pass
    except Exception:
        pass


# fn: init_db | tr: uygulama başlangıcında tabloları oluştur + eksik sütun migration'ları çalıştır / en: create tables at startup and run missing column migrations
def init_db() -> None:
    auto_create = os.getenv("DB_AUTO_CREATE_TABLES", "true").lower() == "true"
    if not auto_create:
        return

    if engine is None:
        return

    Base.metadata.create_all(bind=engine)
    _ensure_profiles_profile_photo_path_column()
    _ensure_profiles_bio_column()
    _ensure_academic_study_plans_topic_outline_column()
    _ensure_academic_study_plans_history_columns()
    _ensure_documents_study_text_column()
    _ensure_documents_study_text_longtext_mysql()
    _ensure_quizzes_extra_columns()
    _ensure_document_summary_history_user_id_column()
    _ensure_messages_user_id_column()

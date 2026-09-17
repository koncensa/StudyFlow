# route: pdf_routes | tr: pdf yükleme, sohbet, özet, quiz ve oturum geçmişi http endpoint'leri — main.py'de /pdf prefix ile bağlanır / en: pdf upload chat summary quiz and session history endpoints mounted at /pdf prefix in main.py

import logging
import os
import shutil
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.document_db_service import resolve_document_pk, try_restore_document_to_store
from app.deps.auth import require_user_id, require_user_id_matching
from app.schemas.pdf_schema import (
    PdfApplyModeRequest,
    PdfApplyModeResponse,
    PdfChatRequest,
    PdfChatResponse,
    PdfDynamicSummaryRequest,
    PdfDynamicSummaryResponse,
    PdfSummaryHistoryRequest,
    PdfSummaryHistoryResponse,
    PdfSummaryHistoryItem,
    PdfDocumentStatusRequest,
    PdfDocumentStatusResponse,
    PdfSessionHistoryItem,
    PdfSessionHistoryMessage,
    PdfSessionHistoryResponse,
    StudyOutcomeRequest,
    StudyOutcomeResponse,
)
from app.services.chat_message_service import append_chat_turn, list_user_pdf_sessions
from app.schemas.quiz_schema import quiz_bundle_for_pdf_json
from app.services.document_store import create_document, document_missing_reason, get_document
from app.services.document_summary_history_service import (
    list_summary_history,
    save_summary_history,
)
from app.services.study_modes.outcomes import outcome_for
from app.services.pdf_session import (
    chat_with_document,
    user_context_suffix,
)
from app.services.pdf_service import extract_text_from_pdf
from app.services.pdf_text_clean import build_normalized_study_text
from app.services.quiz_service import (
    QuizGenerationError,
    generate_quiz_guaranteed,
    warm_document_quiz_topics_background,
)
from app.services.summary_service import (
    generate_summary,
    normalize_output_locale,
    normalize_summary_format_enum,
    normalize_summary_length,
    normalize_summary_style,
)
from app.services.stats_service import build_quiz_generation_profile

router = APIRouter()
log = logging.getLogger(__name__)

# tr: yüklenen pdf dosyalarının geçici disk klasörü / en: temporary disk folder for uploaded pdf files
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# fn: _default_quiz_level_from_explain | tr: explain_level -> varsayılan quiz zorluk seviyesi (1-8) / en: map explain_level to default quiz level 1-8
def _default_quiz_level_from_explain(explain_level: str) -> int:
    e = (explain_level or "normal").strip().lower()
    if e == "beginner":
        return 1
    if e == "technical":
        return 4
    return 2


# fn: _resolve_quiz_level | tr: açık quiz_level yoksa explain_level'dan türet / en: use explicit quiz_level or derive from explain_level
def _resolve_quiz_level(explicit: Optional[int], explain_level: str) -> int:
    if explicit is not None and int(explicit) > 0:
        return max(1, min(8, int(explicit)))
    return _default_quiz_level_from_explain(explain_level)


# fn: pdf_chat | tr: POST /pdf/chat — pdf üzerinde çok turlu sohbet (tutor modu) / en: POST multi-turn chat over uploaded pdf
@router.post("/chat", response_model=PdfChatResponse)
async def pdf_chat(
    body: PdfChatRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PdfChatResponse:
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    raw_id = (body.document_id or "").strip()
    if not raw_id:
        raise HTTPException(status_code=400, detail="Missing document_id.")
    doc = get_document(raw_id)
    if not doc and db is not None:
        doc = try_restore_document_to_store(db, raw_id, uid)
    if not doc:
        miss = document_missing_reason(raw_id) or "missing_document"
        msg = (
            "The PDF session is missing on the server or has expired. Please upload the PDF again."
            if miss == "expired_document"
            else "PDF was not found. Please upload it again."
        )
        return PdfChatResponse(reply=msg)
    if doc.user_id != uid:
        return PdfChatResponse(reply="This document does not belong to your session.")
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    loc = (body.output_locale or "en").strip().lower()[:8]
    focus = [str(x).strip() for x in (body.focus_topics or []) if str(x).strip()][:12]
    challenge = [str(x).strip() for x in (body.challenge_topics or []) if str(x).strip()][:10]
    pers = user_context_suffix(focus, challenge, loc)
    reply = chat_with_document(
        raw_id,
        history,
        user_id=uid,
        study_mode=body.study_mode,
        explain_level=body.explain_level,
        output_locale=loc,
        personalization_suffix=pers,
    )
    if db is not None and history:
        last_user = ""
        for turn in reversed(history):
            if turn.get("role") == "user":
                last_user = (turn.get("content") or "").strip()
                break
        if last_user and (reply or "").strip():
            try:
                append_chat_turn(
                    db,
                    user_id=uid,
                    session_key=raw_id,
                    user_content=last_user,
                    assistant_content=reply.strip(),
                    mode=(body.study_mode or "tutor_chat"),
                )
            except Exception:
                log.exception("pdf chat: failed to persist messages for user_id=%s", uid)
    return PdfChatResponse(reply=reply)


# fn: pdf_apply_mode | tr: POST /pdf/apply-mode — çalışma modu uygula (özet/explain/exam) ve geçmişe kaydet / en: POST apply study mode summary explain exam and save history
@router.post("/apply-mode", response_model=PdfApplyModeResponse)
async def pdf_apply_mode(
    body: PdfApplyModeRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PdfApplyModeResponse:
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    raw_id = (body.document_id or "").strip()
    if not raw_id:
        raise HTTPException(status_code=400, detail="Missing document_id.")
    doc = get_document(raw_id)
    if not doc and db is not None:
        doc = try_restore_document_to_store(db, raw_id, uid)
    if not doc:
        miss = document_missing_reason(raw_id) or "missing_document"
        msg = (
            "The PDF session is missing on the server or has expired. Please upload the PDF again."
            if miss == "expired_document"
            else "PDF was not found. Please upload it again."
        )
        return PdfApplyModeResponse(reply=msg)
    if doc.user_id != uid:
        return PdfApplyModeResponse(reply="This document does not belong to your session.")
    loc = (body.output_locale or "en").strip().lower()[:8]
    focus = [str(x).strip() for x in (body.focus_topics or []) if str(x).strip()][:12]
    challenge = [str(x).strip() for x in (body.challenge_topics or []) if str(x).strip()][:10]
    requested_mode = (body.mode or body.study_mode or "summary").strip().lower()
    history_id = None
    result = generate_summary(
        doc,
        mode=requested_mode,
        level=body.explain_level,
        output_locale=loc,
        focus_topics=focus,
        challenge_topics=challenge,
        summary_style=body.summary_style,
        summary_format=body.summary_format,
        summary_length=body.summary_length,
    )
    reply = result.text
    if db is not None:
        try:
            saved = save_summary_history(
                db,
                user_id=uid,
                session_key=raw_id,
                mode=result.mode,
                level=result.level,
                output_locale=loc,
                content=reply,
            )
            history_id = int(saved.id) if saved is not None else None
        except Exception:
            log.exception("pdf apply-mode: failed to save summary history")
    return PdfApplyModeResponse(
        reply=reply,
        history_id=history_id,
        pipeline_duration_ms=getattr(result, "pipeline_duration_ms", None),
        pipeline_llm_calls=getattr(result, "pipeline_llm_calls", None),
        pipeline_used_map_reduce=getattr(result, "pipeline_used_map_reduce", None),
        summary_quality=getattr(result, "summary_quality", None),
        summary_warning=getattr(result, "summary_warning", None),
        used_fallback=getattr(result, "used_fallback", None),
        fallback_reason=(getattr(result, "fallback_reason", None) or None),
        summary_quality_warning=getattr(result, "summary_quality_warning", None),
        summary_generation_note=getattr(result, "summary_generation_note", None),
    )


# fn: pdf_generate_summary | tr: POST /pdf/generate-summary — dinamik özet + isteğe bağlı quiz / en: POST dynamic summary with optional bundled quiz
@router.post("/generate-summary", response_model=PdfDynamicSummaryResponse)
async def pdf_generate_summary(
    body: PdfDynamicSummaryRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PdfDynamicSummaryResponse:
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    raw_id = (body.document_id or "").strip()
    if not raw_id:
        raise HTTPException(status_code=400, detail="Missing document_id.")
    doc = get_document(raw_id)
    if not doc and db is not None:
        doc = try_restore_document_to_store(db, raw_id, uid)
    if not doc:
        miss = document_missing_reason(raw_id) or "missing_document"
        msg = (
            "The PDF session is missing on the server or has expired. Please upload the PDF again."
            if miss == "expired_document"
            else "PDF was not found. Please upload it again."
        )
        raise HTTPException(status_code=400, detail=msg)
    if doc.user_id != uid:
        raise HTTPException(status_code=403, detail="This document does not belong to your session.")

    loc = normalize_output_locale(body.output_locale)
    focus = [str(x).strip() for x in (body.focus_topics or []) if str(x).strip()][:12]
    challenge = [str(x).strip() for x in (body.challenge_topics or []) if str(x).strip()][:10]
    requested_mode = (body.mode or body.study_mode or "summary").strip().lower()
    result = generate_summary(
        doc,
        mode=requested_mode,
        level=body.explain_level,
        output_locale=loc,
        focus_topics=focus,
        challenge_topics=challenge,
        summary_style=body.summary_style,
        summary_format=body.summary_format,
        summary_length=body.summary_length,
    )
    summary_text = result.text
    history_id = None
    if db is not None:
        try:
            saved = save_summary_history(
                db,
                user_id=uid,
                session_key=raw_id,
                mode=result.mode,
                level=result.level,
                output_locale=loc,
                content=summary_text,
            )
            history_id = int(saved.id) if saved is not None else None
        except Exception:
            log.exception("pdf generate-summary: failed to save summary history")

    quiz = None
    if body.include_quiz:
        adapted_focus = list(focus)
        adapted_challenge = list(challenge)
        forbidden_stems: list[str] = []
        blocked_topic_types: dict[str, list[str]] = {}
        quiz_difficulty = (body.quiz_difficulty or body.explain_level or "normal").strip().lower()[:16]
        if db is not None:
            doc_pk = resolve_document_pk(db, uid, raw_id)
            prof = build_quiz_generation_profile(
                db,
                uid,
                document_db_id=doc_pk,
                max_weak=max(8, min(body.max_topics + 4, 14)),
                max_strong=max(6, min(body.max_topics + 2, 10)),
            )
            for t in prof.weak_topics:
                if t not in adapted_focus:
                    adapted_focus.append(t)
            for t in prof.strong_topics:
                if t not in adapted_challenge:
                    adapted_challenge.append(t)
            adapted_focus = adapted_focus[:12]
            adapted_challenge = adapted_challenge[:10]
            quiz_difficulty = prof.adaptive_difficulty
            forbidden_stems = list(prof.recent_stems or [])[:140]
            blocked_topic_types = dict(prof.blocked_topic_types or {})
        quiz_level = _resolve_quiz_level(body.quiz_level, body.explain_level)
        # tr: quiz yalnızca normalize pdf metninden (study_text), özetten değil / en: quiz grounds in study_text not summary
        quiz_body = (doc.study_text or "").strip()
        if quiz_body:
            try:
                questions = generate_quiz_guaranteed(
                    quiz_body,
                    num_questions=body.num_questions,
                    max_topics=body.max_topics,
                    focus_topics=adapted_focus,
                    challenge_topics=adapted_challenge,
                    locale=loc,
                    difficulty=quiz_difficulty,
                    document_id=raw_id,
                    quiz_level=quiz_level,
                    forbidden_stems=forbidden_stems,
                    blocked_topic_types=blocked_topic_types,
                )
                quiz = quiz_bundle_for_pdf_json(
                    questions,
                    difficulty=quiz_difficulty,
                    quiz_kind=body.quiz_kind,
                    quiz_level=quiz_level,
                )
            except Exception:
                log.warning("pdf generate-summary: quiz generation failed", exc_info=True)
                quiz = None
        else:
            log.warning("pdf generate-summary: include_quiz requested but study_text is empty")

    return PdfDynamicSummaryResponse(
        summary=summary_text,
        mode=result.mode,
        explain_level=result.level,
        summary_style=normalize_summary_style(body.summary_style),
        summary_format=normalize_summary_format_enum(body.summary_format),
        summary_length=normalize_summary_length(body.summary_length),
        history_id=history_id,
        quiz=quiz,
        pipeline_duration_ms=getattr(result, "pipeline_duration_ms", None),
        pipeline_llm_calls=getattr(result, "pipeline_llm_calls", None),
        pipeline_used_map_reduce=getattr(result, "pipeline_used_map_reduce", None),
        pipeline_map_chunks_in=getattr(result, "pipeline_map_chunks_in", None),
        pipeline_map_chunks_after_quality=getattr(result, "pipeline_map_chunks_after_quality", None),
        pipeline_map_chunks_skipped_quality=getattr(result, "pipeline_map_chunks_skipped_quality", None),
        pipeline_map_chunks_out=getattr(result, "pipeline_map_chunks_out", None),
        pipeline_map_phase_ms=getattr(result, "pipeline_map_phase_ms", None),
        pipeline_cache_hit=getattr(result, "pipeline_cache_hit", None),
        pipeline_fallback_path=(getattr(result, "pipeline_fallback_path", None) or None),
        summary_quality=getattr(result, "summary_quality", None),
        summary_warning=getattr(result, "summary_warning", None),
        used_fallback=getattr(result, "used_fallback", None),
        fallback_reason=(getattr(result, "fallback_reason", None) or None),
        summary_quality_warning=getattr(result, "summary_quality_warning", None),
        summary_generation_note=getattr(result, "summary_generation_note", None),
    )


# fn: pdf_summary_history | tr: POST /pdf/summary-history — belge için özet geçmişi listele / en: POST list summary history for document session
@router.post("/summary-history", response_model=PdfSummaryHistoryResponse)
async def pdf_summary_history(
    body: PdfSummaryHistoryRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PdfSummaryHistoryResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    rows = list_summary_history(
        db,
        user_id=uid,
        session_key=(body.document_id or "").strip(),
        limit=int(body.limit or 24),
    )
    items = [
        PdfSummaryHistoryItem(
            id=int(r.id),
            mode=str(r.mode or "summary"),
            explain_level=str(r.level or "normal"),
            output_locale=str(r.output_locale or "en"),
            summary=str(r.content or ""),
            created_at=r.created_at.isoformat() if getattr(r, "created_at", None) else "",
        )
        for r in rows
    ]
    return PdfSummaryHistoryResponse(items=items)


# fn: pdf_session_history | tr: GET /pdf/session-history — kullanıcının pdf sohbet oturumları / en: GET user's pdf chat session list
@router.get("/session-history", response_model=PdfSessionHistoryResponse)
async def pdf_session_history(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
    limit: int = 32,
) -> PdfSessionHistoryResponse:
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured.")
    uid = require_user_id(authorization)
    rows = list_user_pdf_sessions(db, uid, limit=max(1, min(int(limit or 32), 64)))
    items = [
        PdfSessionHistoryItem(
            key=row.key,
            document_id=row.document_id,
            filename=row.filename,
            messages=[
                PdfSessionHistoryMessage(role=m.role, content=m.content) for m in row.messages
            ],
            updated_at=row.updated_at.isoformat() if row.updated_at else "",
        )
        for row in rows
    ]
    return PdfSessionHistoryResponse(items=items)


# fn: pdf_document_status | tr: POST /pdf/document-status — belge hâlâ bellekte/restore edilebilir mi / en: POST check if pdf session still live in store
@router.post("/document-status", response_model=PdfDocumentStatusResponse)
async def pdf_document_status(
    body: PdfDocumentStatusRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> PdfDocumentStatusResponse:
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    raw = (body.document_id or "").strip()
    log.info(
        "pdf document-status: request document_id prefix=%s user_id=%s",
        raw[:16] if raw else "(empty)",
        uid,
    )
    if len(raw) < 8:
        log.warning("pdf document-status: rejected short document_id")
        return PdfDocumentStatusResponse(
            ok=False,
            error="missing_document",
            message="Geçersiz oturum kimliği.",
        )

    doc = get_document(raw)
    if not doc and db is not None:
        doc = try_restore_document_to_store(db, raw, uid)
    if doc:
        if doc.user_id != uid:
            log.warning(
                "pdf document-status: wrong user document_id prefix=%s doc_user=%s request_user=%s",
                raw[:16],
                doc.user_id,
                uid,
            )
            return PdfDocumentStatusResponse(
                ok=False,
                error="wrong_user",
                message="Bu belge bu hesaba ait değil.",
            )
        log.info("pdf document-status: ok document_id prefix=%s", raw[:16])
        return PdfDocumentStatusResponse(ok=True)

    miss = document_missing_reason(raw) or "missing_document"
    log.warning(
        "pdf document-status: not found document_id prefix=%s classified=%s",
        raw[:16],
        miss,
    )
    msg_tr = (
        "Bu PDF oturumu sona erdi. Tekrar yükleyin."
        if miss == "expired_document"
        else "Bu PDF oturumu bulunamadı. Tekrar yükleyin."
    )
    return PdfDocumentStatusResponse(ok=False, error=miss, message=msg_tr)


# fn: pdf_study_outcome | tr: POST /pdf/study-outcome — eski tek seferlik çıktı (deprecated) / en: POST legacy one-shot outcome deprecated use generate-summary
@router.post("/study-outcome", response_model=StudyOutcomeResponse)
async def pdf_study_outcome(
    body: StudyOutcomeRequest,
    authorization: Annotated[Optional[str], Header()] = None,
) -> StudyOutcomeResponse:
    uid = require_user_id_matching(
        authorization, int(body.user_id) if body.user_id is not None else None
    )
    doc = get_document(body.document_id.strip())
    if not doc:
        return StudyOutcomeResponse(outcome=body.outcome, content="Session expired or invalid. Upload the PDF again.")
    if doc.user_id != uid:
        return StudyOutcomeResponse(
            outcome=body.outcome,
            content="This document does not belong to your session.",
        )
    text = outcome_for(doc, body.outcome, explain_level=body.explain_level or "normal")
    return StudyOutcomeResponse(outcome=body.outcome, content=text)


# fn: upload_pdf | tr: POST /pdf/upload-pdf — pdf yükle, metin çıkar, bellekte oturum oluştur / en: POST upload pdf extract text create in-memory session
@router.post("/upload-pdf")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    create_quiz: bool = Form(False),
    num_questions: int = Form(5),
    user_id: int = Form(1),
    output_locale: str = Form("en"),
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
):
    _ = user_id  # tr: eski istemciler için; kimlik yalnızca jwt'den / en: legacy form field identity from jwt only
    raw_name = (file.filename or "").strip()
    loc0 = (output_locale or "en").strip().lower()
    tr_ui = loc0.startswith("tr")
    if not raw_name or not raw_name.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_file_type",
                "message": (
                    "Yalnızca .pdf dosyası yükleyebilirsiniz."
                    if tr_ui
                    else "Only PDF files are allowed."
                ),
            },
        )

    try:
        uid = require_user_id(authorization)
    except HTTPException as e:
        d = e.detail
        msg = d if isinstance(d, str) else "Authentication required. Please log in."
        return JSONResponse(status_code=e.status_code, content={"error": "auth", "message": msg})

    safe_name = os.path.basename(raw_name) or "document.pdf"
    unique_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        extracted_text = extract_text_from_pdf(file_path)
        study_text = build_normalized_study_text(extracted_text)
        raw_len = len((extracted_text or "").strip())
        study_len = len((study_text or "").strip())
        if raw_len < 40 or study_len < 40:
            log.warning(
                "pdf upload-pdf: extract too short raw_len=%s study_len=%s filename=%s",
                raw_len,
                study_len,
                safe_name[:80],
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "pdf_text_not_extractable",
                    "message": (
                        "Bu PDF'den metin okunamadi. Dosya taranmis goruntu olabilir; secilebilir metin iceren PDF yukleyin."
                        if tr_ui
                        else "Could not extract readable text from this PDF. It may be image-scanned; upload a text-based PDF."
                    ),
                },
            )
        doc = create_document(study_text, safe_name, user_id=uid)
    except OSError as e:
        log.exception("pdf upload-pdf: failed to save or read file")
        return JSONResponse(
            status_code=500,
            content={
                "error": "upload_io_error",
                "message": (
                    f"Dosya kaydedilemedi veya okunamadı: {e}"
                    if tr_ui
                    else f"Could not save or read the file: {e}"
                ),
            },
        )
    except Exception:
        log.exception("pdf upload-pdf: unexpected error during ingest")
        return JSONResponse(
            status_code=500,
            content={
                "error": "upload_failed",
                "message": (
                    "PDF işlenirken hata oluştu. Dosyayı tekrar deneyin veya başka bir PDF ile test edin."
                    if tr_ui
                    else "Could not process this PDF. Try again or test with another file."
                ),
            },
        )
    log.info(
        "pdf upload-pdf: stored document_id prefix=%s user_id=%s filename=%s",
        doc.document_id[:16],
        uid,
        safe_name[:80],
    )

    loc = normalize_output_locale(output_locale)
    background_tasks.add_task(warm_document_quiz_topics_background, doc.document_id, loc)
    brief = ""
    summary_en = ""
    summary_tr = ""
    primary_summary = ""

    if os.getenv("PDF_UPLOAD_INITIAL_SUMMARY", "false").strip().lower() in ("1", "true", "yes", "on"):
        try:
            result = generate_summary(
                doc,
                mode="summary",
                level="normal",
                output_locale=loc,
            )
            brief = result.text
            primary_summary = result.text
            summary_en = result.text
            summary_tr = ""
        except Exception:
            log.exception("pdf upload-pdf: structured initial summary failed")

    quiz = None
    if create_quiz:
        try:
            target_questions = max(1, min(15, int(num_questions or 5)))
            adapted_focus: list[str] = []
            adapted_challenge: list[str] = []
            forbidden_stems: list[str] = []
            blocked_topic_types: dict[str, list[str]] = {}
            diff = "normal"
            if db is not None:
                doc_pk = resolve_document_pk(db, uid, doc.document_id)
                prof = build_quiz_generation_profile(
                    db,
                    uid,
                    document_db_id=doc_pk,
                    max_weak=max(8, min(target_questions + 4, 14)),
                    max_strong=max(6, min(target_questions + 2, 10)),
                )
                adapted_focus = list(prof.weak_topics or [])[:12]
                adapted_challenge = list(prof.strong_topics or [])[:10]
                forbidden_stems = list(prof.recent_stems or [])[:140]
                blocked_topic_types = dict(prof.blocked_topic_types or {})
                diff = prof.adaptive_difficulty
            quiz_questions = generate_quiz_guaranteed(
                study_text,
                num_questions=target_questions,
                max_topics=max(4, min(target_questions + 3, 12)),
                focus_topics=adapted_focus or None,
                challenge_topics=adapted_challenge or None,
                locale=loc,
                difficulty=diff,
                document_id=doc.document_id,
                forbidden_stems=forbidden_stems,
                blocked_topic_types=blocked_topic_types,
            )
            quiz = quiz_bundle_for_pdf_json(quiz_questions)
        except QuizGenerationError:
            quiz = None

    if db is not None:
        try:
            from app.services import document_db_service, profile_service

            document_db_service.register_document(
                db, uid, doc.document_id, safe_name, study_text=study_text
            )
            profile_service.apply_after_pdf_upload(db, uid)
        except Exception:
            log.exception(
                "pdf upload-pdf: failed to persist document row (sessions will not restore after restart); "
                "document_id prefix=%s user_id=%s",
                doc.document_id[:16],
                uid,
            )

    return {
        "message": "PDF uploaded successfully",
        "document_id": doc.document_id,
        "brief_summary": brief,
        "filename": safe_name,
        "path": file_path,
        "extracted_text_preview": extracted_text[:1500],
        "summary": primary_summary,
        "summary_en": summary_en,
        "summary_tr": summary_tr,
        "quiz": quiz,
    }

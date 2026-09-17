# route: quiz_routes | tr: quiz üretimi, gönderim, geçmiş ve sonuç http endpoint'leri — main.py'de /quiz prefix ile bağlanır / en: quiz generate submit history results endpoints mounted at /quiz prefix in main.py

import hashlib
from typing import Annotated, Optional
import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.schemas.quiz_results_schema import QuizHistoryListResponse, QuizResultsPageResponse
from app.schemas.quiz_schema import (
    QuizGenerateFromDocumentRequest,
    QuizGenerateRequest,
    QuizGenerateResponse,
    QuizQuestion,
    QuizSubmissionRequest,
    QuizSubmissionResponse,
)
from app.schemas.suggestion_schema import SuggestionResponse
from app.schemas.topic_schema import TopicAnalysisResponse
from app.services.document_db_service import resolve_document_pk, try_restore_document_to_store
from app.services.document_store import document_missing_reason, get_document
from app.services.quiz_service import QuizGenerationError, generate_quiz_guaranteed
from app.services.quiz_timing import compute_quiz_time_limit_seconds
from app.database.connection import get_db
from app.deps.auth import require_user_id, require_user_id_matching
from app.services.quiz_attempt_service import record_quiz_attempt_and_build_response
from app.services.quiz_history_service import list_user_quiz_history
from app.services.quiz_results_service import build_user_results_page
from app.services.quiz_scores import score_submission
from app.services.suggestion_service import build_suggestions
from app.services.stats_service import build_quiz_generation_profile
from app.services.summary_service import normalize_output_locale

router = APIRouter()
log = logging.getLogger(__name__)

# tr: belge bulunamadı / süresi doldu kullanıcı mesajları / en: user-facing document missing or expired messages
QUIZ_DOC_MSG_MISSING = "PDF was not found. Please upload it again."
QUIZ_DOC_MSG_EXPIRED = (
    "The PDF session is missing on the server or has expired. Please upload the PDF again."
)
MAX_STANDARD_QUIZ_QUESTIONS = 15
MAX_MINI_QUIZ_QUESTIONS = 15


# fn: _quiz_document_error_response | tr: pdf oturumu hatası için standart 400 json / en: standard 400 json for pdf session errors
def _quiz_document_error_response(error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": error, "message": message},
    )


# fn: _auto_num_questions_from_text | tr: istenen soru sayısını 1-15 aralığına sıkıştır / en: clamp requested question count to 1-15
def _auto_num_questions_from_text(text: str, requested: int, focus_len: int = 0) -> int:
    _ = (text, focus_len)
    hard_cap = MAX_STANDARD_QUIZ_QUESTIONS
    if requested and requested > 0:
        return max(1, min(requested, hard_cap))
    return 5


# fn: _uniq_extend_topics | tr: konu listesine benzersiz eklemeler yap / en: extend topic list with unique entries up to cap
def _uniq_extend_topics(base: list[str], more: Optional[list[str]], cap: int) -> list[str]:
    seen = {x.strip().lower() for x in base if x and str(x).strip()}
    out = list(base)
    for t in more or []:
        s = str(t).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= cap:
            break
    return out


# fn: _stable_mix_u32 | tr: deterministik 32-bit karışım (generation_salt için) / en: deterministic 32-bit mix for generation_salt
def _stable_mix_u32(*parts: object) -> int:
    raw = "||".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)


# fn: _generate_quiz_exact_with_retry | tr: tam N soru üret, geçici hatalarda 5 kez dene / en: generate exactly N questions retry up to 5 times
def _generate_quiz_exact_with_retry(
    *,
    content: str,
    num_questions: int,
    max_topics: int,
    focus_topics: Optional[list[str]],
    challenge_topics: Optional[list[str]],
    locale: str,
    difficulty: str,
    document_id: Optional[str],
    forbidden_stems: Optional[list[str]] = None,
    generation_salt: int = 0,
):
    target = max(1, min(int(num_questions or 1), MAX_STANDARD_QUIZ_QUESTIONS))
    last_err: Optional[QuizGenerationError] = None
    for attempt in range(5):
        try:
            return generate_quiz_guaranteed(
                content,
                num_questions=target,
                max_topics=max_topics,
                focus_topics=focus_topics,
                challenge_topics=challenge_topics,
                locale=locale,
                difficulty=difficulty,
                document_id=document_id,
                forbidden_stems=forbidden_stems,
                generation_salt=generation_salt,
            )
        except QuizGenerationError as e:
            last_err = e
            log.warning(
                "quiz generation failed attempt=%s requested=%s document_id_prefix=%s detail=%s",
                attempt + 1,
                target,
                (document_id or "")[:16],
                e.detail_tr,
            )
    if last_err:
        raise last_err
    raise QuizGenerationError("Quiz generation failed. Please try again.")


# fn: _dedupe_quiz_questions | tr: aynı id veya kök metinli soruları çıkar / en: remove duplicate questions by id or stem
def _dedupe_quiz_questions(items: list[QuizQuestion]) -> list[QuizQuestion]:
    out: list[QuizQuestion] = []
    seen: set[str] = set()
    for q in items:
        qid = str(getattr(q, "id", "") or "").strip().lower()
        stem = str(getattr(q, "question_text", "") or "").strip().lower()
        key = qid if qid else stem
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


# fn: _generate_quiz_exact_chunked | tr: 6'lık partiler halinde hedef soru sayısına ulaş / en: build target count in batches of up to 6
def _generate_quiz_exact_chunked(
    *,
    content: str,
    target: int,
    max_topics: int,
    focus_topics: Optional[list[str]],
    challenge_topics: Optional[list[str]],
    locale: str,
    difficulty: str,
    document_id: Optional[str],
    forbidden_stems: Optional[list[str]] = None,
    generation_salt: int = 0,
) -> list[QuizQuestion]:
    collected: list[QuizQuestion] = []
    safety_rounds = 0
    last_err: Optional[QuizGenerationError] = None
    while len(collected) < target and safety_rounds < 16:
        safety_rounds += 1
        before = len(collected)
        remaining = target - len(collected)
        batch_n = min(6, remaining)
        try:
            batch = _generate_quiz_exact_with_retry(
                content=content,
                num_questions=batch_n,
                max_topics=max_topics,
                focus_topics=focus_topics,
                challenge_topics=challenge_topics,
                locale=locale,
                difficulty=difficulty,
                document_id=document_id,
                forbidden_stems=forbidden_stems,
                generation_salt=generation_salt + safety_rounds,
            )
        except QuizGenerationError as e:
            last_err = e
            log.warning(
                "quiz chunked batch failed round=%s batch_n=%s collected=%s detail=%s",
                safety_rounds,
                batch_n,
                len(collected),
                e.detail_tr,
            )
            if len(collected) == before and safety_rounds >= 4:
                break
            continue
        collected.extend(batch)
        collected = _dedupe_quiz_questions(collected)

    if len(collected) < target:
        if last_err:
            raise last_err
        raise QuizGenerationError(
            "İstenen soru sayısı üretilemedi. Lütfen tekrar deneyin veya soru sayısını biraz azaltın."
        )
    return collected[:target]


# fn: _generate_quiz_exact_batched | tr: önce tek geçiş, başarısızsa chunked yedek / en: single pass first then chunked fallback
def _generate_quiz_exact_batched(
    *,
    content: str,
    num_questions: int,
    max_topics: int,
    focus_topics: Optional[list[str]],
    challenge_topics: Optional[list[str]],
    locale: str,
    difficulty: str,
    document_id: Optional[str],
    forbidden_stems: Optional[list[str]] = None,
    generation_salt: int = 0,
) -> list[QuizQuestion]:
    target = max(1, min(int(num_questions or 1), MAX_STANDARD_QUIZ_QUESTIONS))
    try:
        return _generate_quiz_exact_with_retry(
            content=content,
            num_questions=target,
            max_topics=max_topics,
            focus_topics=focus_topics,
            challenge_topics=challenge_topics,
            locale=locale,
            difficulty=difficulty,
            document_id=document_id,
            forbidden_stems=forbidden_stems,
            generation_salt=generation_salt,
        )
    except QuizGenerationError as primary_err:
        if target <= 6:
            raise primary_err from None
        log.warning(
            "quiz single-pass generation failed; trying chunked fallback target=%s detail=%s",
            target,
            primary_err.detail_tr,
        )
        try:
            return _generate_quiz_exact_chunked(
                content=content,
                target=target,
                max_topics=max_topics,
                focus_topics=focus_topics,
                challenge_topics=challenge_topics,
                locale=locale,
                difficulty=difficulty,
                document_id=document_id,
                forbidden_stems=forbidden_stems,
                generation_salt=generation_salt,
            )
        except QuizGenerationError as chunk_err:
            raise chunk_err from primary_err


# fn: generate_quiz_endpoint | tr: POST /quiz/generate — ham metinden quiz üret / en: POST generate quiz from raw text content
@router.post("/generate", response_model=QuizGenerateResponse)
def generate_quiz_endpoint(
    payload: QuizGenerateRequest,
    authorization: Annotated[Optional[str], Header()] = None,
) -> QuizGenerateResponse:
    require_user_id(authorization)
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="`content` must not be empty.")

    num_questions = _auto_num_questions_from_text(
        payload.content,
        payload.num_questions,
        focus_len=len(payload.focus_topics or []),
    )
    max_topics = max(1, min(payload.max_topics, 15))

    try:
        questions = _generate_quiz_exact_batched(
            content=payload.content,
            num_questions=num_questions,
            max_topics=max_topics,
            focus_topics=payload.focus_topics or None,
            challenge_topics=payload.challenge_topics or None,
            locale=(payload.locale or "en").strip().lower()[:8],
            difficulty=(payload.difficulty or "normal").strip().lower()[:16],
            document_id=None,
        )
    except QuizGenerationError as e:
        raise HTTPException(status_code=503, detail=e.detail_tr) from e
    topics_set = {q.topic.strip() for q in questions if q.topic and q.topic.strip()}
    tl = compute_quiz_time_limit_seconds(
        payload.content,
        len(questions),
        difficulty=(payload.difficulty or "normal").strip().lower()[:16],
        topic_labels=topics_set or None,
    )
    return QuizGenerateResponse(questions=questions, total_questions=len(questions), time_limit_seconds=tl)


# fn: generate_quiz_from_document | tr: POST /quiz/generate-from-document — pdf oturumundan adaptif quiz / en: POST adaptive quiz from pdf document session
@router.post("/generate-from-document", response_model=QuizGenerateResponse)
def generate_quiz_from_document(
    payload: QuizGenerateFromDocumentRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> QuizGenerateResponse:
    uid = require_user_id(authorization)
    raw_id = (payload.document_id or "").strip()
    log.info(
        "quiz generate-from-document: received document_id prefix=%s user_id=%s",
        raw_id[:16] if raw_id else "(empty)",
        uid,
    )
    if not raw_id:
        log.warning("quiz generate-from-document: rejected empty document_id")
        return _quiz_document_error_response("missing_document", QUIZ_DOC_MSG_MISSING)

    doc = get_document(raw_id)
    if not doc and db is not None:
        doc = try_restore_document_to_store(db, raw_id, uid)
        if doc:
            log.info(
                "quiz generate-from-document: rehydrated from DB prefix=%s",
                raw_id[:16],
            )
    if not doc:
        miss = document_missing_reason(raw_id) or "missing_document"
        log.warning(
            "quiz generate-from-document: document not in store prefix=%s classified=%s in_tombstone=%s",
            raw_id[:16],
            miss,
            miss == "expired_document",
        )
        msg = QUIZ_DOC_MSG_EXPIRED if miss == "expired_document" else QUIZ_DOC_MSG_MISSING
        return _quiz_document_error_response(miss, msg)

    log.info(
        "quiz generate-from-document: document found prefix=%s store_hit=True",
        raw_id[:16],
    )
    if doc.user_id != uid:
        raise HTTPException(status_code=403, detail="This document does not belong to the given user_id.")

    text = doc.study_text or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="No extractable text in this document.")
    num_questions = _auto_num_questions_from_text(
        text,
        payload.num_questions,
        focus_len=len(payload.focus_topics or []),
    )
    quiz_kind = str(getattr(payload, "quiz_kind", "standard") or "standard").strip().lower()[:32]
    mini_mode = quiz_kind == "mini_adaptive"
    if mini_mode:
        num_questions = max(1, min(num_questions, MAX_MINI_QUIZ_QUESTIONS))
    max_topics = max(1, min(payload.max_topics, 15))
    use_adaptive = bool(getattr(payload, "use_topic_adaptive", True))
    merged_focus = [str(x).strip() for x in (payload.focus_topics or []) if str(x).strip()][:12]
    merged_challenge = [str(x).strip() for x in (payload.challenge_topics or []) if str(x).strip()][:10]
    if quiz_kind == "mini_adaptive" and merged_focus:
        merged_challenge = _uniq_extend_topics(merged_challenge, merged_focus[:4], 10)

    raw_client_salt = int(getattr(payload, "variation_salt", 0) or 0) % 2_147_483_647
    user_mix = _stable_mix_u32(
        uid,
        raw_id[:64],
        quiz_kind,
        "|".join(merged_focus[:6]),
        "|".join(merged_challenge[:6]),
        payload.num_questions,
        payload.difficulty or "normal",
    )
    request_mix = _stable_mix_u32(uid, raw_id[:48], quiz_kind, raw_client_salt, time.time_ns())
    gen_salt = (raw_client_salt + user_mix + request_mix) % 2_147_483_647
    forbidden_extra: list[str] = []

    if db is not None and use_adaptive:
        try:
            doc_pk = resolve_document_pk(db, uid, raw_id)
            prof = build_quiz_generation_profile(
                db,
                uid,
                document_db_id=doc_pk,
                max_weak=max(8, min(num_questions + 4, 14)),
                max_strong=max(6, min(num_questions + 2, 10)),
                recent_stems_cap=140,
            )
            forbidden_extra = list(prof.recent_stems or [])[:140]
            merged_focus = _uniq_extend_topics(merged_focus, list(prof.weak_topics or []), 12)
            merged_challenge = _uniq_extend_topics(merged_challenge, list(prof.strong_topics or []), 10)
        except Exception:
            log.warning(
                "quiz generate-from-document: adaptive profile skipped prefix=%s",
                raw_id[:16],
                exc_info=True,
            )

    if mini_mode:
        merged_focus = _uniq_extend_topics([], merged_focus, 8)
        focus_keys = {x.strip().lower() for x in merged_focus if x and x.strip()}
        merged_challenge = [
            x
            for x in _uniq_extend_topics([], merged_challenge, 8)
            if x.strip().lower() not in focus_keys
        ][:8]
        if forbidden_extra:
            forbidden_extra = forbidden_extra[:180]

    try:
        questions = _generate_quiz_exact_batched(
            content=text,
            num_questions=num_questions,
            max_topics=max_topics,
            focus_topics=merged_focus or None,
            challenge_topics=merged_challenge or None,
            locale=(payload.locale or "en").strip().lower()[:8],
            difficulty=(payload.difficulty or "normal").strip().lower()[:16],
            document_id=raw_id,
            forbidden_stems=forbidden_extra or None,
            generation_salt=gen_salt,
        )
    except QuizGenerationError as e:
        raise HTTPException(status_code=503, detail=e.detail_tr) from e
    topics_set = {q.topic.strip() for q in questions if q.topic and q.topic.strip()}
    tl = compute_quiz_time_limit_seconds(
        text,
        len(questions),
        difficulty=(payload.difficulty or "normal").strip().lower()[:16],
        topic_labels=topics_set or None,
    )
    return QuizGenerateResponse(questions=questions, total_questions=len(questions), time_limit_seconds=tl)


# fn: submit_quiz | tr: POST /quiz/submit — cevapları puanla, db'ye kaydet, geri bildirim döndür / en: POST score answers save to db return feedback
@router.post("/submit", response_model=QuizSubmissionResponse)
def submit_quiz(
    payload: QuizSubmissionRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> QuizSubmissionResponse:
    if not payload.questions:
        raise HTTPException(status_code=400, detail="`questions` must not be empty.")
    if len(payload.questions) != len(payload.selected_answers):
        raise HTTPException(
            status_code=400,
            detail="`selected_answers` length must match `questions` length.",
        )

    loc = (payload.locale or "en").strip().lower()[:8]
    uid = require_user_id_matching(
        authorization, int(payload.user_id) if payload.user_id is not None else None
    )

    if db is None:
        return score_submission(
            payload.questions,
            payload.selected_answers,
            locale=loc,
            user_id=int(payload.user_id) if payload.user_id is not None else None,
            duration_seconds=payload.duration_seconds,
            quiz_source=payload.quiz_source,
        )

    try:
        return record_quiz_attempt_and_build_response(
            db=db,
            user_id=uid,
            questions=payload.questions,
            selected_answers=payload.selected_answers,
            locale=loc,
            document_id=payload.document_id,
            duration_seconds=payload.duration_seconds,
            quiz_source=payload.quiz_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not save your quiz to the database. Check DB connectivity and try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc) or "Quiz submission failed on the server.",
        ) from exc


# fn: get_user_quiz_history | tr: GET /quiz/history — kullanıcının quiz deneme geçmişi / en: GET user's persisted quiz attempt history
@router.get("/history", response_model=QuizHistoryListResponse)
def get_user_quiz_history(
    limit: int = 60,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> QuizHistoryListResponse:
    if db is None:
        return QuizHistoryListResponse(items=[])
    uid = require_user_id(authorization)
    return list_user_quiz_history(db, uid, limit=limit)


# fn: get_user_quiz_results | tr: GET /quiz/user-results — Results sekmesi ömür boyu analiz / en: GET Results tab lifetime aggregates and suggestions
@router.get("/user-results", response_model=QuizResultsPageResponse)
def get_user_quiz_results(
    locale: str = "en",
    authorization: Annotated[Optional[str], Header()] = None,
    db: Optional[Session] = Depends(get_db),
) -> QuizResultsPageResponse:
    if db is None:
        return QuizResultsPageResponse(has_data=False)
    uid = require_user_id(authorization)
    return build_user_results_page(db, uid, locale=locale or "en")


# fn: topic_performance | tr: POST /quiz/topic-performance — gönderimden konu analizi (db kaydı yok) / en: POST topic analysis from submission without db save
@router.post("/topic-performance", response_model=TopicAnalysisResponse)
def topic_performance(
    payload: QuizSubmissionRequest,
    authorization: Annotated[Optional[str], Header()] = None,
) -> TopicAnalysisResponse:
    require_user_id(authorization)
    try:
        loc = (payload.locale or "en").strip().lower()[:8]
        result = score_submission(
            payload.questions,
            payload.selected_answers,
            locale=loc,
            user_id=None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result.topic_analysis


# fn: quiz_suggestions | tr: POST /quiz/suggestions — quiz sonrası çalışma önerileri / en: POST post-quiz study suggestions
@router.post("/suggestions", response_model=SuggestionResponse)
def quiz_suggestions(
    payload: QuizSubmissionRequest,
    authorization: Annotated[Optional[str], Header()] = None,
) -> SuggestionResponse:
    try:
        loc = (payload.locale or "en").strip().lower()[:8]
        uid = require_user_id_matching(
            authorization, int(payload.user_id) if payload.user_id is not None else None
        )
        result = score_submission(
            payload.questions,
            payload.selected_answers,
            locale=loc,
            user_id=uid,
        )
        return build_suggestions(
            result,
            user_id=uid,
            output_locale=normalize_output_locale(payload.locale),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

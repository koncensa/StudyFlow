# svc: pdf_schema | tr: pdf chat, özet, quiz ve oturum api pydantic şemaları / en: pydantic schemas for pdf chat summary quiz and session api

from typing import Any, Dict, List, Literal, Optional

# tr: özet kalite seviyesi tip alias / en: summary quality level type alias
SummaryQualityLevel = Literal["good", "partial", "weak"]

from pydantic import BaseModel, Field


# schema: PdfChatMessage | tr: pdf sohbet mesajı (user veya assistant) / en: pdf chat message user or assistant
class PdfChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=32000)


# schema: PdfChatRequest | tr: pdf belgesiyle sohbet isteği / en: chat with pdf document request
class PdfChatRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)
    messages: List[PdfChatMessage] = Field(..., min_length=1, max_length=40)
    study_mode: Optional[str] = Field(
        None,
        description="tutor_chat | quick_summary | explain_simple | exam_focus | quiz_coach",
    )
    explain_level: Optional[str] = Field(None, description="Ignored; kept for API compatibility.")
    output_locale: Optional[str] = Field(
        "en",
        description="Preferred reply language (BCP-style short code).",
    )
    focus_topics: List[str] = Field(
        default_factory=list,
        description="Topics to reinforce (e.g. from weak quiz areas).",
    )
    challenge_topics: List[str] = Field(
        default_factory=list,
        description="Strong topics — nudge the model toward depth.",
    )


# schema: PdfChatResponse | tr: pdf sohbet yanıtı (tek reply metni) / en: pdf chat response single reply text
class PdfChatResponse(BaseModel):
    reply: str


# schema: PdfApplyModeRequest | tr: study modu uygula isteği (özet/tutor demo turu) / en: apply study mode request summary tutor demo turn
class PdfApplyModeRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)
    study_mode: str = Field(
        "tutor_chat",
        description="tutor_chat | quick_summary | explain_simple | exam_focus | quiz_coach",
    )
    mode: Optional[str] = Field(
        None,
        description="summary | explain | exam (preferred for dynamic summary pipeline)",
    )
    explain_level: str = Field("normal", description="Ignored; fixed profile server-side.")
    output_locale: str = Field("en", description="Reply language for the demo turn.")
    focus_topics: List[str] = Field(
        default_factory=list,
        description="Topics to reinforce for this Apply action.",
    )
    challenge_topics: List[str] = Field(
        default_factory=list,
        description="Strong topics — less hand-holding on basics.",
    )
    summary_style: Optional[str] = Field(
        "balanced",
        description="balanced | concise | detailed",
    )
    summary_format: Optional[str] = Field(
        "mixed",
        description="mixed | bullets | prose",
    )
    summary_length: Optional[str] = Field(
        "medium",
        description="short | medium | long",
    )


# schema: PdfApplyModeResponse | tr: mod uygulama yanıtı + pipeline teşhis alanları / en: apply mode response with pipeline diagnostics
class PdfApplyModeResponse(BaseModel):
    reply: str
    history_id: Optional[int] = None
    pipeline_duration_ms: Optional[float] = None
    pipeline_llm_calls: Optional[int] = None
    pipeline_used_map_reduce: Optional[bool] = None
    summary_quality: Optional[SummaryQualityLevel] = None
    summary_warning: Optional[str] = None
    used_fallback: Optional[bool] = None
    fallback_reason: Optional[str] = None
    summary_quality_warning: Optional[str] = None
    summary_generation_note: Optional[str] = None


# schema: PdfDynamicSummaryRequest | tr: dinamik özet (+ isteğe bağlı quiz) isteği / en: dynamic summary optional quiz request
class PdfDynamicSummaryRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)
    study_mode: str = Field(
        "quick_summary",
        description="tutor_chat | quick_summary | explain_simple | exam_focus | quiz_coach",
    )
    mode: Optional[str] = Field(
        None,
        description="summary | explain | exam (preferred)",
    )
    explain_level: str = Field("normal", description="Ignored; fixed profile server-side.")
    output_locale: str = Field("en", description="Preferred output language: en | tr")
    focus_topics: List[str] = Field(default_factory=list)
    challenge_topics: List[str] = Field(default_factory=list)
    summary_style: Optional[str] = Field(
        "balanced",
        description="balanced | concise | detailed",
    )
    summary_format: Optional[str] = Field(
        "mixed",
        description="mixed | bullets | prose",
    )
    summary_length: Optional[str] = Field(
        "medium",
        description="short | medium | long",
    )
    include_quiz: bool = Field(False)
    num_questions: int = Field(5, ge=1, le=15)
    max_topics: int = Field(6, ge=1, le=15)
    quiz_difficulty: Optional[str] = Field(None, description="Quiz-only; beginner | normal | technical.")
    quiz_kind: str = Field("standard", description="standard | mini_adaptive")


# schema: PdfDynamicSummaryResponse | tr: dinamik özet yanıtı + quiz + pipeline meta / en: dynamic summary response quiz and pipeline meta
class PdfDynamicSummaryResponse(BaseModel):
    summary: str
    mode: str
    explain_level: str
    summary_style: str
    summary_format: str
    summary_length: str
    history_id: Optional[int] = None
    quiz: Optional[Dict[str, Any]] = None
    pipeline_duration_ms: Optional[float] = None
    pipeline_llm_calls: Optional[int] = None
    pipeline_used_map_reduce: Optional[bool] = None
    pipeline_map_chunks_in: Optional[int] = None
    pipeline_map_chunks_after_quality: Optional[int] = None
    pipeline_map_chunks_skipped_quality: Optional[int] = None
    pipeline_map_chunks_out: Optional[int] = None
    pipeline_map_phase_ms: Optional[float] = None
    pipeline_cache_hit: Optional[bool] = None
    pipeline_fallback_path: Optional[str] = Field(
        None,
        description="Internal path: merged_map_digest | extractive | concat_chunks when degraded.",
    )
    summary_quality: Optional[SummaryQualityLevel] = Field(
        None,
        description="Coarse quality: good | partial | weak (heuristic + fallback path).",
    )
    summary_warning: Optional[str] = Field(
        None,
        description="Primary user-facing warning (quality + OCR coverage).",
    )
    used_fallback: Optional[bool] = Field(
        None,
        description="True when a non-standard path ran (timeouts, extractive text, map digest fallback, etc.).",
    )
    fallback_reason: Optional[str] = Field(
        None,
        description="Machine-oriented tags for the fallback path (pipe-separated).",
    )
    summary_quality_warning: Optional[str] = Field(
        None,
        description="Deprecated mirror of summary_warning for older clients.",
    )
    summary_generation_note: Optional[str] = Field(
        None,
        description="Plain-language note on how this summary was produced (retrieval, map, cache, fallbacks).",
    )


# schema: PdfSummaryHistoryItem | tr: tek kayıtlı özet geçmişi satırı / en: single saved summary history row
class PdfSummaryHistoryItem(BaseModel):
    id: int
    mode: str
    explain_level: str
    output_locale: str
    summary: str
    created_at: str


# schema: PdfSummaryHistoryRequest | tr: özet geçmişi listeleme isteği / en: list summary history request
class PdfSummaryHistoryRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)
    limit: int = Field(24, ge=1, le=100)


# schema: PdfSummaryHistoryResponse | tr: özet geçmişi listesi yanıtı / en: summary history list response
class PdfSummaryHistoryResponse(BaseModel):
    items: List[PdfSummaryHistoryItem] = Field(default_factory=list)


# schema: StudyOutcomeRequest | tr: study outcome (sınav/quick/explain) isteği / en: study outcome exam quick explain request
class StudyOutcomeRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)
    outcome: Literal["exam_focus", "quick_summary", "explain_overview"] = "exam_focus"
    explain_level: Optional[str] = Field("normal", description="Ignored for summaries; API compatibility.")


# schema: StudyOutcomeResponse | tr: study outcome içerik yanıtı / en: study outcome content response
class StudyOutcomeResponse(BaseModel):
    outcome: str
    content: str


# schema: PdfDocumentStatusRequest | tr: belge durumu sorgu isteği / en: document status check request
class PdfDocumentStatusRequest(BaseModel):
    document_id: str = Field(..., min_length=8, max_length=64)
    user_id: int = Field(1, ge=1, le=2_000_000_000)


# schema: PdfDocumentStatusResponse | tr: belge mevcut mu / süresi doldu mu yanıtı / en: document ok expired wrong user response
class PdfDocumentStatusResponse(BaseModel):
    ok: bool
    error: Optional[str] = Field(
        None,
        description="missing_document | expired_document | wrong_user when ok is false",
    )
    message: Optional[str] = None


# schema: PdfSessionHistoryMessage | tr: oturum geçmişi tek mesaj / en: session history single message
class PdfSessionHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


# schema: PdfSessionHistoryItem | tr: kullanıcı pdf sohbet oturumu özeti / en: user pdf chat session summary item
class PdfSessionHistoryItem(BaseModel):
    key: str
    document_id: str
    filename: str
    messages: List[PdfSessionHistoryMessage] = Field(default_factory=list)
    updated_at: str = ""


# schema: PdfSessionHistoryResponse | tr: pdf sohbet oturumları listesi / en: pdf chat sessions list response
class PdfSessionHistoryResponse(BaseModel):
    items: List[PdfSessionHistoryItem] = Field(default_factory=list)

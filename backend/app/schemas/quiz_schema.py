# svc: quiz_schema | tr: quiz üretim, submit ve geri bildirim api pydantic şemaları / en: pydantic schemas for quiz generation submit and feedback api

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from pydantic import AliasChoices, BaseModel, Field, computed_field, field_validator, model_validator

from app.schemas.topic_schema import TopicAnalysisResponse

# tr: izin verilen soru tipleri / en: allowed question types
_ALLOWED_QUESTION_TYPES = frozenset(
    {
        "definition",
        "concept",
        "comparison",
        "application",
        "interpretation",
        "formula",
    }
)

#quiz sorusu üret
# schema: QuizQuestion | tr: tek çoktan seçmeli soru (kök, şıklar, doğru cevap, konu) / en: single mcq question stem options answer topic
class QuizQuestion(BaseModel):
    """
    A single MCQ question tied to a topic.
    """

    model_config = {"populate_by_name": True}

    id: str = Field(
        default="",
        max_length=48,
        description="Stable id for UI state and analytics (filled when missing).",
        validation_alias=AliasChoices("id", "question_id"),
    )
    question_text: str = Field(validation_alias=AliasChoices("question_text", "question"))
    options: List[str]
    correct_answer: str = Field(validation_alias=AliasChoices("correct_answer", "correctAnswer"))
    topic: str
    explanation: Optional[str] = None
    source_section: Optional[str] = None
    question_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("type", "question_type"),
        serialization_alias="type",
        description="definition | concept | comparison | application",
    )
    difficulty: Optional[str] = Field(
        default=None,
        description="Per-item difficulty: beginner | normal | technical (mirrors session preset when unset).",
    )

    # fn: short_explanation | tr: explanation alias (computed) / en: explanation alias computed field
    @computed_field
    @property
    def short_explanation(self) -> Optional[str]:
        return self.explanation

    # fn: _coerce_incoming_aliases | tr: eski alan adlarını normalize et, eksik id üret / en: normalize legacy field names generate missing id
    @model_validator(mode="before")
    @classmethod
    def _coerce_incoming_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("question") is not None and not data.get("question_text"):
                data["question_text"] = data.get("question")
            if data.get("correctAnswer") is not None and data.get("correct_answer") is None:
                data["correct_answer"] = data.get("correctAnswer")
            if data.get("short_explanation") and not data.get("explanation"):
                data["explanation"] = data["short_explanation"]
            rid = str(data.get("id") or data.get("question_id") or "").strip()
            if len(rid) < 8:
                qt = str(data.get("question_text") or data.get("question") or "")
                ca = str(data.get("correct_answer") or data.get("correctAnswer") or "")
                top = str(data.get("topic") or "")
                base = f"{top}|{qt}|{ca}"
                hid = hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()[:20]
                data["id"] = f"q_{hid}"
            else:
                data["id"] = rid[:48]
        return data

    # fn: _coerce_question_type | tr: soru tipini izin verilen tipe normalize et / en: normalize question type to allowed set
    @field_validator("question_type", mode="before")
    @classmethod
    def _coerce_question_type(cls, v: object) -> Optional[str]:
        if v is None or v == "":
            return None
        s = str(v).strip().lower()
        if s in ("interpretation", "formula", "scenario", "use_case"):
            return "application"
        if s in ("concept_understanding", "understanding"):
            return "concept"
        return s if s in _ALLOWED_QUESTION_TYPES else None


# schema: QuizGenerateRequest | tr: ham metinden quiz üretme isteği / en: generate quiz from raw text request
class QuizGenerateRequest(BaseModel):
    content: str
    num_questions: int = Field(5, ge=1, le=15)
    max_topics: int = Field(5, ge=1, le=15)
    focus_topics: List[str] = Field(default_factory=list)
    challenge_topics: List[str] = Field(default_factory=list)
    locale: str = Field("en", description="en | tr — question language")
    difficulty: str = Field(
        "normal",
        description="beginner | normal | technical — affects question depth",
    )


# schema: QuizGenerateFromDocumentRequest | tr: pdf belgesinden quiz üretme isteği / en: generate quiz from pdf document request
class QuizGenerateFromDocumentRequest(BaseModel):
    document_id: str
    user_id: int = 1
    num_questions: int = Field(5, ge=1, le=15)
    max_topics: int = Field(5, ge=1, le=15)
    focus_topics: List[str] = Field(default_factory=list)
    challenge_topics: List[str] = Field(default_factory=list)
    locale: str = Field("en", description="en | tr")
    difficulty: str = Field("normal", description="beginner | normal | technical")
    use_topic_adaptive: bool = Field(
        True,
        description="When DB is available, merge weak/strong lifetime topics into focus/challenge lists.",
    )
    quiz_kind: str = Field(
        "standard",
        description="standard | mini_adaptive — mini uses tighter topic focus and up to 15 questions.",
    )
    variation_salt: int = Field(
        0,
        ge=0,
        le=2_147_483_647,
        description="Client nonce per generation request — rotates topic/question order and excerpt window so repeated quizzes differ.",
    )


# schema: QuizGenerateResponse | tr: üretilen sorular + süre limiti yanıtı / en: generated questions and time limit response
class QuizGenerateResponse(BaseModel):
    questions: List[QuizQuestion]
    total_questions: int
    time_limit_seconds: int = Field(
        0,
        ge=0,
        description="Suggested quiz duration (seconds) from document length, difficulty, and question count.",
    )


# schema: QuizSubmissionRequest | tr: quiz cevaplarını gönderme isteği / en: submit quiz answers request
class QuizSubmissionRequest(BaseModel):
    """
    Simple submission format (no DB yet):
    - frontend sends the quiz questions it generated
    - frontend sends the user's selected answers in the same order
    """

    questions: List[QuizQuestion]
    selected_answers: List[str]
    user_id: int = 1
    locale: str = "en"
    document_id: Optional[str] = Field(
        default=None,
        description="Active PDF document_id when quiz was taken; scopes analytics per material.",
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=86400,
        description="Client-measured time from first question shown to submit (wall clock).",
    )
    quiz_source: Optional[str] = Field(
        default=None,
        description="pdf_session | text_summary — for analytics only.",
    )


# schema: TopicSnapshotLine | tr: öğrenme özeti için tek konu satırı / en: single topic row for learning snapshot
class TopicSnapshotLine(BaseModel):
    """One topic row for the learning snapshot (this quiz only)."""

    topic: str
    correct_count: int
    total_attempts: int
    success_rate: float = Field(description="0..1")
    band_label: str = Field(description="Localized weak/mid/strong label")
    coaching_line: str = Field(description="One short next-step line for this topic")


# schema: QuizLearningBrief | tr: quiz sonrası zayıf/gelişen/güçlü özet ve sonraki adımlar / en: post-quiz weak developing strong summary and next steps
class QuizLearningBrief(BaseModel):
    """Adaptive summary: where you are weak/mid/strong and what to do next."""

    headline: str
    weak_topics: List[TopicSnapshotLine] = Field(default_factory=list)
    developing_topics: List[TopicSnapshotLine] = Field(default_factory=list)
    strong_topics: List[TopicSnapshotLine] = Field(default_factory=list)
    what_to_do_next: List[str] = Field(default_factory=list)
    mini_quiz_hint: str = ""
    resource_hints: List[str] = Field(default_factory=list)
    error_pattern_summary: str = Field(
        default="",
        description="One short line summarizing dominant mistake type on this attempt.",
    )


# schema: QuizQuestionFeedback | tr: soru başına sonuç ve yanlış cevap koçluğu / en: per-question outcome and wrong-answer coaching
class QuizQuestionFeedback(BaseModel):
    """Per-question outcome; wrong answers include coaching fields."""

    question_index: int = Field(ge=0)
    topic: str
    question_text: Optional[str] = Field(
        default=None,
        description="Stem text for review UI (all wrong answers kept).",
    )
    is_correct: bool
    is_unanswered: bool = Field(
        default=False,
        description="True when the learner submitted with no option selected for this item.",
    )
    selected_answer: str
    correct_answer: str
    question_type: Optional[str] = Field(
        default=None,
        description="definition | concept | comparison | application (from the question)",
    )
    error_type: Optional[str] = Field(
        default=None,
        description="concept_mixup | formula_mixup | careless | interpretation",
    )
    confused_concepts: List[str] = Field(default_factory=list)
    why_wrong: Optional[str] = None
    teaching_snippet: Optional[str] = None
    your_mistake: Optional[str] = Field(
        default=None,
        description="What the learner picked and the mistake category (specific, not generic).",
    )
    why_incorrect: Optional[str] = Field(
        default=None,
        description="Why the selected option fails to satisfy the stem (no vague 'misread' excuses).",
    )
    correct_thinking: Optional[str] = Field(
        default=None,
        description="How to approach the stem / discriminate options to reach the key.",
    )
    correct_answer_explained: Optional[str] = Field(
        default=None,
        description="Correct option plus why it satisfies the question.",
    )
    hint: Optional[str] = None
    teach_back_prompt: Optional[str] = Field(
        default=None,
        description="Short active-learning prompt (explain in one sentence, what you mixed up, etc.).",
    )

    # fn: user_answer | tr: selected_answer alias (computed) / en: selected_answer alias computed field
    @computed_field
    @property
    def user_answer(self) -> str:
        return self.selected_answer


# schema: UserQuizMemory | tr: kalıcı denemelerden ömür boyu quiz hafızası / en: lifetime quiz memory from persisted attempts
class UserQuizMemory(BaseModel):
    """Rolling memory from persisted attempts (when DB is enabled)."""

    total_quiz_count: int = 0
    average_quiz_score: Optional[float] = None
    weak_topics_lifetime: List[str] = Field(default_factory=list)
    strong_topics_lifetime: List[str] = Field(default_factory=list)


# schema: QuizSubmissionResponse | tr: submit sonrası skor, analiz ve geri bildirim yanıtı / en: post-submit score analysis and feedback response
class QuizSubmissionResponse(BaseModel):
    attempt_id: Optional[int] = None

    total_correct: int
    total_wrong: int
    total_unanswered: int = Field(
        default=0,
        ge=0,
        description="Items with no selected answer at submit time.",
    )
    total_question_count: int = Field(
        default=0,
        ge=0,
        description="Same as len(questions) for this attempt.",
    )
    total_duration_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Echo of client duration when provided.",
    )
    quiz_source: Optional[str] = Field(default=None, description="pdf_session | text_summary")
    score_percentage: float
    performance_comment: Optional[str] = Field(
        default=None,
        description="Short, score-tier coaching line (locale-aware).",
    )
    recommended_mini_quiz_count: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Suggested next mini-quiz size from overall score (<40 → 5, <70 → 3, else 1).",
    )
    topic_analysis: TopicAnalysisResponse
    question_feedback: List[QuizQuestionFeedback] = Field(default_factory=list)
    error_type_summary: Dict[str, int] = Field(default_factory=dict)
    confused_topics_ranked: List[str] = Field(
        default_factory=list,
        description="Topics ordered by wrong-count on this attempt.",
    )
    follow_up_actions: List[str] = Field(
        default_factory=list,
        description="Short, user-specific next steps based on this quiz.",
    )
    user_quiz_memory: Optional[UserQuizMemory] = None
    learning_brief: Optional[QuizLearningBrief] = Field(
        default=None,
        description="Weak/mid/strong snapshot and personalized next steps for this attempt.",
    )
    listed_wrong_count: int = Field(
        default=0,
        ge=0,
        description="Count of answered-wrong items (excludes unanswered blanks).",
    )
    wrong_items: List[QuizQuestionFeedback] = Field(
        default_factory=list,
        description="Entries where the learner picked a wrong option (not blank).",
    )
    weak_topics: List[str] = Field(
        default_factory=list,
        description="Very weak + weak topic names from topic_analysis (shortcut for UI).",
    )
    suggested_mini_quiz_topic: Optional[str] = Field(
        default=None,
        description="Preferred focus topic for the next mini quiz (most-missed wrong topic, else first weak).",
    )
    teach_back_tasks: List[str] = Field(
        default_factory=list,
        description="Aggregated teach-back prompts for incorrect items.",
    )


# fn: quiz_bundle_for_pdf_json | tr: pdf upload yanıtı için quiz json paketi / en: quiz json bundle for pdf upload response
def quiz_bundle_for_pdf_json(questions: Sequence[QuizQuestion]) -> Dict[str, Any]:
    """
    JSON payload for ``POST /pdf/upload-pdf`` when ``create_quiz`` is true.

    Matches the frontend ``PdfAssistantUploadResponse.quiz`` shape
    (``questions`` + ``total_questions`` only; omits redundant ``short_explanation``).
    """
    items = list(questions)
    serialized = [
        q.model_dump(mode="json", by_alias=True, exclude={"short_explanation"})
        for q in items
    ]
    return {"questions": serialized, "total_questions": len(items)}

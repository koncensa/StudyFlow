# svc: suggestion_schema | tr: quiz sonrası öneri, koçluk paketi ve adaptif plan pydantic şemaları / en: pydantic schemas for post-quiz suggestions coaching pack and adaptive plan

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


# schema: SuggestedNextQuiz | tr: ui'nin başlatabileceği somut sonraki quiz planı / en: concrete next quiz plan ui can start
class SuggestedNextQuiz(BaseModel):
    """Concrete next quiz the UI can start (counts follow overall score band)."""

    num_questions: int = 5
    difficulty: str = "normal"
    focus_topics: List[str] = Field(default_factory=list)
    rationale: str = ""
    short_recap: str = Field(
        default="",
        description="For mid scores: a short recap paragraph; empty for other bands.",
    )


# schema: TopicStudyContent | tr: zayıf konu için hazır çalışma materyali / en: ready-to-use study material for one weak topic
class TopicStudyContent(BaseModel):
    """Ready-to-use study material for one weak topic (<50% on this quiz)."""

    topic: str
    success_rate_pct: int = 0
    weak_under_half: bool = True
    what_went_wrong: str = ""
    why_hint: str = ""
    pdf_excerpt: str = ""
    topic_summary: str = ""
    simple_explanation: str = ""
    example_question: str = ""
    example_answer_hint: str = ""
    micro_task: str = ""
    mini_quiz_outline: str = ""


# schema: TopicScoreRow | tr: results ekranı konu başarı yüzdesi satırı / en: results screen topic success percent row
class TopicScoreRow(BaseModel):
    """One row for Results: topic label → success % on this quiz."""

    topic: str
    success_pct: int = Field(ge=0, le=100)
    wrong_count: int = 0
    total_attempts: int = 0


# schema: RecommendedMiniQuiz | tr: zayıf konuya odaklı mini quiz önerisi / en: weak-topic focused mini quiz recommendation
class RecommendedMiniQuiz(BaseModel):
    """Weak-topic mini quiz size scales with overall score; focus is user-specific."""

    num_questions: int = 3
    focus_topics: List[str] = Field(default_factory=list)
    caption: str = ""


# schema: StudyResourceTip | tr: konu bazlı sonraki adım ipuçları (özet, pdf, pomodoro) / en: per-topic next-step hints summary pdf pomodoro
class StudyResourceTip(BaseModel):
    """Structured next-step hints after a quiz (summary, explain, PDF, mini quiz, Pomodoro)."""

    topic: str = ""
    short_summary: str = ""
    explain_action: str = ""
    pdf_section_hint: str = ""
    mini_quiz_hint: str = ""
    pomodoro_minutes: int = 25


# schema: QuizCoachingPack | tr: yanlış analizi + eylem planı + konu içerikleri paketi / en: wrong analysis action plan and topic content pack
class QuizCoachingPack(BaseModel):
    """
    Analysis + actionable outputs: wrong/why/next steps, banded quiz plan, per-topic content.
    """

    global_band: str = Field(
        default="building",
        description="critical (0–40%) | building (40–70%) | strong (70%+)",
    )
    overall_what_wrong: str = ""
    overall_why: str = ""
    overall_next_steps: str = ""
    suggested_quiz: SuggestedNextQuiz = Field(default_factory=SuggestedNextQuiz)
    topic_contents: List[TopicStudyContent] = Field(default_factory=list)
    topic_score_rows: List[TopicScoreRow] = Field(default_factory=list)
    weakest_topic: str = ""
    top_error_type: str = ""
    top_error_hint: str = ""
    natural_feedback_lines: List[str] = Field(default_factory=list)
    personalized_tip_lines: List[str] = Field(default_factory=list)
    study_plan_steps: List[str] = Field(default_factory=list)
    recommended_mini_quiz: RecommendedMiniQuiz = Field(default_factory=RecommendedMiniQuiz)
    resource_tips: List[StudyResourceTip] = Field(
        default_factory=list,
        description="Per-topic study pack: recap, explain, PDF anchor, mini quiz, Pomodoro.",
    )


# schema: AdaptiveStudyPlan | tr: adaptif ui için makine okunur plan kancaları / en: machine-readable hooks for adaptive ui plan
class AdaptiveStudyPlan(BaseModel):
    """
    Machine-readable hooks for adaptive UI (quiz focus, resources, difficulty hints).
    """

    remedial_topics: List[str] = Field(default_factory=list)
    challenge_topics: List[str] = Field(default_factory=list)
    resource_queries: List[str] = Field(default_factory=list)
    next_quiz_focus: List[str] = Field(default_factory=list)
    narrative: str = ""
    next_step_title: Optional[str] = Field(
        default=None,
        description="Short headline for 'what to study next' on Results.",
    )
    next_step_why: Optional[str] = Field(
        default=None,
        description="Why this step is recommended (ties to weak topics / mistakes).",
    )


# schema: SuggestionAction | tr: tek önerilen eylem (review, mini_quiz, pomodoro vb.) / en: single suggested action review mini_quiz pomodoro etc
class SuggestionAction(BaseModel):
    """One suggested next step (review, mini_quiz, new_quiz, pomodoro, resource, maintenance)."""

    action: str
    message: str
    pomodoro_minutes: Optional[int] = None
    resource_query: Optional[str] = None

    # fn: _normalize_message | tr: message alanını tuple/boş değerden stringe normalize et / en: normalize message field from tuple or empty to string
    @field_validator("message", mode="before")
    @classmethod
    def _normalize_message(cls, v: Any) -> str:
        """Guard against accidental 1-tuples from trailing commas after ternary strings."""
        if isinstance(v, tuple):
            parts = [str(x).strip() for x in v if x is not None and str(x).strip()]
            return " ".join(parts) if parts else ""
        if v is None:
            return ""
        return str(v).strip()


# schema: TopicSuggestion | tr: konu bandı + o konuya özel eylem listesi / en: topic band plus actions for that topic
class TopicSuggestion(BaseModel):
    topic: str
    status: str = "weak"
    success_rate: float = 0.0
    actions: List[SuggestionAction] = Field(default_factory=list)


# schema: SuggestionResponse | tr: quiz sonrası tam öneri yanıtı (ana giriş) / en: full post-quiz suggestion response main entry
class SuggestionResponse(BaseModel):
    very_weak_topics: List[str] = Field(default_factory=list)
    weak_topics: List[str]
    moderate_topics: List[str] = Field(default_factory=list)
    developing_topics: List[str] = Field(default_factory=list)
    good_topics: List[str] = Field(default_factory=list)
    strong_topics: List[str] = Field(default_factory=list)
    topics: List[TopicSuggestion] = Field(default_factory=list)
    general_actions: List[SuggestionAction] = Field(default_factory=list)
    coach_message: Optional[str] = None
    user_id: int = 1
    adaptive_plan: Optional[AdaptiveStudyPlan] = None
    coaching_pack: Optional[QuizCoachingPack] = None

# svc: quiz_results_schema | tr: quiz sonuç sayfası ve geçmiş listesi api pydantic şemaları / en: pydantic schemas for quiz results page and history list api

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.topic_schema import TopicAnalysisResponse
from app.schemas.quiz_schema import QuizLearningBrief, QuizQuestionFeedback, UserQuizMemory
from app.schemas.suggestion_schema import SuggestionResponse


# schema: TopicProgressItem | tr: konu bazlı ilerleme (önceki vs son quiz) / en: topic progress comparing previous vs latest quiz
class TopicProgressItem(BaseModel):
    """Compare same topic between the latest quiz and the previous one."""

    topic: str
    previous_success_rate: Optional[float] = Field(
        default=None,
        description="Prior attempt success rate 0–1 for this topic, if any.",
    )
    current_success_rate: float = Field(description="Latest attempt success rate 0–1.")
    delta_points: Optional[float] = Field(
        default=None,
        description="Change in success percentage points (e.g. 40 means +40%).",
    )
    trend: str = Field(description="improved | declined | new | stable")


# schema: QuizHistoryListItem | tr: geçmiş quiz listesinde tek satır / en: single row in quiz history list
class QuizHistoryListItem(BaseModel):
    """One row in GET /quiz/history for the signed-in user."""

    id: str
    attempt_id: int
    solved_at_iso: str
    quiz_kind: str = Field(description="standard | mini_adaptive")
    quiz_source: str = Field(description="pdf_session | text_summary | other")
    score: float = 0.0
    correct: int = 0
    wrong: int = 0
    unanswered: int = 0
    total_questions: int = 0
    duration_seconds: Optional[int] = None
    recommended_mini_count: int = 3
    suggested_mini_quiz_topic: Optional[str] = None
    focus_topics: List[str] = Field(default_factory=list)
    recommendation_lines: List[str] = Field(default_factory=list)
    topic_rows: List[dict] = Field(default_factory=list)
    error_summary: Dict[str, int] = Field(default_factory=dict)


# schema: QuizHistoryListResponse | tr: quiz geçmişi listesi yanıtı / en: quiz history list response
class QuizHistoryListResponse(BaseModel):
    items: List[QuizHistoryListItem] = Field(default_factory=list)


# schema: QuizResultsPageResponse | tr: results sekmesi tam yük (son quiz + ömür boyu + öneriler) / en: results tab full payload latest lifetime suggestions
class QuizResultsPageResponse(BaseModel):
    """
    Results tab: last-quiz coaching, lifetime aggregates, progress, and suggestions.
    """

    has_data: bool = False
    attempt_id: Optional[int] = None
    document_id: Optional[str] = Field(
        default=None,
        description="PDF document_id for the latest attempt, when recorded.",
    )
    total_correct: int = 0
    total_wrong: int = 0
    total_unanswered: int = Field(default=0, ge=0)
    total_question_count: int = Field(default=0, ge=0)
    total_duration_seconds: Optional[int] = Field(default=None, ge=0)
    score_percentage: float = 0.0
    performance_comment: Optional[str] = None
    recommended_mini_quiz_count: int = Field(default=3, ge=1, le=10)
    # tr: son deneme konu analizi / en: latest attempt topic analysis
    topic_analysis: Optional[TopicAnalysisResponse] = None
    # tr: tüm zamanlar konu toplamları / en: all-time per-topic totals
    lifetime_topic_analysis: Optional[TopicAnalysisResponse] = None
    topic_progress: List[TopicProgressItem] = Field(default_factory=list)
    question_feedback: List[QuizQuestionFeedback] = Field(default_factory=list)
    error_type_summary: Dict[str, int] = Field(default_factory=dict)
    confused_topics_ranked: List[str] = Field(default_factory=list)
    user_quiz_memory: Optional[UserQuizMemory] = None
    learning_brief: Optional[QuizLearningBrief] = None
    suggestions: Optional[SuggestionResponse] = None
    listed_wrong_count: int = Field(default=0, ge=0)
    wrong_items: List[QuizQuestionFeedback] = Field(default_factory=list)
    weak_topics: List[str] = Field(default_factory=list)
    suggested_mini_quiz_topic: Optional[str] = None

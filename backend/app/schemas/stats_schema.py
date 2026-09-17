# svc: stats_schema | tr: ilerleme/stats paneli toplu çalışma istatistikleri pydantic şemaları / en: pydantic schemas for progress stats panel aggregated study metrics

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# schema: PomodoroSessionSummary | tr: stats ui için tek tamamlanmış pomodoro satırı / en: single completed pomodoro row for stats ui
class PomodoroSessionSummary(BaseModel):
    """One completed Pomodoro row for the Progress / stats UI."""

    id: int
    topic: Optional[str] = None
    duration_seconds: int = 0
    start_time: str = ""
    end_time: Optional[str] = None


# schema: StudyStatsProofQuizAttempt | tr: aggregate hesap kanıtı için tek quiz denemesi / en: single quiz attempt row proving aggregate math
class StudyStatsProofQuizAttempt(BaseModel):
    """One quiz attempt row used to prove aggregate calculations."""

    scoped_id: str
    attempt_id: int
    total_questions: int
    total_correct: int
    total_wrong: int
    total_unanswered: int
    score_percentage: Optional[float] = None


# schema: StudyStatsProofPomodoroSession | tr: süre toplamı kanıtı için tek pomodoro oturumu / en: single pomodoro session row proving time rollups
class StudyStatsProofPomodoroSession(BaseModel):
    """One completed Pomodoro row used to prove time rollups."""

    scoped_id: str
    session_id: int
    topic: Optional[str] = None
    duration_seconds: int
    end_time: Optional[str] = None


# schema: StudyStatsCalculationProof | tr: progress panelindeki formüllerin makine okunur kanıtı / en: machine-readable proof for progress panel formulas
class StudyStatsCalculationProof(BaseModel):
    """Machine-readable evidence for every Progress panel formula."""

    formula_total_focus_time: str
    formula_avg_attempt_score: str
    formula_lifetime_accuracy: str
    pomodoro_completed_sessions_count: int
    pomodoro_total_seconds_from_sessions: int
    pomodoro_total_seconds_from_topic_rollup: int
    pomodoro_sample_limit: int
    pomodoro_sample_truncated: bool = False
    pomodoro_sample_sessions: List[StudyStatsProofPomodoroSession] = Field(default_factory=list)
    quiz_attempts_count: int
    quiz_correct_numerator: int
    quiz_accuracy_denominator: int
    quiz_lifetime_accuracy_percent: Optional[float] = None
    quiz_avg_score_sum_percent: float
    quiz_avg_score_attempt_count: int
    quiz_avg_score_percent: Optional[float] = None
    quiz_attempt_sample_limit: int
    quiz_attempt_sample_truncated: bool = False
    quiz_attempt_sample: List[StudyStatsProofQuizAttempt] = Field(default_factory=list)


# schema: StudyStatsResponse | tr: stats/ilerleme paneli ana yanıt (pomodoro + quiz ömür boyu) / en: stats progress panel main response pomodoro plus quiz lifetime
class StudyStatsResponse(BaseModel):
    user_id: int
    total_study_time_seconds: int
    pomodoro_sessions_count: int
    total_quiz_count: int
    average_quiz_score: Optional[float]
    weak_topics: List[str]
    strong_topics: List[str]
    # tr: tüm quiz denemelerinde soru bazlı toplamlar / en: lifetime sums across all stored quiz attempts
    quiz_sum_correct: int = 0
    quiz_sum_wrong: int = 0
    quiz_sum_unanswered: int = 0
    quiz_sum_question_slots: int = 0
    quiz_lifetime_accuracy_percent: Optional[float] = None
    # tr: pomodoro konu dağılımı + son oturumlar / en: pomodoro topic breakdown plus recent sessions
    pomodoro_by_topic_seconds: Dict[str, int] = Field(default_factory=dict)
    pomodoro_recent_sessions: List[PomodoroSessionSummary] = Field(default_factory=list)
    # tr: ui'da gösterilen her metrik için hesap kanıtı / en: calculation proof for each stat on progress panel
    calculation_proof: Optional[StudyStatsCalculationProof] = None

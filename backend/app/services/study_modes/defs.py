from __future__ import annotations

from typing import Literal

StudyMode = Literal[
    "tutor_chat",
    "quick_summary",
    "explain_simple",
    "exam_focus",
    "quiz_coach",
]

ExplainLevel = Literal["beginner", "normal", "technical"]


def parse_mode(raw: str | None) -> StudyMode:
    v = (raw or "tutor_chat").strip().lower().replace("-", "_")
    allowed: tuple[StudyMode, ...] = (
        "tutor_chat",
        "quick_summary",
        "explain_simple",
        "exam_focus",
        "quiz_coach",
    )
    return v if v in allowed else "tutor_chat"


def parse_level(raw: str | None) -> ExplainLevel:
    v = (raw or "normal").strip().lower()
    if v in ("beginner", "easy", "simple"):
        return "beginner"
    if v in ("technical", "advanced", "expert"):
        return "technical"
    return "normal"

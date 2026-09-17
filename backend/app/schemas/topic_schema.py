# svc: topic_schema | tr: quiz konu bazlı performans ve bant analizi pydantic şemaları / en: pydantic schemas for per-topic quiz performance and band analysis

from typing import List

from pydantic import BaseModel, Field


# schema: TopicPerformance | tr: tek konunun doğru/yanlış sayıları ve başarı bandı / en: one topic correct wrong counts and success band
class TopicPerformance(BaseModel):
    topic: str
    correct_count: int
    wrong_count: int
    total_attempts: int
    success_rate: float
    # tr: very_weak | weak | developing | good | strong (success_rate bantları) / en: very_weak | weak | developing | good | strong (success_rate bands)
    status: str
    # tr: success_rate < 0.5 ise true (koçluk için katı zayıf konu) / en: true when success_rate < 0.5 (strict weak topic for coaching)
    is_weak_under_half: bool = False


# schema: TopicAnalysisResponse | tr: quiz denemesinin tüm konu analizi özeti / en: full topic analysis summary for one quiz attempt
class TopicAnalysisResponse(BaseModel):
    topics: List[TopicPerformance]
    topics_under_half: List[str] = Field(
        default_factory=list,
        description="Topics with success rate strictly below 50% on this attempt/view.",
    )
    very_weak_topics: List[str] = Field(
        default_factory=list,
        description="Topics with success rate under 40% on this view.",
    )
    weak_topics: List[str]
    developing_topics: List[str] = Field(default_factory=list)
    good_topics: List[str] = Field(default_factory=list)
    strong_topics: List[str]
    # tr: developing_topics ile aynı (eski api uyumluluğu) / en: same as developing_topics (legacy api compatibility)
    moderate_topics: List[str] = Field(default_factory=list)

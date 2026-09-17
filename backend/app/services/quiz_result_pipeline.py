# svc: quiz_result_pipeline | tr: quiz cevaplarından konu bazlı analiz ve başarı bandı üret / en: per-topic analysis and success bands from quiz answers

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.schemas.quiz_schema import QuizQuestion
from app.schemas.topic_schema import TopicAnalysisResponse, TopicPerformance

# tr: başarı oranı eşikleri (0..1, üst sınır hariç) / en: success rate thresholds (0..1, upper bound exclusive)
VERY_WEAK_MAX = 0.40
WEAK_MAX = 0.60
DEVELOPING_MAX = 0.75
GOOD_MAX = 0.90

# tr: ömür boyu istatistikler için zayıf/güçlü eşikleri / en: lifetime stats weak/strong thresholds
LIFETIME_WEAK_BELOW = 0.60
LIFETIME_STRONG_FROM = 0.75


# fn: classify_topic_band | tr: başarı oranını banda çevir (very_weak … strong) / en: map success rate to band (very_weak … strong)
def classify_topic_band(success_rate: float) -> str:
    r = float(success_rate)
    if r < VERY_WEAK_MAX:
        return "very_weak"
    if r < WEAK_MAX:
        return "weak"
    if r < DEVELOPING_MAX:
        return "developing"
    if r < GOOD_MAX:
        return "good"
    return "strong"


# fn: topic_band_status | tr: classify_topic_band ile aynı (db kayıtları için) / en: alias of classify_topic_band (for db rows)
def topic_band_status(success_rate: float) -> str:
    return classify_topic_band(success_rate)


# fn: _accumulate_per_topic | tr: konu başına doğru/yanlış/boş sayılarını topla / en: accumulate correct/wrong/blank counts per topic
def _accumulate_per_topic(
    questions: List[QuizQuestion],
    correctness: List[bool],
    selected_answers: Optional[List[str]] = None,
) -> Dict[str, Dict[str, int]]:
    per_topic: Dict[str, Dict[str, int]] = {}
    for i, (q, is_correct) in enumerate(zip(questions, correctness)):
        topic = (q.topic or "General").strip() or "General"
        if topic not in per_topic:
            per_topic[topic] = {"correct": 0, "wrong": 0, "total": 0}
        per_topic[topic]["total"] += 1
        if is_correct:
            per_topic[topic]["correct"] += 1
            continue
        blank = False
        if selected_answers is not None and i < len(selected_answers):
            blank = not (str(selected_answers[i] or "").strip())
        if blank:
            # tr: boş cevap toplamda sayılır, yanlış sayısına eklenmez / en: blank counts in total, not in wrong
            continue
        per_topic[topic]["wrong"] += 1
    return per_topic


# fn: build_topic_analysis | tr: tam konu analizi nesnesi oluştur / en: build full topic analysis response
def build_topic_analysis(
    questions: List[QuizQuestion],
    correctness: List[bool],
    selected_answers: Optional[List[str]] = None,
) -> TopicAnalysisResponse:
    per_topic = _accumulate_per_topic(questions, correctness, selected_answers)

    def _sort_key(topic: str) -> Tuple[float, str]:
        b = per_topic[topic]
        tot = int(b["total"])
        sr = (int(b["correct"]) / tot) if tot else 0.0
        return (sr, topic.lower())

    topics_sorted = sorted(per_topic.keys(), key=_sort_key)

    very_weak: List[str] = []
    weak: List[str] = []
    developing: List[str] = []
    good: List[str] = []
    strong: List[str] = []
    under_half: List[str] = []
    performance: List[TopicPerformance] = []

    for topic in topics_sorted:
        bucket = per_topic[topic]
        correct = int(bucket["correct"])
        wrong = int(bucket["wrong"])
        total = int(bucket["total"])
        success_rate = (correct / total) if total > 0 else 0.0
        status = classify_topic_band(success_rate)
        weak_u50 = success_rate < 0.5
        if weak_u50:
            under_half.append(topic)

        performance.append(
            TopicPerformance(
                topic=topic,
                correct_count=correct,
                wrong_count=wrong,
                total_attempts=total,
                success_rate=success_rate,
                status=status,
                is_weak_under_half=weak_u50,
            )
        )

        if status == "very_weak":
            very_weak.append(topic)
        elif status == "weak":
            weak.append(topic)
        elif status == "developing":
            developing.append(topic)
        elif status == "good":
            good.append(topic)
        else:
            strong.append(topic)

    return TopicAnalysisResponse(
        topics=performance,
        topics_under_half=under_half,
        very_weak_topics=very_weak,
        weak_topics=weak,
        developing_topics=developing,
        good_topics=good,
        strong_topics=strong,
        moderate_topics=list(developing),
    )


# fn: count_wrong | tr: yanlış cevap sayısını say / en: count wrong answers
def count_wrong(correctness: List[bool]) -> int:
    return sum(1 for x in correctness if not x)


# fn: verify_totals | tr: doğru/yanlış/toplam sayım doğrulaması / en: sanity check for correct/wrong/total counts
def verify_totals(questions: List[QuizQuestion], correctness: List[bool]) -> Tuple[int, int, int]:
    n = len(correctness)
    tc = sum(1 for x in correctness if x)
    tw = n - tc
    return tc, tw, n

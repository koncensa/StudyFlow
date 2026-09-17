# svc: quiz_timing | tr: pdf boyutu ve soru sayısına göre quiz süre limiti hesapla / en: compute adaptive quiz time limit from document size and question count

from __future__ import annotations

import re
from typing import Set


# fn: compute_quiz_time_limit_seconds | tr: quiz için saniye cinsinden süre limiti döndür (2–45 dk) / en: return quiz time limit in seconds (2–45 min)
def compute_quiz_time_limit_seconds(
    study_text: str,
    num_questions: int,
    *,
    difficulty: str = "normal",
    topic_labels: Set[str] | None = None,
) -> int:
    wc = len(re.findall(r"\S+", study_text or ""))
    n = max(1, min(int(num_questions or 5), 10))
    diff = (difficulty or "normal").strip().lower()
    # tr: zorluğa göre soru başına saniye / en: seconds per question by difficulty
    per_q = {"beginner": 40, "easy": 40, "normal": 54, "technical": 72, "advanced": 72, "hard": 72, "expert": 72}.get(
        diff, 54
    )

    # tr: pdf kelime sayısına göre ek süre / en: extra time bonus by document word count
    if wc < 400:
        doc_bonus = 30
    elif wc < 900:
        doc_bonus = 60
    elif wc < 2000:
        doc_bonus = 105
    elif wc < 4500:
        doc_bonus = 150
    else:
        doc_bonus = 195

    topic_n = len(topic_labels) if topic_labels else 0
    # tr: çok konu varsa yoğunluk bonusu / en: density bonus when many topics
    density_bonus = min(120, max(0, topic_n - n) * 12)

    total = n * per_q + doc_bonus + density_bonus
    # tr: en az 2 dk, en fazla 45 dk / en: min 2 min, max 45 min
    return int(max(120, min(total, 45 * 60)))

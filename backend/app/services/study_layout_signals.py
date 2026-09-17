# svc: study_layout_signals | tr: pdf metninden başlık/vurgu satırlarını sezgisel çıkar, llm promptuna ekle / en: extract heading/emphasis lines from pdf text heuristically for llm prompts

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Tuple

# tr: başlık benzeri satır prototip etiketleri / en: prototype tags for heading-like line scoring
_PROTO_CAPS = "caps_banner"
_PROTO_NUMBERED = "numbered_heading"
_PROTO_TITLE_WORDS = "title_case_banner"
_PROTO_BOLD = "markdown_bold_line"
_PROTO_KEYWORDS = "importance_keywords"


# fn: _letters | tr: satırdaki yalnızca harfleri birleştir / en: join alphabetic characters only
def _letters(s: str) -> str:
    return "".join(c for c in s if c.isalpha())


# fn: _upper_ratio | tr: satırdaki büyük harf oranı (0–1) / en: uppercase letter ratio in line
def _upper_ratio(s: str) -> float:
    L = _letters(s)
    if len(L) < 2:
        return 0.0
    up = sum(1 for c in L if c.isupper())
    return up / len(L)


# fn: _is_mostly_caps_banner | tr: satır büyük harf banner başlığı mı / en: whether line is mostly-caps banner heading
def _is_mostly_caps_banner(line: str) -> bool:
    s = line.strip()
    ln = len(s)
    if ln < 6 or ln > 120:
        return False
    L = _letters(s)
    if len(L) < 4:
        return False
    if _upper_ratio(s) < 0.82:
        return False
    # tr: uzun tek parça büyük harf metni değil / en: avoid long prose blocks that happen to be uppercase
    if ln > 72 and s.count(" ") < 2:
        return False
    if ln > 90:
        return False
    return True


# fn: _is_numbered_heading | tr: numaralı bölüm/başlık satırı mı / en: whether line is numbered section heading
def _is_numbered_heading(line: str) -> bool:
    s = line.strip()
    if len(s) < 4 or len(s) > 140:
        return False
    return bool(
        re.match(
            r"^(?:Chapter|Bölüm|Kısım|Ünite|Unit|Section|Part|PART|Ders|Konu|Modül)\s+"
            r"(?:[IVXLCDM]+|\d+)\b",
            s,
            re.I,
        )
        or re.match(r"^\d{1,2}[\.\)]\s+\S", s)
        or re.match(r"^[•\-\u2013\u2014]\s+\S", s)
    )


# fn: _is_title_case_banner | tr: her kelimesi büyük harfle başlayan başlık satırı mı / en: title-case banner line
def _is_title_case_banner(line: str) -> bool:
    s = line.strip()
    if len(s) < 8 or len(s) > 100:
        return False
    parts = re.findall(r"\S+", s)
    if not (2 <= len(parts) <= 12):
        return False
    for p in parts:
        if not p[0].isalpha():
            continue
        if not p[0].isupper():
            return False
    return True


# fn: _is_bold_markdown_line | tr: **kalın markdown** satırı mı / en: whether line is bold markdown title
def _is_bold_markdown_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 6 or len(s) > 120:
        return False
    if s.startswith("**") and s.endswith("**") and s.count("**") == 2:
        return True
    return False


# fn: _has_importance_keywords | tr: önem/sınav anahtar kelimeleri içeriyor mu / en: contains importance/exam keywords
def _has_importance_keywords(line: str) -> bool:
    low = line.casefold()
    keys = (
        "önemli",
        "dikkat",
        "exam",
        "sınav",
        "critical",
        "important",
        "remember",
        "unutma",
        "must know",
        "bilmeniz gereken",
        "key concept",
        "anahtar",
        "warning",
        "uyarı",
    )
    return any(k in low for k in keys)


# fn: _prototype_score | tr: satırın en uygun başlık prototipine skor ve etiket / en: score and best prototype id for line
def _prototype_score(line: str) -> Tuple[float, str]:
    """Return (score 0..1, best-matching prototype id)."""
    s = line.strip()
    if not s:
        return 0.0, ""
    scores: List[Tuple[float, str]] = []
    if _is_mostly_caps_banner(s):
        scores.append((0.95, _PROTO_CAPS))
    if _is_numbered_heading(s):
        scores.append((0.88, _PROTO_NUMBERED))
    if _is_title_case_banner(s):
        scores.append((0.72, _PROTO_TITLE_WORDS))
    if _is_bold_markdown_line(s):
        scores.append((0.85, _PROTO_BOLD))
    if _has_importance_keywords(s):
        scores.append((0.55, _PROTO_KEYWORDS))
    if not scores:
        return 0.0, ""
    scores.sort(key=lambda x: -x[0])
    return scores[0]


# fn: _pattern_key | tr: tekrarlayan slayt yapısını kümelemek için normalize anahtar / en: normalized key to cluster repeated slide shapes
def _pattern_key(line: str) -> str:
    """Normalise digits and collapse whitespace so repeated slide shapes cluster."""
    s = re.sub(r"\s+", " ", line.strip())
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"#+", "#", s)
    return s[:88].casefold()


# fn: _iter_physical_lines | tr: metni fiziksel satırlara böl / en: split text into physical lines
def _iter_physical_lines(text: str) -> Iterable[str]:
    for block in (text or "").split("\n\n"):
        for raw in block.split("\n"):
            t = raw.strip()
            if t:
                yield t


# fn: _noise_line | tr: gürültü satırı mı (url, rakam, çok kısa/uzun) / en: whether line is noise to skip
def _noise_line(s: str) -> bool:
    if len(s) < 4:
        return True
    if len(s) > 220:
        return True
    letters = _letters(s)
    if len(letters) < 3:
        return True
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return True
    if re.fullmatch(r"[\d\s\.\-–—/,:;]+", s):
        return True
    return False


# fn: collect_layout_signal_lines | tr: vurgulu satırları ve tekrarlayan yapı özetlerini döndür / en: return ranked highlight lines and repeated pattern summaries
def collect_layout_signal_lines(text: str, *, max_pick: int = 28) -> Tuple[List[str], List[str]]:
    """
    Returns (ranked_highlight_lines, repeated_pattern_summaries).
    """
    lines = [L for L in _iter_physical_lines(text) if not _noise_line(L)]
    if not lines:
        return [], []

    scored: List[Tuple[float, str, str]] = []
    for L in lines:
        sc, proto = _prototype_score(L)
        if sc >= 0.52:
            scored.append((sc, proto, L))

    scored.sort(key=lambda x: (-x[0], len(x[2])))
    picked: List[str] = []
    seen_cf: set[str] = set()
    for sc, _proto, L in scored:
        k = L.casefold()
        if k in seen_cf:
            continue
        seen_cf.add(k)
        tag = "CAPS" if sc >= 0.9 and _is_mostly_caps_banner(L) else (
            "HEAD" if _is_numbered_heading(L) or _is_bold_markdown_line(L) else "HIGHLIGHT"
        )
        picked.append(f"[{tag}] {L[:200]}")
        if len(picked) >= max_pick:
            break

    key_counts: Counter[str] = Counter()
    example_for_key: Dict[str, str] = {}
    for L in lines:
        if _noise_line(L):
            continue
        k = _pattern_key(L)
        if len(k) < 10:
            continue
        key_counts[k] += 1
        if k not in example_for_key:
            example_for_key[k] = L[:160]

    repeated_notes: List[str] = []
    for k, c in key_counts.most_common(12):
        if c >= 4:
            ex = example_for_key.get(k, k)
            repeated_notes.append(f"~{c}× same shape → «{ex}»")

    return picked, repeated_notes[:8]


# fn: format_layout_signals_for_llm | tr: llm promptu için kompakt layout sinyali bloğu üret / en: build compact layout signal block for llm prompt
def format_layout_signals_for_llm(text: str, *, max_chars: int = 2600) -> str:
    """
    Compact English meta-block for model prompts (works for TR/EN source text inside brackets).
    """
    highlights, repeats = collect_layout_signal_lines(text)
    if not highlights and not repeats:
        return ""

    parts: List[str] = [
        "### DOCUMENT_LAYOUT_SIGNALS (auto — heuristic «kNN-style» heading detection)",
        "These lines are likely slide/section emphasis (ALL CAPS, numbered headers, bold titles) or recurring list shapes.",
        "Prefer extracting quiz topics and «important» ideas from sections that match these signals when they align with body text.",
    ]
    if highlights:
        parts.append("Likely high-salience lines:")
        parts.extend(highlights)
    if repeats:
        parts.append("Repeated structural patterns (same layout many times — typical slide/workbook enumeration):")
        parts.extend(f"- {r}" for r in repeats)

    out = "\n".join(parts).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out

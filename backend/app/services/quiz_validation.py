# svc: quiz_validation | tr: llm quiz çıktısını doğrula, pdf dayanak kontrolü, metin kuralları / en: validate llm quiz output, pdf grounding checks, shared text rules

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, List, Optional, Sequence, Set, Tuple

from app.schemas.quiz_schema import QuizQuestion


# fn: QuizGenerationError | tr: quiz üretimi başarısız olunca api'ye kısa hata mesajı taşıyan istisna / en: exception carrying short api error when quiz generation fails
class QuizGenerationError(Exception):
    """Quiz generation failed; ``detail_tr`` is a short English message for API clients."""

    def __init__(self, detail_tr: str) -> None:
        super().__init__(detail_tr)
        self.detail_tr = detail_tr


# tr: anahtar kelime çıkarımı için durak kelimeler (en+tr) / en: stop words for keyword extraction (en+tr)
STOP_WORDS = {
    "the",
    "is",
    "are",
    "of",
    "and",
    "to",
    "in",
    "a",
    "an",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "it",
    "this",
    "that",
    "these",
    "those",
    "be",
    "or",
    "if",
    "then",
    "than",
    "into",
    "about",
    "not",
    "we",
    "you",
    "they",
    "he",
    "she",
    "i",
    "ve",
    "bir",
    "bu",
    "için",
    "ile",
    "da",
    "de",
    "ama",
    "çok",
    "daha",
    "kadar",
    "gibi",
    "bile",
    "üzerine",
    "göre",
    "olarak",
    "veya",
    "şu",
}

# tr: izin verilen soru tipleri / en: allowed question types
ALLOWED_QUESTION_TYPES = frozenset(
    {
        "definition",
        "concept",
        "comparison",
        "application",
        "interpretation",
        "formula",
    }
)
# tr: konu bazlı soru tipi sırası / en: topic-based question type order
TOPIC_QUESTION_TYPES = ("definition", "concept", "comparison", "application")

# tr: «for x in y» ifadesinin kod gibi görünmesine yol açan sağ taraf tokenları / en: rhs tokens that make «for x in y» look like code
_FOR_IN_CODE_ITERABLES = frozenset(
    {
        "range",
        "enumerate",
        "zip",
        "items",
        "values",
        "keys",
        "iterrows",
        "itertuples",
        "alphas",
        "params",
        "models",
        "folds",
        "layers",
    }
)


# fn: source_significant_tokens | tr: pdf/metin metninden örtüşme kontrolü için anlamlı token kümesi / en: significant token set from study/pdf text for overlap checks
def source_significant_tokens(text: str, cap_chars: int = 100_000) -> Set[str]:
    """Token bag from study/PDF text for overlap checks (bounded size)."""
    blob = re.sub(r"\s+", " ", (text or "").strip())[:cap_chars]
    return significant_tokens(blob)


# fn: strip_leading_enumeration_prefix | tr: şıktaki numara/madde işaretlerini temizle / en: strip outline markers from mcq option text
def strip_leading_enumeration_prefix(text: str) -> str:
    """
    Remove pasted outline markers from an MCQ option (e.g. «1, …», «2. …», «(3) …», bullets).
    Keeps decimals like «3,14» when the comma is not followed by whitespace.
    """
    s = (text or "").strip()
    if not s:
        return s
    # Letter after «1.» → outline number, not a version «3.14» (digit after dot).
    _pat = re.compile(
        r"^(?:"
        r"\(\s*\d{1,2}\s*\)\s*"
        r"|[•·▪▫◦‣⁃]\s+"
        r"|\d{1,2}\s*[.)]\s*"
        r"|\d{1,2}\s*:\s+"
        r"|\d{1,2}\s*-\s+"
        r"|\d{1,2}\s*,\s+"
        r"|\d{1,2}\.(?=[A-Za-zĞğÜüŞşİıÖöÇç])"
        r")",
        flags=re.UNICODE,
    )
    prev = None
    while s and s != prev:
        prev = s
        s2 = _pat.sub("", s).strip()
        s = s2
    return s


# fn: _bad_option_syntax_garbage | tr: şıkta json/kırık tırnak gibi çöp sözdizimini reddet / en: reject json fragments and broken syntax in options
def _bad_option_syntax_garbage(s: str) -> bool:
    """Reject JSON fragments, broken quotes, and obvious non-prose in MCQ options."""
    t = (s or "").strip()
    if not t:
        return True
    if re.search(r"\?{2,}", t):
        return True
    if re.search(r"(?<![.])\.\.(?![.])", t):
        return True
    # Model pasted JSON instead of prose
    if '\\"' in t or re.search(r'"\s*:\s*(\{|\[)', t):
        return True
    if re.search(r'["\']\s*,\s*["\']', t):
        return True
    if re.search(r"\{\s*[\"']", t) or re.search(r"\}\s*$", t):
        return True
    if t.count("{") != t.count("}") or t.count("[") != t.count("]"):
        return True
    if re.search(r"\\[nrtbf\"']{1,3}", t):
        return True
    if re.search(r"\bjson\b", t, flags=re.I) and ("{" in t or "[" in t):
        return True
    return False


# fn: bad_option_text | tr: şık metni geçersiz/çöp mü (placeholder, soru cümlesi, ocr vb.) / en: whether option text is invalid junk
def bad_option_text(opt: str) -> bool:
    raw = (opt or "").strip()
    s = raw.lower()
    if not s:
        return True
    # OCR / outline tail garbage: ", 3." / ": 1." / " 4."
    if re.search(r"[,;:]\s*\d{1,2}[.)]?\s*$", raw):
        return True
    if re.search(r"\s+\d{1,2}\.\s*$", raw):
        return True
    if re.search(r"\s+\d{1,2}\)\s*$", raw):
        return True
    if quiz_line_has_bad_turkish_glue(raw):
        return True
    if option_looks_like_question_sentence(raw):
        return True
    if line_uses_external_visual_reference(raw):
        return True
    if len(raw) >= 18 and quiz_line_looks_truncated_or_incomplete(raw, min_words=5):
        return True
    # Still looks like a bare list label after whitespace cleanup
    if re.match(r"^\d{1,2}\s*[.,;:)\-–—]\s*$", raw):
        return True
    if re.match(r"^\(?\s*\d{1,2}\s*\)?\s*$", raw):
        return True
    if _bad_option_syntax_garbage(opt or ""):
        return True
    if re.fullmatch(r"option\s*[a-d0-9]?", s):
        return True
    if re.fullmatch(r"option\s*\d+", s):
        return True
    if re.fullmatch(r"^(choice|seçenek|şık)\s*\d+$", s):
        return True
    if s in ("tbd", "n/a", "na", "none", "unknown", "other", "other.", "placeholder"):
        return True
    if "all of the above" in s or "none of the above" in s:
        return True
    if len(s) < 3:
        return True
    if re.fullmatch(r"^[a-d][\).]\s*$", s):
        return True
    if re.search(
        r"\b(lorem ipsum|asdf|foo bar|test test|random text|örnek metin|rastgele)\b",
        s,
        flags=re.I,
    ):
        return True
    if re.fullmatch(r"^[\W\d_]+$", s):
        return True
    alpha = sum(ch.isalpha() for ch in s)
    if len(s) > 12 and alpha < max(6, int(len(s) * 0.25)):
        return True
    return False


# fn: option_len_min | tr: şık minimum karakter sınırı (env) / en: min option char limit from env
def option_len_min() -> int:
    try:
        v = int(os.getenv("QUIZ_OPTION_MIN_CHARS", "8").strip() or "8")
    except ValueError:
        v = 8
    return max(5, min(v, 80))


# fn: option_len_max | tr: şık maksimum karakter sınırı (env) / en: max option char limit from env
def option_len_max() -> int:
    try:
        v = int(os.getenv("QUIZ_OPTION_MAX_CHARS", "900").strip() or "900")
    except ValueError:
        v = 900
    # Hard cap keeps prompts/JSON bounded; default is generous so stems/options are not clipped in the UI.
    return max(60, min(v, 4000))


# fn: option_len_ok | tr: şık uzunluğu min-max aralığında mı / en: whether option length is within min-max range
def option_len_ok(opt: str) -> bool:
    s = (opt or "").strip()
    return option_len_min() <= len(s) <= option_len_max()


# fn: question_len_max | tr: soru kökü maksimum karakter sınırı / en: max question stem char limit
def question_len_max() -> int:
    try:
        v = int(os.getenv("QUIZ_QUESTION_MAX_CHARS", "900").strip() or "900")
    except ValueError:
        v = 900
    return max(100, min(v, 4000))


# fn: question_word_max | tr: soru kökü maksimum kelime sayısı / en: max question stem word count
def question_word_max() -> int:
    try:
        v = int(os.getenv("QUIZ_QUESTION_MAX_WORDS", "72").strip() or "72")
    except ValueError:
        v = 72
    return max(12, min(v, 160))


# fn: option_word_max | tr: şık maksimum kelime sayısı / en: max option word count
def option_word_max() -> int:
    try:
        v = int(os.getenv("QUIZ_OPTION_MAX_WORDS", "56").strip() or "56")
    except ValueError:
        v = 56
    return max(6, min(v, 120))


# fn: clip_quiz_visible_text | tr: metni kelime ortasında kesmeden kısalt / en: shorten text without chopping mid-word
def clip_quiz_visible_text(s: str, max_chars: int) -> str:
    """
    Shorten only when longer than ``max_chars``. **Never** leaves a half-token tail:
    either cuts at the last space before the limit, or (if the limit falls inside the
    first token) returns that whole first token even when slightly over ``max_chars``.
    """
    t = re.sub(r"\s+", " ", str(s or "").strip())
    if not t or max_chars < 1:
        return t
    if len(t) <= max_chars:
        return t

    # Boundary is after index max_chars - 1; next char is t[max_chars] when len > max_chars
    i = min(max_chars, len(t))
    if i >= len(t):
        return t

    def _is_word_char(ch: str) -> bool:
        if not ch:
            return False
        o = ord(ch)
        if ch == "-" or ch == "_":
            return True
        if unicodedata.category(ch) in ("Nd", "Nl", "No"):
            return True
        return ch.isalpha()

    # Cut already ends at whitespace → trim trailing spaces only
    if t[i - 1].isspace():
        return t[:i].rstrip()

    # If we would split inside one word, never return a chopped stem
    if _is_word_char(t[i - 1]) and _is_word_char(t[i]):
        sp = t.rfind(" ", 0, i)
        if sp > 0:
            return t[:sp].rstrip()
        # Entire prefix before i is one word (no space): keep the full first word
        end = i
        while end < len(t) and _is_word_char(t[end]):
            end += 1
        return t[:end].rstrip()

    return t[:i].rstrip()


# fn: looks_like_code_or_tooling_noise | tr: metin kod/kütüphane gürültüsü mü / en: whether text looks like code or tooling noise
def looks_like_code_or_tooling_noise(text: str) -> bool:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    code_markers = (
        "import ",
        "from ",
        "def ",
        "class ",
        "return ",
        "lambda ",
        "print(",
        "plt.",
        "sns.",
        "np.",
        "pd.",
        ".loc[",
        ".iloc[",
        ".fit(",
        ".predict(",
        "pip install",
        "sklearn.",
        "tensorflow",
        "keras.",
        "torch.",
    )
    if any(m in low for m in code_markers):
        return True
    if re.search(r"`[^`]+`", s):
        return True
    if re.search(r"[A-Za-z_]\w*\[['\"][^'\"]+['\"]\]", s):
        return True
    if re.search(r"\b[a-z_]\w*\s*=\s*[^=]", low) and re.search(r"\b(np|pd|plt|sns)\b|[\[\]{}]", low):
        return True
    # Typical library / estimator constructor calls (not plain English like «April (»).
    if re.search(
        r"\b(?:Lasso|Ridge|RidgeCV|LassoCV|ElasticNet|SGDClassifier|RandomForest|GradientBoosting|"
        r"LinearRegression|LogisticRegression|GridSearch|Pipeline|StandardScaler|MinMaxScaler|"
        r"train_test_split|mean_squared_error)\s*\(",
        s,
        re.I,
    ):
        return True
    # Python-style «for x in y» (single-letter iterator + code-like iterable).
    m_for = re.search(r"\bfor\s+([a-z])\s+in\s+([a-z][a-z0-9_]*)\b", low)
    if m_for:
        rhs = m_for.group(2)
        if "_" in rhs or rhs in _FOR_IN_CODE_ITERABLES or any(ch.isdigit() for ch in rhs):
            return True
    return False


# fn: quiz_line_has_bad_auxiliary_doubling | tr: «has is», «was been» gibi bozuk yardımcı fiil tekrarı var mı / en: detect broken auxiliary verb doubling
def quiz_line_has_bad_auxiliary_doubling(s: str) -> bool:
    """«has is», «are is», «was been» — common broken MCQ glue from small models."""
    low = re.sub(r"\s+", " ", (s or "").strip()).lower()
    if re.search(
        r"\b(?:has|have|had|is|are|was|were)\s+(?:is|are|was|were|has|have|had)\b",
        low,
    ):
        return True
    if re.search(r"\b(?:was|were)\s+been\b", low):
        return True
    if re.search(r"\bto\s+be\s+is\b", low):
        return True
    if re.search(r"\b(?:does|do|did)\s+(?:is|are|was|were)\b", low):
        return True
    if re.search(
        r"\b(?:will|would|could|should|may|might|must|can)\s+(?:will|would|could|should|may|might|must|can)\b",
        low,
    ):
        return True
    return False


# fn: quiz_line_has_bad_turkish_glue | tr: «mi mi», «için için» gibi bozuk türkçe yapıştırma var mı / en: detect broken turkish particle doubling
def quiz_line_has_bad_turkish_glue(s: str) -> bool:
    """Duplicate particles / function words that almost always indicate a broken generation."""
    t = (s or "").strip()
    if not t:
        return False
    low = re.sub(r"\s+", " ", t)
    # Same question particle twice (mi mi, mı mı, …)
    if re.search(r"\b(mi|mı|mu|mü)\s+\1\b", low, flags=re.IGNORECASE):
        return True
    # Doubled high-frequency glue words
    if re.search(
        r"\b(için|ve|bir|ile|ancak|fakat|çünkü|veya|ya\s+da|gibi|kadar)\s+\1\b",
        low,
        flags=re.IGNORECASE,
    ):
        return True
    return False


# fn: quiz_line_looks_truncated_or_incomplete | tr: cümle yarım kesilmiş veya asılı bağlaçla bitiyor mu / en: detect truncated or dangling sentence endings
def quiz_line_looks_truncated_or_incomplete(s: str, *, min_words: int = 8) -> bool:
    """Comma/dash tails or a subordinator left dangling at the end — common LLM cut-offs."""
    t = (s or "").strip()
    if not t:
        return False
    low = re.sub(r"\s+", " ", t).casefold()
    w = re.findall(r"(?u)\w+", low)
    if len(w) < min_words:
        return False
    if t.endswith(",") or t.endswith("，"):
        return True
    if re.search(r"[-–—]\s*$", t):
        return True
    if re.search(
        r"\b(that|because|although|unless|whether|which|where|when|while|since|so that)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    if re.search(
        r"\b(çünkü|için|şu ki|bunun için|eğer|ancak|fakat|oysa|nitekim)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    if re.search(
        r"\b(to|for|of|with|about|from|into|onto|than|via|through|across)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    if re.search(
        r"\b(için|ile|gibi|kadar|göre|üzerine|hakkında|üzerinden)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    return False


# fn: quiz_line_has_syntax_or_completion_issue | tr: kök/şık için birleşik dilbilgisi ve tamamlama kontrolü / en: unified prose hygiene gate for stems/options
def quiz_line_has_syntax_or_completion_issue(s: str, *, min_words: int = 6) -> bool:
    """
    Unified prose hygiene gate for stems/options.
    Rejects broken glue, dangling endings, unmatched groupers, and obvious punctuation artifacts.
    """
    t = re.sub(r"\s+", " ", (s or "").strip())
    if not t:
        return True
    if quiz_line_has_bad_auxiliary_doubling(t):
        return True
    if quiz_line_has_bad_turkish_glue(t):
        return True
    if quiz_line_looks_truncated_or_incomplete(t, min_words=min_words):
        return True
    if quiz_line_has_unbalanced_groupers(t):
        return True
    if re.search(r"\?{2,}", t) or re.search(r"(?<![.])\.\.(?![.])", t):
        return True
    if re.search(r"[,;:]\s*$", t):
        return True
    low = t.casefold()
    if re.search(
        r"\b(and|or|because|if|that|which|while|since|to|for|of|with|about|from|into|onto|than)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    if re.search(
        r"\b(ve|veya|çünkü|için|eğer|ancak|fakat|ile|ya da|gibi|kadar|göre|üzerine)\s*([?.!…]+)?\s*$",
        low,
    ):
        return True
    return False


# fn: quiz_line_has_unbalanced_groupers | tr: eşleşmeyen parantez veya tek tırnak var mı / en: detect unmatched parentheses or odd quote count
def quiz_line_has_unbalanced_groupers(s: str) -> bool:
    """Unmatched () / [] or odd count of ASCII double quotes in one line."""
    t = (s or "").strip()
    if not t:
        return False
    if t.count("(") != t.count(")") and (t.count("(") + t.count(")")) >= 2:
        return True
    if t.count("[") != t.count("]") and (t.count("[") + t.count("]")) >= 2:
        return True
    dq = t.count('"')
    if dq % 2 == 1 and dq >= 1:
        return True
    return False


# fn: quiz_line_has_repeated_ngram | tr: aynı n-kelime parçası satırda iki kez geçiyor mu / en: detect repeated n-word chunk in one string
def quiz_line_has_repeated_ngram(s: str, n: int = 5) -> bool:
    """Same n-word chunk appears twice in one string (e.g. duplicated «Rowe says that …»)."""
    low = (s or "").casefold()
    w = re.findall(r"(?u)\w+", low)
    if len(w) < n * 2 + 1:
        return False
    for i in range(len(w) - n + 1):
        g = tuple(w[i : i + n])
        for j in range(i + n, len(w) - n + 1):
            if tuple(w[j : j + n]) == g:
                return True
    return False


# fn: stem_is_scaffold_junk_stem | tr: kalıp/iskelet çöp soru kökü mü / en: whether stem is stock scaffold junk
def stem_is_scaffold_junk_stem(text: str) -> bool:
    """Stock stems that often carry broken list markers (matches common bad generations)."""
    low = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if "in a problem context" in low and "which option" in low:
        return True
    if re.search(r"\buse of\s+\d+\)\s", low):
        return True
    if re.search(r"\d+\)\s*whether\b", low):
        return True
    return False


# fn: prose_has_obvious_defects | tr: tekrar eden kelime, metrik yapıştırma gibi bariz yazım kusurları / en: obvious prose defects for pdf-quality mcqs
def prose_has_obvious_defects(text: str) -> bool:
    """Repeated words, odd metric paste, or «is that N (» fragments — reject for PDF-quality MCQs."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    if quiz_line_has_bad_auxiliary_doubling(s):
        return True
    if quiz_line_has_bad_turkish_glue(s):
        return True
    if quiz_line_looks_truncated_or_incomplete(s):
        return True
    if "•" in s or "\u2022" in s:
        return True
    if re.search(r"\s[1-9]\d?\)\s+", s):
        return True
    if len(s) <= 280 and quiz_line_has_unbalanced_groupers(s):
        return True
    if 36 <= len(s) <= 320 and quiz_line_has_repeated_ngram(s, n=5):
        return True
    if re.search(r"\b(\w{2,})\s+\1\b", low):
        return True
    if s.count("$") >= 2:
        return True
    digit_runs = len(re.findall(r"\d[\d,.\s]*\d|\d{4,}", s))
    if digit_runs >= 3 and re.search(r"\b(rmse|mse|mae|r2|r²|cv)\b", low):
        return True
    if re.search(r"\bis\s+that\s+\d+\s*\(", low):
        return True
    if has_known_ocr_word_break(s):
        return True
    return False


# fn: looks_like_broken_math_notation | tr: latex/ocr matematik gürültüsü mü / en: whether text is broken math notation not pdf-friendly prose
def looks_like_broken_math_notation(text: str) -> bool:
    """LaTeX fragments, summation soup, or OCR-like math noise — not PDF-friendly prose."""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"\\(?:frac|sum|prod|int|sqrt|alpha|beta|gamma|lambda|sigma|hat|mathbf|mathrm|left|right)\b", s, re.I):
        return True
    if any(ch in s for ch in "∑∏∫√"):
        return True
    greek_math = sum(1 for ch in s if ch in "ρλθαβγδωΣ")
    if greek_math >= 3:
        return True
    if "Σ" in s or "∑" in s:
        return True
    if greek_math >= 2 and re.search(r"[|‖]\s*w", s, re.I):
        return True
    hats = len(re.findall(r"\^", s))
    if hats >= 3:
        return True
    if hats >= 2 and re.search(r"\^[^\s]{1,6}\^", s):
        return True
    if re.search(r"Σ\s*\|", s) or re.search(r"∑\s*\|", s):
        return True
    return False


# fn: stem_uses_vague_placeholder_entity | tr: «Problem» gibi belirsiz yer tutucu varlık kullanıyor mu / en: reject stems with vague placeholder entity like «Problem»
def stem_uses_vague_placeholder_entity(text: str) -> bool:
    """Reject stems that compare to the literal word «Problem» / vague placeholders (LLM junk)."""
    low = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if re.search(r"\band\s+problem\b[?.!…]*\s*$", low):
        return True
    if re.search(r"\bbetween\s+.+\s+and\s+problem\b", low):
        return True
    if re.search(r"\babout\s+problem\b[?.!…]*\s*$", low):
        return True
    if re.search(r"\bcompared\s+to\s+problem\b", low):
        return True
    return False


# fn: option_looks_like_training_or_metrics_dump | tr: şık eğitim/cv metrik satırı yapıştırması mı / en: reject options that are pasted model metrics lines
def option_looks_like_training_or_metrics_dump(s: str) -> bool:
    """
    Reject options that are pasted model-evaluation / CV output lines (common LLM failure in screenshots).
    """
    t = (s or "").strip()
    if not t:
        return False
    tl = t.lower()
    if re.search(r"\btrain\s+r\s*2\s*:", tl) and re.search(r"\btest\s+", tl):
        return True
    if re.search(r"\btest\s+rmse\s*:", tl) and re.search(r"\btrain\s+", tl):
        return True
    if re.search(r"\bridgecv\s*:", tl) or re.search(r"\blassocv\s*:", tl):
        return True
    if re.search(r"\bmodelcv\s*:", tl):
        return True
    if len(re.findall(r"\$\s*[\d,]{3,}", t)) >= 2:
        return True
    if re.search(r"\btest\s+r\s*2\s*:", tl) and re.search(r"[\d.]{3,}", t):
        return True
    if re.search(r"\brmse\s*:\s*\$", tl) and re.search(r"\br\s*2\s*:", tl):
        return True
    return False


# fn: option_uses_scraper_for_label_template | tr: «For Problem,» gibi kazıyıcı etiket kalıbı mı / en: detect «For Problem / For Train RMSE» scraper glue
def option_uses_scraper_for_label_template(s: str) -> bool:
    """«For Problem / For Train RMSE / For The model …» glue seen in bad generations."""
    low = re.sub(r"\s+", " ", (s or "").strip()).lower()
    if re.match(r"^for\s+problem\s*,", low):
        return True
    if re.match(r"^for\s+train\s+rmse\s*,", low):
        return True
    if re.match(r"^for\s+test\s+rmse\s*,", low):
        return True
    if re.match(r"^for\s+the\s+model\s*,", low):
        return True
    if re.match(r"^for\s+the\s+key\s+statement\s+for\s+", low):
        return True
    if re.match(r"^the\s+key\s+statement\s+for\s+", low):
        return True
    return False


# fn: option_looks_like_bare_metric_heading_list | tr: virgüllü metrik başlık listesi (cümle değil) mi / en: comma-list of metric names without a sentence
def option_looks_like_bare_metric_heading_list(s: str) -> bool:
    """Comma-list of metric names without a sentence (common bad distractor)."""
    tl = re.sub(r"\s+", " ", (s or "").strip()).lower()
    if re.match(r"^confusion matrix\s*,\s*precision\s*,\s*recall\s*,\s*f1", tl):
        return True
    return False


# fn: stem_looks_like_glued_comparison | tr: «Between A and B» yapıştırılmış uzun başlık karşılaştırması mı / en: unreadable glued «between a and b» comparison stem
def stem_looks_like_glued_comparison(text: str) -> bool:
    """«Between A and B» where A/B are huge pasted titles — unreadable in exams/PDF."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    low = t.lower()
    if " between " not in low or " and " not in low:
        return False
    m = re.search(r"\bbetween\s+(.+?)\s+and\s+([^?]+)\??\s*$", low, flags=re.I | re.S)
    if not m:
        return False
    a, b = m.group(1).strip(), m.group(2).strip()
    wa, wb = len(a.split()), len(b.split())
    # Only reject clearly pasted long titles — normal two-concept comparisons can be 8–12 words each side.
    if max(len(a), len(b)) > 100:
        return True
    if wa >= 14 or wb >= 14:
        return True
    if wa + wb >= 30:
        return True
    if "comparison between" in low and len(a) + len(b) > 160:
        return True
    if a.count(",") + b.count(",") >= 5:
        return True
    if len((a + " " + b).split()) > 34:
        return True
    return False


# fn: option_is_tautological_or_repetitive | tr: şık kendi kendini tekrar eden tautoloji mi / en: circular or repeated clause in option text
def option_is_tautological_or_repetitive(text: str) -> bool:
    """Circular «For X … is that X» patterns or the same clause repeated twice."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) < 24:
        return False
    low = t.lower()
    m = re.search(
        r"\bfor\s+(.+?),\s*the\s+accurate\s+interpretation\s+is\s+that\s+(.+)$",
        low,
    )
    if m:
        head = m.group(1).strip()
        tail = m.group(2).strip().rstrip(".!?")
        if len(head) >= 12 and tail and len(tail) <= 100:
            compact_h = re.sub(r"[\s,]", "", head)
            compact_t = re.sub(r"[\s,]", "", tail)
            if compact_t and (compact_t in compact_h or tail in head):
                return True
    chunks = [x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if len(x.strip()) > 18]
    if len(chunks) >= 2:
        a, b = chunks[0].lower(), chunks[-1].lower()
        if a in b or b in a:
            if min(len(a), len(b)) >= 28:
                return True
    words = low.split()
    if len(words) >= 12:
        hi = min(14, max(6, len(words) // 2))
        for ngram_len in range(hi, 4, -1):
            for i in range(0, len(words) - ngram_len * 2 + 1):
                ng = " ".join(words[i : i + ngram_len])
                if len(ng) < 28:
                    continue
                rest = " ".join(words[i + ngram_len :])
                if ng in rest:
                    return True
    return False


# fn: looks_like_notebook_or_outline_scrape | tr: jupyter/slayt outline yapıştırması mı / en: jupyter or slide outline pasted into mcq
def looks_like_notebook_or_outline_scrape(text: str) -> bool:
    """Jupyter / slide outline pasted into an MCQ — not educational prose."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    if "**" in s:
        return True
    if re.search(r"\bpart\s+[abcd]\s*[-–—]", low):
        return True
    if re.search(r"\bpart\s+[abcd]\b", low) and "scenario" in low:
        return True
    if re.search(r"\bin\s*\[\s*\d+", low):
        return True
    if re.search(r"#\s*(what|why|how|does|explain)\b", low):
        return True
    if "notebook covers" in low or "essential topics:" in low:
        return True
    if re.search(r"scenario:\s*you are", low):
        return True
    if s.count("—") >= 4 or s.count("–") >= 4:
        return True
    if re.search(r"^\s*no\s*\([^)]{0,48}\)\s*[—–]", low) and "yes" in low:
        return True
    return False


# fn: line_uses_external_visual_reference | tr: «aşağıdaki grafik» gibi dış görsel referansı var mı / en: reject lines relying on external visuals
def line_uses_external_visual_reference(text: str) -> bool:
    """
    Reject lines that rely on visuals/UI around the text (e.g. "the plot below").
    Quiz questions/options should stay self-contained.
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    if re.search(
        r"\b(?:as\s+shown|as\s+seen|shown|displayed|illustrated)\s+(?:above|below)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:figure|fig\.?|plot|graph|chart|table|diagram|image|screenshot|slide)\s+(?:above|below)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:in|from)\s+the\s+(?:figure|plot|graph|chart|table|diagram|image|screenshot)\s+(?:above|below)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:aşağıdaki|yukarıdaki)\s+(?:şekil|grafik|tablo|resim|görsel|diyagram)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:şekil|grafik|tablo|resim|görsel)\s*(?:\d+|[ivxlcdm]+)\b",
        low,
    ):
        return True
    if re.search(r"\b(?:click|tap|press)\s+(?:next|back|geri|ileri)\b", low):
        return True
    return False


# fn: option_looks_like_question_sentence | tr: şık soru cümlesi formunda mı (mcq şıkkı olmamalı) / en: option is interrogative not an assertion
def option_looks_like_question_sentence(text: str) -> bool:
    """
    MCQ options should be assertions, not new questions.
    Reject if the option is explicitly in interrogative form.
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    if "?" in s:
        return True
    if re.match(
        r"^(why|how|what|which|who|whom|whose|when|where|is|are|do|does|did|can|could|should|would|will|may|might|must)\b",
        low,
    ):
        return True
    if re.match(
        r"^(neden|niçin|nasıl|hangi|ne|kim|nerede|ne zaman|mı|mi|mu|mü)\b",
        low,
    ):
        return True
    return False


# fn: stem_references_question_as_subject | tr: kök başka bir soru cümlesini konu olarak mı soruyor / en: stem asks about another question sentence itself
def stem_references_question_as_subject(text: str) -> bool:
    """
    Reject stems that ask about another question sentence itself
    (e.g. "best explains the use of Why does ...").
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return False
    low = s.lower()
    if re.search(
        r"\b(?:use|meaning|interpretation|application|analysis|explains?)\s+of\s+(?:why|how|what|which|is|are|do|does|did)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:kullanımını|anlamını|yorumunu|açıklamasını)\s+(?:neden|niçin|nasıl|hangi|ne)\b",
        low,
    ):
        return True
    return False


# fn: has_known_ocr_word_break | tr: bilinen ocr kelime bölme hataları var mı / en: common split-letter typos from pdf/ocr
def has_known_ocr_word_break(text: str) -> bool:
    """Common split-letter typos from PDF/OCR (reject so model retries)."""
    low = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if re.search(
        r"\b(?:e xactly|gene rali[sz]e|gene ralise|i nstead|u nless|u nder|o therwise)\b",
        low,
    ):
        return True
    # Turkish / bilingual OCR-style splits inside common function words
    if re.search(
        r"\b(?:b\s+ir|v\s+e|i\s+çin|i\s+le|d\s+eğil|d\s+e\s+ğil|o\s+lan|m\s+i\s+dir|y\s+oktur)\b",
        low,
    ):
        return True
    return False


# fn: option_looks_truncated_or_stub | tr: şık yarım kelime veya stub cümleyle bitiyor mu / en: option ends mid-word or as stub sentence
def option_looks_truncated_or_stub(text: str) -> bool:
    """Ends mid-word / stub sentence — bad for print."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) < 22:
        return False
    low = t.lower()
    if re.search(r"\s+[a-z]{1,2}\.\s*$", t, flags=re.I):
        return True
    if re.search(r"\bpr\.\s*$", low):
        return True
    if low.endswith(" this.") or low.endswith(" this"):
        return True
    if t.endswith("—.") or t.endswith("–."):
        return True
    return False


# fn: stem_uses_raw_heading_fragment | tr: defines/about yuvası slayt başlığı ile mi doldurulmuş / en: heading slot filled with slide title not concept
def stem_uses_raw_heading_fragment(text: str) -> bool:
    """Defines/about … slot filled with a slide title or notebook line, not a concept name."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    low = t.lower()
    inner: Optional[str] = None
    m = re.search(r"\bdefines\s+(.+?)\s+most accurately", low, flags=re.I)
    if m:
        inner = m.group(1).strip()
    if inner is None:
        m2 = re.search(r"\babout\s+(.+?)\??\s*$", low, flags=re.I)
        if m2:
            inner = m2.group(1).strip()
    if not inner:
        return False
    li = inner.lower()
    bad_markers = (
        "we create",
        "this notebook",
        "cannot handle this",
        "perfectly on",
        "essential topics",
        "notebook covers",
        "scenario:",
        "part a —",
        "part b —",
        "in [",
        "target is now binary",
        "only approaches 0",
        "logistic regression this",
        "regularisation & logistic regression this",
    )
    if any(b in li for b in bad_markers):
        return True
    if li.endswith(" this") or li.endswith(" this?"):
        return True
    if li.endswith(" on") and len(inner) > 36:
        return True
    if re.search(r"\b(will|must|can)\s+(try|find)\s*\??$", li.rstrip("?")):
        return True
    if len(inner) > 58:
        return True
    return False


# fn: significant_tokens | tr: metinden anlamlı token kümesi çıkar / en: extract significant token set from text
def significant_tokens(s: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ0-9]{3,}", (s or "").lower())
        if w not in STOP_WORDS
    }


# fn: clamp_quiz_count | tr: istenen soru sayısını güvenli aralığa sıkıştır / en: clamp requested question count to safe range
def clamp_quiz_count(n: int, hard_cap: int = 15) -> int:
    return max(1, min(int(n or 5), hard_cap))


# fn: question_stem_key | tr: soru kökü için normalize edilmiş benzerlik anahtarı / en: normalized similarity key for question stem
def question_stem_key(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r"[^\w\sçğıöşü]", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:220]


# tr: kök tekrarını önlemek için şablon önekleri / en: stem dedupe prefix templates
_STEM_DEDUPE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("which statement is conceptually most accurate about ", "c_about"),
    ("which statement offers the most accurate comparison between ", "cmp_between"),
    ("which option defines ", "defn"),
    ("which definition best reflects ", "defn2"),
    ("in the given short scenario, which option applies ", "scn_apply"),
    ("in a problem context, which option best explains the use of ", "scn_use"),
)


# fn: _cross_stem_similarity_max | tr: quiz içi kök benzerlik eşiği (env) / en: cross-stem similarity threshold from env
def _cross_stem_similarity_max() -> float:
    try:
        v = float(os.getenv("QUIZ_CROSS_STEM_SIM_MAX", "0.73").strip() or "0.73")
    except ValueError:
        v = 0.73
    return max(0.62, min(v, 0.94))


# fn: _cross_option_similarity_max | tr: quiz içi şık benzerlik eşiği (env) / en: cross-option similarity threshold from env
def _cross_option_similarity_max() -> float:
    try:
        v = float(os.getenv("QUIZ_CROSS_OPTION_SIM_MAX", "0.76").strip() or "0.76")
    except ValueError:
        v = 0.76
    return max(0.65, min(v, 0.93))


# fn: question_reuses_identical_option_set | tr: önceki soruyla aynı dört şık kümesi mi / en: same four options as a previous question
def question_reuses_identical_option_set(candidate: QuizQuestion, prior: Sequence[QuizQuestion]) -> bool:
    """Same four options as a previous question (order may differ)."""
    cand = frozenset(option_signature(o) for o in (candidate.options or []) if str(o).strip())
    if len(cand) != 4:
        return False
    for q in prior:
        prev = frozenset(option_signature(o) for o in (q.options or []) if str(o).strip())
        if len(prev) == 4 and prev == cand:
            return True
    return False


# fn: question_shares_prior_option_signature | tr: şık metni önceki sorularda zaten kullanılmış mı / en: any option text already used in prior questions
def question_shares_prior_option_signature(
    candidate: QuizQuestion,
    prior: Sequence[QuizQuestion],
    *,
    min_sig_len: int = 18,
) -> bool:
    """Any option text (normalized) already used on an earlier question in this quiz."""
    prior_sigs: Set[str] = set()
    for q in prior:
        for o in q.options or []:
            sig = option_signature(str(o))
            if len(sig) >= min_sig_len:
                prior_sigs.add(sig)
    for o in candidate.options or []:
        sig = option_signature(str(o))
        if len(sig) >= min_sig_len and sig in prior_sigs:
            return True
    return False


# fn: survivor_collides_with_prior_quiz | tr: son çare pad için önceki quiz ile şık çakışması var mı / en: block recycled options for last-resort pads
def survivor_collides_with_prior_quiz(candidate: QuizQuestion, prior: Sequence[QuizQuestion]) -> bool:
    """
    For last-resort pads: block recycled option lines / identical option sets, but do not use
    stem similarity (pads may share a template while still being distinct MCQs).
    """
    if not prior:
        return False
    if question_reuses_identical_option_set(candidate, prior):
        return True
    if question_shares_prior_option_signature(candidate, prior, min_sig_len=16):
        return True
    return False


# fn: question_redundant_with_prior_quiz | tr: aday soru öncekilerle fazla benzer veya konu kotası aşıldı mı / en: candidate too close to prior or topic cap exceeded
def question_redundant_with_prior_quiz(
    candidate: QuizQuestion,
    prior: Sequence[QuizQuestion],
    *,
    quiz_target_size: Optional[int] = None,
) -> bool:
    """
    True when this question is too close to one already in the same quiz (stem paraphrase
    or an option recycled / heavily overlapping with a previous option).

    When ``quiz_target_size`` is set, also limits how many questions may share the same
    ``topic`` label so one chapter does not dominate the quiz.
    """
    if not prior:
        return False
    if quiz_target_size and int(quiz_target_size) > 0:
        n = int(quiz_target_size)
        cap_t = max(2, min(4, (n + 2) // 4))
        tp = (candidate.topic or "").strip().lower()
        if len(tp) >= 2:
            same_t = sum(1 for q in prior if (q.topic or "").strip().lower() == tp)
            if same_t >= cap_t:
                return True
    if question_reuses_identical_option_set(candidate, prior):
        return True
    if question_shares_prior_option_signature(candidate, prior, min_sig_len=18):
        return True
    smax = _cross_stem_similarity_max()
    omax = _cross_option_similarity_max()
    c_stem = question_stem_key(candidate.question_text or "")
    if len(c_stem) >= 12:
        for q in prior:
            p_stem = question_stem_key(q.question_text or "")
            if len(p_stem) >= 12 and SequenceMatcher(None, c_stem, p_stem).ratio() >= smax:
                return True
    prior_texts: List[str] = []
    for q in prior:
        for o in q.options or []:
            s = str(o).strip()
            if s:
                prior_texts.append(s)
    prior_texts = prior_texts[-140:]
    for co in candidate.options or []:
        s = str(co).strip()
        if len(s) < 18:
            continue
        slo = s.lower()
        for po in prior_texts:
            if len(po) < 18:
                continue
            if SequenceMatcher(None, slo, po.lower()).ratio() >= omax:
                return True
    return False


# fn: question_stem_dedupe_key | tr: aynı şablon+konu tekrarını önlemek için kök anahtarı / en: dedupe key for same template and subject
def question_stem_dedupe_key(text: str) -> str:
    """
    Key for deduplicating questions within one quiz: same template + same subject
    should not appear twice (even if punctuation differs slightly).
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    low = raw.lower()
    for prefix, tag in _STEM_DEDUPE_PREFIXES:
        if low.startswith(prefix):
            inner = low[len(prefix) :].strip().rstrip("?.!…")
            if len(inner) >= 5:
                return f"{tag}|{question_stem_key(inner)}"
    return question_stem_key(raw)


# fn: min_stem_key_len | tr: kök anahtarı minimum karakter uzunluğu / en: minimum stem key char length
def min_stem_key_len() -> int:
    try:
        v = int(os.getenv("QUIZ_MIN_STEM_CHARS", "8").strip() or "8")
    except ValueError:
        v = 8
    return max(6, min(v, 24))


# fn: normalize_quiz_options | tr: şıkları temizle, çöpleri at, tekrarları kaldır / en: clean options, drop junk, dedupe
def normalize_quiz_options(opts: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in opts:
        s = re.sub(r"\s+", " ", strip_leading_enumeration_prefix(str(raw)))
        if bad_option_text(s):
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


# fn: max_option_token_jaccard | tr: şıklar arası maksimum jaccard benzerliği / en: max jaccard similarity between options
def max_option_token_jaccard(opts: Sequence[str]) -> float:
    sets = [significant_tokens(str(o)) for o in opts]
    if not sets:
        return 0.0
    mx = 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i], sets[j]
            if not a or not b:
                continue
            inter = len(a & b)
            uni = len(a | b)
            mx = max(mx, inter / uni if uni else 0.0)
    return mx


# fn: max_option_sequence_similarity | tr: şıklar arası maksimum dizi benzerliği / en: max sequence similarity between options
def max_option_sequence_similarity(opts: Sequence[str]) -> float:
    arr = [str(o).strip() for o in opts]
    mx = 0.0
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            mx = max(mx, SequenceMatcher(None, arr[i].lower(), arr[j].lower()).ratio())
    return mx


# fn: options_have_substring_overlap | tr: bir şık diğerinin alt dizisi mi / en: whether one option is substring of another
def options_have_substring_overlap(opts: Sequence[str]) -> bool:
    arr = [re.sub(r"\s+", " ", str(o).strip()) for o in opts]
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            a, b = arr[i].lower(), arr[j].lower()
            if a == b:
                return True
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) < 18:
                continue
            if shorter in longer:
                return True
    return False


# fn: options_share_repeated_opening | tr: birkaç şık aynı uzun önekle başlıyor mu / en: several options share same long prefix
def options_share_repeated_opening(opts: Sequence[str], *, prefix_len: int = 32, min_same: int = 3) -> bool:
    """True when several options begin with the same long prefix (template copy-paste)."""
    prefs: List[str] = []
    for o in opts:
        s = re.sub(r"\s+", " ", str(o).strip().lower())
        if not s:
            continue
        prefs.append(s[:prefix_len] if len(s) >= prefix_len else s)
    if len(prefs) < min_same:
        return False
    return max(Counter(prefs).values(), default=0) >= min_same


# fn: options_coherent_with_topic | tr: en az üç şık konu/kök/doğru cevapla token örtüşüyor mu / en: at least three options anchor to topic/stem/answer
def options_coherent_with_topic(q: QuizQuestion) -> bool:
    topic = significant_tokens(q.topic or "")
    stem = significant_tokens(q.question_text or "")
    ca = significant_tokens(q.correct_answer or "")
    anchor = topic | stem | ca
    if len(anchor) < 2:
        return True
    anchored = 0
    for opt in q.options or []:
        ot = significant_tokens(opt)
        if ot and (ot & anchor):
            anchored += 1
    return anchored >= 3


# fn: strict_topic_options_enabled | tr: katı konu-şık tutarlılık modu açık mı / en: whether strict topic-option coherence is enabled
def strict_topic_options_enabled() -> bool:
    return os.getenv("QUIZ_STRICT_TOPIC_OPTIONS", "1").strip().lower() in ("1", "true", "yes", "on")


# fn: quiz_grounding_enabled | tr: pdf kaynak dayanak kontrolü açık mı / en: whether pdf source grounding check is enabled
def quiz_grounding_enabled() -> bool:
    return os.getenv("QUIZ_REQUIRE_SOURCE_GROUNDING", "1").strip().lower() in ("1", "true", "yes", "on")


# fn: min_options_source_overlap | tr: pdf ile örtüşmesi gereken minimum şık sayısı / en: min options that must overlap with source pdf
def min_options_source_overlap() -> int:
    try:
        v = int(os.getenv("QUIZ_MIN_OPTIONS_SOURCE_OVERLAP", "3").strip() or "3")
    except ValueError:
        v = 3
    return max(2, min(v, 4))


# fn: question_grounded_in_source | tr: soru kökü/konu ve çoğu şık pdf tokenlarıyla örtüşüyor mu / en: stem/topic and most options share pdf tokens
def question_grounded_in_source(q: QuizQuestion, source_tokens: Set[str]) -> bool:
    """Stem/topic and most options must share non-trivial tokens with the PDF/study text."""
    if not quiz_grounding_enabled() or len(source_tokens) < 12:
        return True
    stem_t = significant_tokens(q.question_text or "")
    topic_t = significant_tokens(q.topic or "")
    if not (stem_t & source_tokens) and not (topic_t & source_tokens):
        return False
    need = min_options_source_overlap()
    hit = 0
    for o in q.options or []:
        ot = significant_tokens(str(o))
        if ot & source_tokens:
            hit += 1
    if hit < need:
        return False
    # Rich PDFs: avoid «generic stem + topic label only» questions — anchor the stem *or* touch all options.
    try:
        rich = int(os.getenv("QUIZ_GROUNDING_RICH_SOURCE_TOKENS", "48").strip() or "48")
    except ValueError:
        rich = 48
    rich = max(24, min(rich, 200))
    if len(source_tokens) >= rich:
        stem_ok = bool(stem_t & source_tokens)
        if not stem_ok and hit < 4:
            return False
    return True


# fn: normalize_question_type | tr: llm soru tipini izin verilen tipe normalize et / en: normalize llm question type to allowed set
def normalize_question_type(raw: object) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("interpretation", "formula", "application", "scenario", "use_case"):
        return "application"
    if s in ("concept_understanding", "understanding"):
        return "concept"
    return s if s in ALLOWED_QUESTION_TYPES else None


# fn: normalize_session_difficulty | tr: api zorluk stringini beginner|normal|technical'e eşle / en: map api difficulty to beginner|normal|technical
def normalize_session_difficulty(raw: str) -> str:
    """Map API/session difficulty strings to beginner | normal | technical."""
    v = (raw or "normal").strip().lower()
    if v in ("beginner", "easy", "simple"):
        return "beginner"
    if v in ("technical", "advanced", "expert", "hard"):
        return "technical"
    return "normal"


# fn: stem_word_count | tr: soru kökündeki kelime sayısı / en: word count in question stem
def stem_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşüıİ0-9]+", (text or "").strip()))


# fn: resolve_correct_answer_text | tr: llm cevabını (A-D veya metin) şık metnine çöz / en: resolve llm answer index/letter to option text
def resolve_correct_answer_text(ans: Any, options: List[str]) -> Optional[str]:
    if not options or len(options) != 4:
        return None
    if isinstance(ans, bool):
        return None
    if isinstance(ans, int) and not isinstance(ans, bool):
        if 0 <= ans <= 3:
            return options[ans]
        return None
    s = str(ans).strip() if ans is not None else ""
    if not s:
        return None
    m = re.fullmatch(r"([A-Da-d])\s*[.\)]?\s*", s)
    if m:
        i = ord(m.group(1).upper()) - ord("A")
        if 0 <= i < 4:
            return options[i]
    for o in options:
        if o.strip().lower() == s.lower():
            return o
    return None


# fn: option_signature | tr: şık metni için normalize imza / en: normalized signature for option text
def option_signature(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


# fn: quiz_visible_text_has_stray_symbols | tr: görünmez kontrol/emoji gibi yabancı sembol var mı / en: invisible controls or emoji-like symbols remain
def quiz_visible_text_has_stray_symbols(s: str) -> bool:
    """
    True if invisible controls, private-use blocks, variation selectors, or emoji-like pictographs remain.
    Stems/options should be plain readable text after polish.
    """
    for ch in s or "":
        o = ord(ch)
        if o == 0xFEFF:
            return True
        cat = unicodedata.category(ch)
        if cat == "Cf":
            return True
        if cat == "Cc" and ch not in "\t\n\r":
            return True
        if 0xE000 <= o <= 0xF8FF:
            return True
        if 0xFFF0 <= o <= 0xFFFF:
            return True
        if 0xFE00 <= o <= 0xFE0F:
            return True
        if 0x1F300 <= o <= 0x1FAFF:
            return True
        if 0x2600 <= o <= 0x26FF or 0x2700 <= o <= 0x27BF:
            return True
    return False


# fn: validate_quiz_question_strict | tr: katı doğrulama: yapı + pdf dayanak + yazım kalitesi / en: strict validation: shape + pdf grounding + prose quality
def validate_quiz_question_strict(q: QuizQuestion, *, source_grounding: Optional[str] = None) -> bool:
    qt = (q.question_text or "").strip()
    if quiz_visible_text_has_stray_symbols(qt):
        return False
    if len(qt) < 12:
        return False
    if stem_word_count(qt) < 3:
        return False
    if len(qt) > question_len_max():
        return False
    if stem_word_count(qt) > question_word_max():
        return False
    if looks_like_code_or_tooling_noise(qt):
        return False
    if looks_like_broken_math_notation(qt):
        return False
    if stem_looks_like_glued_comparison(qt):
        return False
    if stem_uses_raw_heading_fragment(qt):
        return False
    if stem_references_question_as_subject(qt):
        return False
    if looks_like_notebook_or_outline_scrape(qt):
        return False
    if line_uses_external_visual_reference(qt):
        return False
    if has_known_ocr_word_break(qt):
        return False
    if stem_uses_vague_placeholder_entity(qt):
        return False
    if stem_is_scaffold_junk_stem(qt):
        return False
    if quiz_line_has_syntax_or_completion_issue(qt, min_words=6):
        return False
    if 36 <= len(qt) <= 320 and quiz_line_has_repeated_ngram(qt, n=5):
        return False
    opts = q.options or []
    if len(opts) != 4:
        return False
    if any(quiz_line_has_syntax_or_completion_issue(str(o).strip(), min_words=5) for o in opts):
        return False
    if any(
        36 <= len(str(o).strip()) <= 320 and quiz_line_has_repeated_ngram(str(o).strip(), n=5) for o in opts
    ):
        return False
    if any(bad_option_text(str(o)) for o in opts):
        return False
    if not all(option_len_ok(str(o)) for o in opts):
        return False
    if any(stem_word_count(str(o)) > option_word_max() for o in opts):
        return False
    if any(looks_like_code_or_tooling_noise(str(o)) for o in opts):
        return False
    if any(looks_like_broken_math_notation(str(o)) for o in opts):
        return False
    if any(option_is_tautological_or_repetitive(str(o)) for o in opts):
        return False
    if any(looks_like_notebook_or_outline_scrape(str(o)) for o in opts):
        return False
    if any(option_looks_truncated_or_stub(str(o)) for o in opts):
        return False
    if any(has_known_ocr_word_break(str(o)) for o in opts):
        return False
    if any(option_looks_like_training_or_metrics_dump(str(o)) for o in opts):
        return False
    if any(option_uses_scraper_for_label_template(str(o)) for o in opts):
        return False
    if any(option_looks_like_bare_metric_heading_list(str(o)) for o in opts):
        return False
    if options_share_repeated_opening(opts, prefix_len=30, min_same=3):
        return False
    if any(quiz_visible_text_has_stray_symbols(str(o)) for o in opts):
        return False
    ca = (q.correct_answer or "").strip()
    if not ca:
        return False
    if ca not in opts and not any(str(o).strip().lower() == ca.lower() for o in opts):
        return False
    ca_cf = ca.casefold()
    n_match = sum(1 for o in opts if str(o).strip().casefold() == ca_cf)
    if n_match != 1:
        return False
    top = (q.topic or "").strip()
    if quiz_visible_text_has_stray_symbols(top):
        return False
    if re.match(r"^\d{1,3}\s*\)", top):
        return False
    if "•" in top or "\u2022" in top:
        return False
    if len(top) < 2:
        return False
    if len(top) > 100:
        return False
    if looks_like_notebook_or_outline_scrape(top):
        return False
    if stem_uses_vague_placeholder_entity(top):
        return False
    if strict_topic_options_enabled() and not options_coherent_with_topic(q):
        return False
    if source_grounding and str(source_grounding).strip():
        src = source_significant_tokens(source_grounding)
        if src and not question_grounded_in_source(q, src):
            return False
    return True


# fn: validate_quiz_question_structural | tr: yapısal doğrulama: şekil + kod güvenliği, dayanak yok / en: structural validation without pdf grounding
def validate_quiz_question_structural(q: QuizQuestion) -> bool:
    """
    Shape + code-safety + exactly one correct option match; no PDF grounding, topic-coherence, or prose polish.
    Used only as a last resort so the API can still return a full quiz of length N.
    """
    qt = (q.question_text or "").strip()
    if len(qt) < 12:
        return False
    if stem_word_count(qt) < 3:
        return False
    if len(qt) > question_len_max() + 80:
        return False
    if stem_word_count(qt) > question_word_max() + 10:
        return False
    if looks_like_code_or_tooling_noise(qt):
        return False
    if looks_like_broken_math_notation(qt):
        return False
    if stem_looks_like_glued_comparison(qt):
        return False
    if stem_uses_raw_heading_fragment(qt):
        return False
    if stem_references_question_as_subject(qt):
        return False
    if looks_like_notebook_or_outline_scrape(qt):
        return False
    if line_uses_external_visual_reference(qt):
        return False
    if has_known_ocr_word_break(qt):
        return False
    if stem_uses_vague_placeholder_entity(qt):
        return False
    if quiz_line_has_syntax_or_completion_issue(qt, min_words=5):
        return False
    opts = q.options or []
    if len(opts) != 4:
        return False
    if any(bad_option_text(str(o)) for o in opts):
        return False
    if not all(option_len_ok(str(o)) for o in opts):
        return False
    if any(stem_word_count(str(o)) > option_word_max() + 8 for o in opts):
        return False
    if any(looks_like_code_or_tooling_noise(str(o)) for o in opts):
        return False
    if any(looks_like_broken_math_notation(str(o)) for o in opts):
        return False
    if any(option_is_tautological_or_repetitive(str(o)) for o in opts):
        return False
    if any(looks_like_notebook_or_outline_scrape(str(o)) for o in opts):
        return False
    if any(option_looks_truncated_or_stub(str(o)) for o in opts):
        return False
    if any(has_known_ocr_word_break(str(o)) for o in opts):
        return False
    if any(option_looks_like_training_or_metrics_dump(str(o)) for o in opts):
        return False
    if any(option_uses_scraper_for_label_template(str(o)) for o in opts):
        return False
    if any(option_looks_like_bare_metric_heading_list(str(o)) for o in opts):
        return False
    ca = (q.correct_answer or "").strip()
    if not ca:
        return False
    if ca not in opts and not any(str(o).strip().lower() == ca.lower() for o in opts):
        return False
    ca_cf = ca.casefold()
    n_match = sum(1 for o in opts if str(o).strip().casefold() == ca_cf)
    if n_match != 1:
        return False
    top = (q.topic or "").strip()
    if len(top) < 2:
        return False
    if len(top) > 100:
        return False
    if looks_like_notebook_or_outline_scrape(top):
        return False
    if stem_uses_vague_placeholder_entity(top):
        return False
    return True


# fn: validate_quiz_question_count_fill | tr: sayı doldurma katmanı: n soruya ulaşmak için gevşek doğrulama / en: looser tier so quiz can reach length n
def validate_quiz_question_count_fill(q: QuizQuestion) -> bool:
    """
    Final tier so a quiz can still reach length N: keeps code/math/notebook hygiene and
    four distinct options, but skips PDF grounding, topic-coherence, prose, similarity caps,
    and other strict teaching-quality gates.
    (Intentionally looser than strict/structural on metric-line / scraper heuristics so
    rule-based fill can still pass.)
    """
    qt = (q.question_text or "").strip()
    if len(qt) < 12 or stem_word_count(qt) < 3:
        return False
    if len(qt) > question_len_max() + 140:
        return False
    if stem_word_count(qt) > question_word_max() + 14:
        return False
    if looks_like_code_or_tooling_noise(qt) or looks_like_broken_math_notation(qt):
        return False
    if stem_references_question_as_subject(qt):
        return False
    if looks_like_notebook_or_outline_scrape(qt):
        return False
    if line_uses_external_visual_reference(qt):
        return False
    if quiz_line_has_syntax_or_completion_issue(qt, min_words=5):
        return False
    opts = q.options or []
    if len(opts) != 4:
        return False
    if any(bad_option_text(str(o)) for o in opts):
        return False
    if any(quiz_line_has_syntax_or_completion_issue(str(o), min_words=4) for o in opts):
        return False
    if not all(option_len_ok(str(o)) for o in opts):
        return False
    if any(stem_word_count(str(o)) > option_word_max() + 12 for o in opts):
        return False
    if any(looks_like_code_or_tooling_noise(str(o)) for o in opts):
        return False
    if any(looks_like_broken_math_notation(str(o)) for o in opts):
        return False
    if any(looks_like_notebook_or_outline_scrape(str(o)) for o in opts):
        return False
    sigs = [option_signature(o) for o in opts]
    if len(sigs) != 4 or len(set(sigs)) != 4:
        return False
    ca = (q.correct_answer or "").strip()
    if not ca:
        return False
    if ca not in opts and not any(str(o).strip().lower() == ca.lower() for o in opts):
        return False
    ca_cf = ca.casefold()
    if sum(1 for o in opts if str(o).strip().casefold() == ca_cf) != 1:
        return False
    top = (q.topic or "").strip()
    if len(top) < 2 or len(top) > 120:
        return False
    if looks_like_notebook_or_outline_scrape(top):
        return False
    return True


# fn: validate_quiz_question_survivor | tr: son çare katmanı: yalnızca şekil ve dört farklı şık / en: last-resort tier: shape and four distinct options only
def validate_quiz_question_survivor(q: QuizQuestion) -> bool:
    """
    Last-resort tier: only shape, distinct options, and sane lengths — used so the API
    can still return exactly N questions when all richer tiers are exhausted.
    """
    qt = (q.question_text or "").strip()
    if len(qt) < 10 or stem_word_count(qt) < 2:
        return False
    if len(qt) > question_len_max() + 260:
        return False
    if looks_like_code_or_tooling_noise(qt) or looks_like_broken_math_notation(qt):
        return False
    if stem_references_question_as_subject(qt):
        return False
    if looks_like_notebook_or_outline_scrape(qt):
        return False
    if line_uses_external_visual_reference(qt):
        return False
    if quiz_visible_text_has_stray_symbols(qt):
        return False
    if quiz_line_has_syntax_or_completion_issue(qt, min_words=4):
        return False
    opts = q.options or []
    if len(opts) != 4:
        return False
    for o in opts:
        s = str(o).strip()
        if len(s) < 6 or len(s) > option_len_max():
            return False
        if bad_option_text(s):
            return False
        if quiz_line_has_syntax_or_completion_issue(s, min_words=3):
            return False
        if option_looks_truncated_or_stub(s):
            return False
        if looks_like_notebook_or_outline_scrape(s):
            return False
        if quiz_visible_text_has_stray_symbols(s):
            return False
    sigs = [option_signature(o) for o in opts]
    if len(sigs) != 4 or len(set(sigs)) != 4:
        return False
    ca = (q.correct_answer or "").strip()
    if not ca:
        return False
    if not any(str(o).strip().casefold() == ca.casefold() for o in opts):
        return False
    if sum(1 for o in opts if str(o).strip().casefold() == ca.casefold()) != 1:
        return False
    top = (q.topic or "").strip()
    if not top or len(top) > 120:
        return False
    if quiz_visible_text_has_stray_symbols(top):
        return False
    if quiz_line_has_syntax_or_completion_issue(top, min_words=2):
        return False
    return True


# fn: validate_quiz_question_complete | tr: en katı doğrulama: strict + benzer şık/alt dizi kontrolleri / en: strictest tier with option similarity checks
def validate_quiz_question_complete(q: QuizQuestion, *, source_grounding: Optional[str] = None) -> bool:
    if not validate_quiz_question_strict(q, source_grounding=source_grounding):
        return False
    qt = (q.question_text or "").strip()
    if prose_has_obvious_defects(qt):
        return False
    opts = q.options or []
    if any(prose_has_obvious_defects(str(o)) for o in opts):
        return False
    sigs = [option_signature(o) for o in opts]
    if len(sigs) != 4 or len(set(sigs)) != 4:
        return False
    if options_have_substring_overlap(opts):
        return False
    try:
        jmax = float(os.getenv("QUIZ_OPTION_JACCARD_MAX", "0.76").strip() or "0.76")
    except ValueError:
        jmax = 0.76
    jmax = max(0.50, min(jmax, 0.92))
    if max_option_token_jaccard(opts) > jmax:
        return False
    try:
        smax = float(os.getenv("QUIZ_OPTION_SEQUENCE_MAX", "0.80").strip() or "0.80")
    except ValueError:
        smax = 0.80
    smax = max(0.55, min(smax, 0.92))
    if max_option_sequence_similarity(opts) > smax:
        return False
    for o in opts:
        if stem_word_count(str(o)) < 2:
            return False
    exp = (q.explanation or "").strip()
    if exp and (
        len(exp) > 2400
        or looks_like_code_or_tooling_noise(exp)
        or looks_like_broken_math_notation(exp)
        or looks_like_notebook_or_outline_scrape(exp)
        or has_known_ocr_word_break(exp)
        or quiz_visible_text_has_stray_symbols(exp)
    ):
        return False
    return True


# fn: min_viable_quiz_count | tr: üretim başarısız olursa kabul edilebilir minimum soru sayısı / en: minimum acceptable question count on partial failure
def min_viable_quiz_count(requested: int) -> int:
    n = clamp_quiz_count(requested)
    if n <= 2:
        return n
    if n <= 4:
        return 2
    return 3

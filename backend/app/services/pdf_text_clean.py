# svc: pdf_text_clean | tr: pdf'den çıkan bozuk metni temizle, kod gürültüsünü at / en: clean noisy pdf text, drop code noise

import re
import unicodedata
from typing import List, Sequence, Tuple

_CONS = "bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ"
# Do not merge after possessive «'s» (e.g. «one's sex» → «s»+«sex» is two words, not «ssex»).
_SPLIT_WORD_FIX = re.compile(
    rf"(?<![\'\u2019\u2018])\b([{re.escape(_CONS)}])\s+([^\W\d_]{{3,}})\b",
    re.UNICODE,
)
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)", re.UNICODE)
_SPLIT_OF_THEIR = re.compile(r"\b([Oo])\s+([fF])(their|rom|or)\b")

# Longer first: merge "differ ent", "predict ions", "Scenar io", etc.
_MERGE_SUFFIXES = (
    "tion",
    "sion",
    "ment",
    "ness",
    "ance",
    "ence",
    "able",
    "ible",
    "fully",
    "edly",
    "ions",
    "ough",
    "predictions",
    "ictions",
    "ent",
    "ing",
    "ful",
    "ous",
    "ive",
    "ity",
    "ally",
    "ier",
    "ies",
    "eds",
    "ing",
    "ed",
    "ly",
    "er",
    "es",
    "al",
    "ic",
    "or",
    "um",
    "en",
    "an",
    "ce",
    "te",
    "ve",
    "le",
    "re",
    "nd",
    "st",
    "ts",
    "io",
    "um",
    "ly",
    # PDF line-wrap: stem split before final «on» (needs long left stem; see _merge_pdf_word_splits).
    "on",
)

_MERGE_PREFIXES = (
    "inter",
    "trans",
    "over",
    "under",
    "counter",
    "pre",
    "sub",
    "con",
    "com",
    "dis",
    "dif",
    "per",
    "pro",
    "re",
    "un",
    "de",
    "ex",
    "im",
    "pr",
)

_CODE_LINE_HINTS = re.compile(
    r"\b(sklearn|matplotlib|seaborn|pandas|numpy|jupyter|dataframe|pipeline|"
    r"MinMaxScaler|SimpleImputer|train_test_split)\b",
    re.I,
)

# Prose cues: if a line is long enough and contains these, do not treat as pure notebook plot line.
_LLM_CONTEXT_PROSE_CUE = re.compile(
    r"\b(means|because|therefore|thus|hence|introduces|allows|helps|explains|defines|definition|important|"
    r"key idea|note that|recall|intuitively|in contrast|however|activation|nonlinear|function|concept|"
    r"illustrates|demonstrates|shows that|argues|claims|summary|purpose)\b",
    re.I,
)

# Notebook / matplotlib one-liners and common ML helpers (PDF + notebook export noise).
_NB_PLOT_API = re.compile(
    r"(?i)\b(plt\.|fig\.|ax\.|sns\.|pd\.|np\.|tf\.|torch\.)\s*\w*\s*\(?"
)
_NB_PLOT_CALLS = re.compile(
    r"(?i)\b(set_xlabel|set_ylabel|set_title|scatter|subplots?|imshow|colorbar|legend|plot|show)\s*\(",
)
_NB_SKLEARN_HELPERS = re.compile(
    r"(?i)\b(make_circles|make_moons|make_classification|make_blobs|mlpclassifier|svc\b|randomforest|"
    r"train_test_split|gridsearch|cross_val|fit\(|\.fit\(|predict\()\b",
)
_NB_IMPORT_LINE = re.compile(
    r"(?i)^\s*(import|from)\s+(sklearn|matplotlib|numpy|pandas|seaborn|torch|tensorflow|keras)\b",
)
_NB_SECTION_STUB = re.compile(
    r"(?i)^(below|here|in this (notebook|section|cell))\b.{0,120}\b(train|fit|plot|build|run)\b",
)

_OCR_GLUE_FIXES = (
    (r"\billegalor\b", "illegal or"),
    (r"\blegalor\b", "legal or"),
    (r"\bmoralor\b", "moral or"),
    (r"\bethicalor\b", "ethical or"),
    (r"\btechnicalor\b", "technical or"),
    (r"\bfinancialor\b", "financial or"),
    (r"\bbusinessor\b", "business or"),
    (r"\binformationmeans\b", "information means"),
    (r"\binformationis\b", "information is"),
    (r"\bconflictsofinterest\b", "conflicts of interest"),
)

# PDF line-wrap / font kerning: a tiny word is split as "o f", "t he", "i n", etc. (whole-word patterns only.)
_OCR_SPLIT_SHORT_WORD_FIXES: tuple[tuple[str, str], ...] = (
    (r"\bw hich\b", "which"),
    (r"\bw hether\b", "whether"),
    (r"\bw here\b", "where"),
    (r"\bw hile\b", "while"),
    (r"\bw ith\b", "with"),
    (r"\bw hen\b", "when"),
    (r"\bw hat\b", "what"),
    (r"\bw ere\b", "were"),
    (r"\bt here\b", "there"),
    # «these/those» as «the se» / «thos e» (line wrap before last letter).
    (r"\bthe se\b", "these"),
    (r"\bthos e\b", "those"),
    (r"\bt hese\b", "these"),
    (r"\bt hose\b", "those"),
    (r"\bt hen\b", "then"),
    (r"\bt han\b", "than"),
    (r"\bt hat\b", "that"),
    (r"\bt hey\b", "they"),
    (r"\bh ave\b", "have"),
    (r"\bh as\b", "has"),
    (r"\bh ad\b", "had"),
    (r"\bh is\b", "his"),
    (r"\bh er\b", "her"),
    (r"\bf rom\b", "from"),
    (r"\bf or\b", "for"),
    (r"\bn ot\b", "not"),
    (r"\bn ow\b", "now"),
    (r"\ba nd\b", "and"),
    (r"\ba re\b", "are"),
    (r"\bw as\b", "was"),
    (r"\bth e\b", "the"),
    (r"\bt he\b", "the"),
    (r"\bo f\b", "of"),
    (r"\bt o\b", "to"),
    (r"\bi n\b", "in"),
    (r"\bi s\b", "is"),
    (r"\bi t\b", "it"),
    (r"\ba s\b", "as"),
    (r"\ba t\b", "at"),
    (r"\bo r\b", "or"),
    (r"\bo n\b", "on"),
    (r"\bb y\b", "by"),
    (r"\bn o\b", "no"),
    (r"\bs o\b", "so"),
    (r"\bw e\b", "we"),
    (r"\bm e\b", "me"),
    (r"\bb e\b", "be"),
    (r"\bd o\b", "do"),
    (r"\bi f\b", "if"),
    (r"\bu s\b", "us"),
    (r"\ba m\b", "am"),
    (r"\bh e\b", "he"),
    (r"\bu p\b", "up"),
    (r"\ba n\b", "an"),
    # «one» as «on e» or «o ne» (PDF wrap / kerning).
    (r"\bon e\b", "one"),
    (r"\bo ne\b", "one"),
    (r"\btw o\b", "two"),
    (r"\bsom e\b", "some"),
    (r"\bbas e\b", "base"),
    (r"\bcas e\b", "case"),
    (r"\bmor e\b", "more"),
    (r"\btim e\b", "time"),
    (r"\brac e\b", "race"),
    (r"\bregardles s\b", "regardless"),
    (r"\bregar d less\b", "regardless"),
    (r"\bbecaus e\b", "because"),
)


# fn: _repair_ocr_word_glue | tr: ocr birleşik/ayrı kelimeleri düzelt / en: fix ocr glued/split words
def _repair_ocr_word_glue(t: str) -> str:
    s = t or ""
    for pat, rep in _OCR_GLUE_FIXES:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    for pat, rep in _OCR_SPLIT_SHORT_WORD_FIXES:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    for _ in range(4):
        prev = s
        s = re.sub(r"([A-Za-z])'s([a-z])", r"\1's \2", s)
        s = re.sub(r"([a-z]),([a-z])", r"\1, \2", s)
        s = re.sub(r"([a-z])\.([A-Z])", r"\1. \2", s)
        if s == prev:
            break
    return s


# fn: _strip_notebook_page_footer_noise | tr: notebook in/out, sayfa no gibi gürültüyü sil / en: strip notebook in/out and page footer noise
def _strip_notebook_page_footer_noise(t: str) -> str:
    t = re.sub(r"In\s*\[\d+\]:[^\n]*", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"Out\s*\[\d+\]:[^\n]*", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bPage\s*\d+\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bSayfa\s*\d+\b", " ", t, flags=re.IGNORECASE)
    lines: List[str] = []
    for line in t.split("\n"):
        lines.append(re.sub(r"\b\d+\b(?=\s*$)", " ", line))
    return "\n".join(lines)


# fn: _remove_garbage_chars | tr: geçersiz unicode ve özel karakterleri sil / en: remove invalid unicode and junk chars
def _remove_garbage_chars(t: str) -> str:
    t = t.replace("\ufffd", "").replace("\u200b", "").replace("\ufeff", "")
    out: List[str] = []
    for ch in t:
        if ch in "\n\t":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C"):
            if ch in "\n\t":
                out.append(ch)
            continue
        o = ord(ch)
        if 0xE000 <= o <= 0xF8FF:
            continue
        out.append(ch)
    return "".join(out)


# fn: _merge_pdf_word_splits | tr: satır sonu kelime kırılmalarını birleştir / en: merge line-broken words
def _merge_pdf_word_splits(t: str) -> str:
    for suf in sorted(set(_MERGE_SUFFIXES), key=len, reverse=True):
        if len(suf) <= 2:
            # Two-letter «suffix» pieces are ambiguous (e.g. «on» in «switch on»).
            # «on» after a long stem fixes line-wrap splits like «…ati on» / «…iti on» (not «switch on»).
            min_left = 7 if suf.lower() == "on" else 4
        else:
            min_left = 2
        pat = rf"\b([a-zA-Z]{{{min_left},}})\s+({re.escape(suf)})\b"
        t = re.sub(pat, r"\1\2", t, flags=re.IGNORECASE)
    for pref in sorted(set(_MERGE_PREFIXES), key=len, reverse=True):
        pat = rf"\b({re.escape(pref)})\s+([a-zA-Z]{{4,}})\b"
        t = re.sub(pat, r"\1\2", t, flags=re.IGNORECASE)
    return t


# fn: _soft_join_wrapped_lines | tr: yumusak satır kırılmalarını birleştir / en: join soft line wraps
def _soft_join_wrapped_lines(t: str) -> str:
    t = re.sub(r"(?<=[a-z,;:])\n(?=[a-z])", " ", t)
    t = re.sub(r"(?<=[a-z0-9])\n(?=[a-z]{2,})", " ", t)
    return t


# fn: _strip_markdown_table_noise | tr: markdown tablo satırlarını düz metne çevir / en: flatten markdown table lines to prose
def _strip_markdown_table_noise(t: str) -> str:
    lines_out: List[str] = []
    for line in t.split("\n"):
        s = line.strip()
        if not s:
            lines_out.append("")
            continue
        if "|" in s and re.match(r"^[\s|\-–:]+$", s.replace("|", " ").strip()):
            continue
        if s.count("|") >= 3:
            cells = [c.strip() for c in s.split("|")]
            cells = [c for c in cells if c and not re.match(r"^[\-–:]+$", c)]
            if len(cells) >= 2:
                lines_out.append(" — ".join(cells))
            continue
        lines_out.append(line)
    return "\n".join(lines_out)


# fn: clean_extracted_pdf_text | tr: ana pdf temizleme (pdf_service burayi çağırır) / en: main pdf text cleanup entry
def clean_extracted_pdf_text(text: str) -> str:
    if not text or not text.strip():
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = unicodedata.normalize("NFKC", t)
    t = _remove_garbage_chars(t)
    t = _strip_notebook_page_footer_noise(t)
    t = _repair_ocr_word_glue(t)
    t = _HYPHEN_LINEBREAK.sub(r"\1\2", t)
    # Line-wrap often splits a word across «…ti» + newline + «on» before merge can see a single token.
    # Join soft wraps first, then merge stem+suffix (possibly twice).
    for _ in range(2):
        t = _soft_join_wrapped_lines(t)
        t = _merge_pdf_word_splits(t)
    t = _strip_markdown_table_noise(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = _SPLIT_OF_THEIR.sub(r"\1\2\3", t)
    prev = None
    for _ in range(60):
        if prev == t:
            break
        prev = t
        t = _SPLIT_WORD_FIX.sub(r"\1\2", t)
    return t.strip()


# fn: structure_study_paragraphs | tr: paragrafları düzenle, satır kırılmalarını birleştir / en: structure paragraphs, merge wraps
def structure_study_paragraphs(text: str) -> str:
    if not text.strip():
        return text
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    merged: List[str] = []
    for b in blocks:
        inner = re.sub(r"(?<=\w)\n(?=\w)", " ", b)
        inner = re.sub(r"[ \t]{2,}", " ", inner).strip()
        if inner:
            merged.append(inner)
    return "\n\n".join(merged)


# fn: _line_is_setup_or_runtime_noise | tr: pip install, print, seed gibi satırları at / en: drop setup/runtime noise lines
def _line_is_setup_or_runtime_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    sl = s.lower()
    if sl.startswith("%") or sl.startswith("!pip") or sl.startswith("!conda"):
        return True
    if "pip install" in sl or "conda install" in sl:
        return True
    if re.match(r"^print\s*\(", sl):
        return True
    if re.search(
        r"\b(random\.seed|np\.random\.seed|numpy\.random\.seed|torch\.manual_seed|"
        r"tensorflow\.random\.set_seed|tf\.random\.set_seed|manual_seed_all)\s*\(",
        sl,
    ):
        return True
    if re.search(r"\brandom_state\s*=\s*\d+\b", sl) and len(s) < 160:
        return True
    if re.search(r"\ball libraries loaded\b|\bsuccessfully installed\b|\brequirement already satisfied\b", sl):
        return True
    if re.match(r"^[\d\.\s]+(?:\s+MB|\s+KB)?$", sl) and len(sl) < 40:
        return True
    if sl.startswith("saving") and "checkpoint" in sl:
        return True
    if sl.startswith("epoch ") and re.search(r"\b\d+/\d+\b", sl):
        return True
    if sl.startswith("```") or sl in ("```python", "```bash", "```sql", "```javascript", "```json"):
        return True
    return False


# fn: build_normalized_study_text | tr: özet/quiz için tam temiz metin üret / en: full clean text for summary/quiz
def build_normalized_study_text(pdf_extract: str) -> str:
    t = clean_extracted_pdf_text((pdf_extract or "").strip())
    t = structure_study_paragraphs(t)
    t = prepare_text_for_document_summary(t)
    return t.strip()


# fn: _line_code_score | tr: satırın ne kadar kod gibi olduğunu skorla / en: score how code-like a line is
def _line_code_score(line: str) -> float:
    if not line.strip():
        return 0.0
    L = len(line)
    sl = line.lower()
    sp = line.count(" ") / max(L, 1)
    score = 0.0
    if L > 200 and sp < 0.07:
        score += 28.0
    if sl.startswith("import ") or sl.startswith("from "):
        score += 18.0
    if " import " in sl:
        score += 6.0
    if "pip install" in sl or "!pip" in sl or "pip3 install" in sl:
        score += 14.0
    score += sl.count("def ") * 10.0
    score += sl.count("class ") * 6.0
    score += min(line.count("="), 25) * 0.35
    score += line.count("__") * 1.5
    score += sl.count("plt.") * 6.0 + sl.count("pd.") * 6.0 + sl.count("np.") * 6.0
    score += sl.count("sns.") * 6.0
    score += line.count("(") * 0.12 + line.count(")") * 0.12
    score += line.count(";") * 0.8
    if _CODE_LINE_HINTS.search(line):
        score += 4.0
    words = line.split()
    if words:
        score += sum(3.5 for w in words if len(w) > 50)
    if re.search(r"lambda\s+\w+\s*:", sl):
        score += 12.0
    if "np.array" in sl or "print(" in sl:
        score += 5.0
    if re.search(r"\b(torch\.|tensorflow|keras\.|plt\.|sns\.|matplotlib|seaborn\.|cv2\.)\b", sl):
        score += 4.0
    if re.search(r"\b(session\.run|tensorboard|wandb\.|logging\.)\b", sl):
        score += 8.0
    if _NB_PLOT_API.search(line):
        score += 14.0
    if _NB_PLOT_CALLS.search(line):
        score += 12.0
    if _NB_SKLEARN_HELPERS.search(line):
        score += 10.0
    if _NB_IMPORT_LINE.match(line.strip()):
        score += 20.0
    return score


# fn: _line_is_notebook_section_stub | tr: notebook giriş cümlesi mi / en: is notebook section stub line
def _line_is_notebook_section_stub(line: str) -> bool:
    s = (line or "").strip()
    if not s or len(s) > 200:
        return False
    if _NB_SECTION_STUB.match(s):
        return True
    return False


# fn: _line_looks_like_notebook_plot_line | tr: plt.scatter gibi plot satırı mı / en: is matplotlib/sklearn plot line
def _line_looks_like_notebook_plot_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    sl = s.lower()
    words = s.split()
    if len(words) >= 10 and _LLM_CONTEXT_PROSE_CUE.search(s):
        return False
    if _NB_IMPORT_LINE.match(s):
        return True
    if _NB_SECTION_STUB.match(s):
        return True
    if _NB_PLOT_CALLS.search(s) and len(s) < 220:
        return True
    if _NB_PLOT_API.search(s) and len(s) < 260:
        return True
    if _NB_SKLEARN_HELPERS.search(s) and len(s) < 220 and "=" in s:
        return True
    # Garbled low-space token runs dominated by plot/code tokens
    if 12 <= len(s) <= 140:
        hits = sum(1 for tok in ("plt", "fig", "ax", "relu", "sklearn", "scatter", "subplot", "numpy", "pandas") if tok in sl)
        spaces = s.count(" ")
        if hits >= 3 and spaces <= max(6, len(s) // 18):
            return True
    # Tiny assignment RHS looks like API call
    if len(s) < 95 and "=" in s and "(" in s and _NB_PLOT_API.search(s):
        return True
    return False


# fn: notebook_noise_penalty_for_chunk | tr: rag'de kod ağırlıklı chunk cezası / en: rag penalty for code-heavy chunks
def notebook_noise_penalty_for_chunk(chunk: str) -> float:
    c = chunk or ""
    if len(c) < 40:
        return 0.0
    low = c.lower()
    hits = 0
    hits += len(_NB_PLOT_API.findall(c))
    hits += len(_NB_PLOT_CALLS.findall(c))
    hits += len(re.findall(r"\bsklearn\.|import sklearn|from sklearn\b", low))
    hits += len(_NB_SKLEARN_HELPERS.findall(c))
    if _LLM_CONTEXT_PROSE_CUE.search(c):
        hits = max(0, hits - 3)
    return min(0.24, hits * 0.028)


# fn: _line_has_many_numbers | tr: satır cok sayı içeriyor mu / en: does line have many numbers
def _line_has_many_numbers(line: str) -> bool:
    s = (line or "").strip()
    if len(s) < 12:
        return False
    L = len(s)
    digit_ratio = sum(c.isdigit() for c in s) / max(L, 1)
    groups = re.findall(r"\d+(?:[.,]\d+)?", s)
    if digit_ratio >= 0.17 and L >= 22:
        return True
    if len(groups) >= 4:
        return True
    return False


# fn: _line_structurally_looks_like_code | tr: import/def/class gibi kod yapısı mı/ en: structural code signals on line
def _line_structurally_looks_like_code(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    sl = s.lower()
    if sl.startswith("import ") or re.match(r"^\s*from\s+\S+\s+import\b", sl):
        return True
    if re.match(r"^\s*(def|class)\s+\w", sl):
        return True
    if s.count("__") >= 2:
        return True
    # word.method( — avoids "Dr. Smith" (no paren)
    if re.search(r"[A-Za-z0-9_]\.[a-z_][a-z0-9_]*\s*\(", s):
        return True
    b = s.count("[") + s.count("]")
    if b >= 4 and "[" in s and "]" in s:
        return True
    if s.count(";") >= 2:
        return True
    if re.search(r"=\s*[\w.]+\s*\(", s):
        return True
    if re.search(r"=\s*[\[{]", s):
        return True
    return False


# fn: _line_looks_like_natural_sentence | tr: dogal cümle formu mu / en: looks like natural prose sentence
def _line_looks_like_natural_sentence(line: str) -> bool:
    s = (line or "").strip()
    if len(s) < 18:
        return False
    words = s.split()
    if len(words) < 6:
        return False
    alpha = sum(c.isalpha() for c in s)
    if alpha / max(len(s), 1) < 0.42:
        return False
    last = s[-1]
    if last in ".?!":
        return True
    if len(words) >= 8 and "," in s:
        return True
    return False


# fn: _line_has_explanation_cues | tr: açıklama ipuçları var mı (is, means) / en: has explanation cues
def _line_has_explanation_cues(line: str) -> bool:
    low = line.lower()
    if re.search(r"\b(is|means|works)\b", low):
        return True
    if "refers to" in low:
        return True
    if "used for" in low:
        return True
    return False


# fn: _line_llm_context_line_score | tr: llm'e gidecek satır kalite skoru / en: line quality score for llm context
def _line_llm_context_line_score(line: str) -> int:
    s = (line or "").strip()
    if not s:
        return -999
    score = 0
    words = s.split()
    nw = len(words)

    if _line_has_many_numbers(s):
        score -= 2
    if _line_structurally_looks_like_code(s):
        score -= 3
    if nw < 5:
        score -= 1

    if _line_has_explanation_cues(s):
        score += 3
    if _line_looks_like_natural_sentence(s):
        score += 2
    return score


# fn: _salvage_prose_sentences_from_chunk | tr: çok silindiyse anlamlı cümleleri kurtar / en: salvage prose sentences if over-filtered
def _salvage_prose_sentences_from_chunk(text: str) -> str:
    raw = (text or "").replace("\r", "\n")
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", raw) if p.strip()]
    kept: List[str] = []
    for p in parts:
        if _line_llm_context_line_score(p) >= 1:
            kept.append(p)
    return " ".join(kept).strip()


# fn: clean_chunk_text_for_llm_context | tr: chunk'tan llm için anlamlı satırları tut / en: keep meaningful lines from chunk for llm
def clean_chunk_text_for_llm_context(chunk: str, *, min_chars: int = 80) -> Tuple[str, int]:
    raw = (chunk or "").replace("\r\n", "\n")
    if not raw.strip():
        return "", 0
    lines = raw.split("\n")
    removed = 0
    kept: List[str] = []
    for line in lines:
        if _line_llm_context_line_score(line) < 1:
            removed += 1
            continue
        kept.append(line.rstrip())
    out = "\n".join(ln for ln in kept if ln.strip()).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    floor = max(40, min_chars // 2)
    if len(out) >= floor:
        return out, removed
    salv = _salvage_prose_sentences_from_chunk(raw)
    if len(salv) >= floor:
        return salv, removed
    if out.strip():
        return out, removed
    if salv.strip():
        return salv.strip(), removed
    return raw.strip()[: min(2000, len(raw))], removed


# fn: _insert_import_boundaries | tr: import satırlarına satır sonu ekle / en: insert newlines before import lines
def _insert_import_boundaries(text: str) -> str:
    t = text
    t = re.sub(
        r"(?<![a-zA-Z0-9_])from\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s+import\s",
        r"\nfrom \1 import ",
        t,
    )
    t = re.sub(
        r"(?<![a-zA-Z0-9_])import\s+",
        r"\nimport ",
        t,
    )
    return t


# fn: prepare_text_for_document_summary | tr: özet öncesi kod/setup satırlarını at / en: drop code/setup before summary
def prepare_text_for_document_summary(text: str) -> str:
    if not text or not text.strip():
        return text
    t = _insert_import_boundaries(text)
    lines: List[str] = []
    seen: set[str] = set()
    dup_count: dict[str, int] = {}
    for raw in t.split("\n"):
        line = re.sub(r"^(In|Out)\s*\[\d+\]\s*:\s*", "", raw.strip(), flags=re.IGNORECASE).strip()
        if not line:
            continue
        if _line_is_setup_or_runtime_noise(line):
            continue
        if _line_is_notebook_section_stub(line):
            continue
        if _line_looks_like_notebook_plot_line(line):
            continue
        if _line_code_score(line) > 12.0:
            if not lines or lines[-1] != "__CODE_OMITTED__":
                lines.append("__CODE_OMITTED__")
            continue
        # Drop repeated boilerplate/noise lines while preserving short rare lines.
        nkey = re.sub(r"\s+", " ", line.lower())
        dup_count[nkey] = dup_count.get(nkey, 0) + 1
        if dup_count[nkey] > 2 and len(nkey) > 28:
            continue
        if nkey in seen and len(nkey) > 48:
            continue
        seen.add(nkey)
        lines.append(line)

    omit_note = (
        "[Notebook/code sections omitted: imports, setup, prints, random seeds, training logs, and plotting "
        "boilerplate—conceptual narrative below remains.]"
    )
    parts: List[str] = []
    for x in lines:
        if x == "__CODE_OMITTED__":
            if not parts or parts[-1] != omit_note:
                parts.append(omit_note)
        else:
            parts.append(x)
    out = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# fn: clean_retrieved_chunks | tr: rag sonrası chunk'ları hafif temizle / en: light cleanup after rag retrieval
def clean_retrieved_chunks(chunks: Sequence[str], *, min_chars: int = 50) -> List[str]:
    mc = max(1, int(min_chars))
    cleaned: List[str] = []
    for raw in chunks or []:
        chunk = str(raw or "")
        chunk = re.sub(r"In\s*\[\d+\]:[^\n]*", " ", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"Out\s*\[\d+\]:[^\n]*", " ", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if len(chunk) > mc:
            cleaned.append(chunk)
    return cleaned


# Model / PDF table artifacts: long runs of hyphens, underscores, rules, en/em dashes.
_INLINE_DECORATIVE_DASH_RUN = re.compile(r"[-_=–—─━]{6,}")
# Line that is only decorative separators (no prose) — drop entirely.
_LINE_ONLY_DECORATIVE = re.compile(r"^[\s\-_=–—─━]{8,}$")


# fn: strip_decorative_dash_runs | tr: uzun tire/alt çizgi gürültüsünü sil / en: strip decorative dash runs
def strip_decorative_dash_runs(text: str) -> str:
    if not text or not text.strip():
        return (text or "").strip()
    out: List[str] = []
    for raw in text.split("\n"):
        ln = raw.rstrip()
        if _LINE_ONLY_DECORATIVE.match(ln):
            continue
        ln = _INLINE_DECORATIVE_DASH_RUN.sub(" ", ln)
        ln = re.sub(r" {2,}", " ", ln).rstrip()
        out.append(ln)
    t = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# Common LLM/OCR slips in study Markdown (English prompts); safe to collapse when adjacent.
_DUP_PROSE_TOKEN = re.compile(
    r"\b("
    r"the|a|an|and|or|for|to|of|in|on|at|as|by|"
    r"is|are|was|were|be|been|being|"
    r"not|no|so|do|does|did|have|has|had|"
    r"will|would|could|should|must|might|may|can"
    r")\s+\1\b",
    re.IGNORECASE,
)


# fn: collapse_adjacent_duplicate_words | tr: tekrarlanan kelimeleri birleştir (the the) / en: collapse duplicate adjacent words
def collapse_adjacent_duplicate_words(text: str) -> str:
    if not (text or "").strip():
        return (text or "").strip()
    t = text
    for _ in range(8):
        n = _DUP_PROSE_TOKEN.sub(r"\1", t)
        if n == t:
            break
        t = n
    return t


# fn: normalize_prose_punctuation_runs | tr: noktalama tekrarlarını düzelt / en: normalize repeated punctuation
def normalize_prose_punctuation_runs(text: str) -> str:
    if not (text or "").strip():
        return (text or "").strip()
    t = text
    t = re.sub(r"([,;:!?])\1+", r"\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"(?<!\.\.)\.\.(?!\.)", ".", t)
    t = re.sub(r"\s+([,;:!?.])", r"\1", t)
    return t


# fn: _collapse_duplicate_list_markers | tr: çift madde işaretini tek yap / en: collapse duplicate list markers
def _collapse_duplicate_list_markers(line: str) -> str:
    if not re.match(r"^\s*[-*+•]", line):
        return line
    s = line
    for _ in range(4):
        prev = s
        s = re.sub(r"^(\s*)•\s*-\s+", r"\1• ", s)
        s = re.sub(r"^(\s*)-\s*•\s+", r"\1- ", s)
        s = re.sub(r"^(\s*)•\s*•\s+", r"\1• ", s)
        s = re.sub(r"^(\s*)-\s*-\s+", r"\1- ", s)
        s = re.sub(r"^(\s*)\*\s*-\s+", r"\1* ", s)
        s = re.sub(r"^(\s*)\+\s*-\s+", r"\1+ ", s)
        if s == prev:
            break
    return s


# fn: _ensure_one_bullet_per_line | tr: her madde ayrı satırda olsun / en: one markdown bullet per line
def _ensure_one_bullet_per_line(text: str) -> str:
    if not (text or "").strip():
        return (text or "").strip()
    normalized = re.sub(r"([.!?])\s*-\s+(?=[A-ZÇĞİÖŞÜ0-9])", r"\1\n- ", text or "")
    normalized = re.sub(r"([.!?])\s*[•\u2022]\s*(?=[A-ZÇĞİÖŞÜ0-9])", r"\1\n- ", normalized)
    out: List[str] = []
    for raw in normalized.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            out.append("")
            continue

        bullet_match = re.match(r"^(\s*)[-*+•]\s*(.+)$", line)
        if bullet_match:
            indent, body = bullet_match.group(1), bullet_match.group(2).strip()
            if not body:
                continue
            chunks = re.split(r"\s*[•\u2022]\s*", body)
            parts: List[str] = []
            for chunk in chunks:
                parts.extend(
                    [p.strip() for p in re.split(r"(?<=[.!?])\s*-\s+(?=[A-ZÇĞİÖŞÜ0-9])", chunk) if p.strip()]
                )
            if not parts:
                continue
            for part in parts:
                out.append(f"{indent}- {part}")
            continue

        # Fallback: plain lines with inline bullets / merged `.-` style separators.
        inline_parts = [p.strip() for p in re.split(r"\s*[•\u2022]\s*", line) if p.strip()]
        if len(inline_parts) < 2:
            inline_parts = [
                p.strip()
                for p in re.split(r"(?<=[.!?])\s*-\s+(?=[A-ZÇĞİÖŞÜ0-9])", line)
                if p.strip()
            ]
        if len(inline_parts) >= 2 and all(len(p) >= 12 for p in inline_parts):
            for part in inline_parts:
                out.append(f"- {part}")
            continue

        out.append(line)
    return "\n".join(out)


# fn: final_clean_markdown_output | tr: özet çıktısını son kez temizle / en: final cleanup of summary markdown output
def final_clean_markdown_output(text: str) -> str:
    if not text or not text.strip():
        return (text or "").strip()
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _repair_ocr_word_glue(t)
    t = collapse_adjacent_duplicate_words(t)
    t = normalize_prose_punctuation_runs(t)
    t = _ensure_one_bullet_per_line(t)
    t = re.sub(r"\b(?:and|ve)\s+\d{1,2}\.?\s+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r",\s*\d{1,2}\.\s+", ", ", t)
    lines: List[str] = []
    for line in t.split("\n"):
        ln = re.sub(r"[ \t]{2,}", " ", line)
        ln = _collapse_duplicate_list_markers(ln)
        if not re.match(r"^\s*#", ln) and not re.match(r"^\s*[-*+•]\s*\S", ln):
            ln = re.sub(r"^\s*[^\w#*+\-•\u2013\u2014]+", "", ln)
        lines.append(ln.rstrip())
    t = "\n".join(lines)
    t = re.sub(r"\n{4,}", "\n\n\n", t)
    t = strip_decorative_dash_runs(t)
    return t.strip()


# fn: dedupe_similar_markdown_bullets | tr: benzer maddeleri tekrar etme / en: dedupe similar markdown bullets
def dedupe_similar_markdown_bullets(text: str, *, prefix_len: int = 50) -> str:
    if not text or not text.strip():
        return ""
    pl = max(20, min(int(prefix_len), 120))
    seen: set[str] = set()
    out: List[str] = []
    for line in text.split("\n"):
        if re.match(r"^\s*##\s", line):
            seen.clear()
        m = re.match(r"^(\s*[-*+•]\s*)(.+)$", line)
        if m:
            body = m.group(2)
            key = re.sub(r"\s+", " ", body[:pl]).strip().lower()
            if key:
                if key in seen:
                    continue
                seen.add(key)
        out.append(line)
    return "\n".join(out)

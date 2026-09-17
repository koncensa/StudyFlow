# svc: pdf | tr: pdf dosyasından düz metin çıkar / en: extract plain text from pdf file

import os

from app.services.pdf_text_clean import clean_extracted_pdf_text


# fn: _normalize_piece | tr:boşlukları tekilleştir (kopya sayfa karşılaştırma) / en: normalize whitespace for duplicate page check
def _normalize_piece(text: str) -> str:
    return " ".join((text or "").split())


# fn: _extract_with_pymupdf | tr: pymupdf (fitz) ile metin çıkar / en: extract text with pymupdf (fitz)
def _extract_with_pymupdf(pdf_path: str) -> str:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        parts: list[str] = []
        for page in doc:
            # tr: önce normal mod, boş ise blocks modu / en: try text mode first, then blocks mode
            txt = (page.get_text("text", sort=True) or "").strip()
            if not txt:
                blocks = page.get_text("blocks", sort=True) or []
                block_lines: list[str] = []
                for b in blocks:
                    if len(b) >= 5 and isinstance(b[4], str):
                        s = b[4].strip()
                        if s:
                            block_lines.append(s)
                txt = "\n".join(block_lines).strip()
            if txt:
                # tr: ard arda aynı sayfa metnini atlama / en: skip near-duplicate consecutive pages
                if not parts or _normalize_piece(parts[-1]) != _normalize_piece(txt):
                    parts.append(txt)
        return "\n\n".join(parts)
    finally:
        doc.close()


# fn: _extract_with_pypdf2 | tr: pypdf2 ile yedek metin çıkar / en: fallback text extraction with pypdf2
def _extract_with_pypdf2(pdf_path: str) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


# fn: extract_text_from_pdf | tr: ana fonksiyon: pdf -> temiz metin / en: main entry: pdf to cleaned text
def extract_text_from_pdf(pdf_path: str) -> str:
    backend = os.getenv("PDF_EXTRACT_BACKEND", "pymupdf").strip().lower()
    raw = ""
    if backend != "pypdf2":
        try:
            raw = _extract_with_pymupdf(pdf_path)
        except Exception:
            raw = ""
    # tr: pymupdf boş/hatalı ise pypdf2 dene / en: fallback to pypdf2 if pymupdf empty or failed
    if not raw.strip():
        try:
            raw = _extract_with_pypdf2(pdf_path)
        except Exception:
            raw = ""
    return clean_extracted_pdf_text(raw)

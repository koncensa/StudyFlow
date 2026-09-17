"""Integration-style tests with mocked LLM (no external API)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.services import summary_service as ss
from app.services.document_store import StoredDocument


def _fake_doc(study_text: str, *, did: str = "test-doc-integration") -> StoredDocument:
    """Minimal in-memory document without running full PDF ingest."""
    words = study_text.split()
    chunks = []
    step = max(80, len(words) // 6 or 1)
    for i in range(0, len(words), step):
        chunks.append(" ".join(words[i : i + step]))
    if len(chunks) < 2:
        chunks = [study_text, study_text + " extra padding."]
    return StoredDocument(
        document_id=did,
        user_id=1,
        filename="fixture.txt",
        study_text=study_text,
        chunks=chunks,
        chunk_topics=["topic"] * len(chunks),
        chunk_embeddings=[None] * len(chunks),
        chunk_topic_embeddings=[None] * len(chunks),
        content_hash="x",
        created_at=0.0,
    )


def test_generate_summary_records_metrics_with_mocked_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MAP_REDUCE", "true")
    monkeypatch.setenv("SUMMARY_MAP_REDUCE_MIN_WORDS", "120")
    monkeypatch.setenv("SUMMARY_MAP_MIN_CHUNK_WORDS", "8")
    monkeypatch.setenv("SUMMARY_MAP_MIN_LETTER_RATIO", "0.12")
    monkeypatch.setenv("SUMMARY_MAP_PARALLEL_WORKERS", "2")
    monkeypatch.setenv("STUDY_MODES_CLOUD_FIRST", "false")
    body = " ".join([f"paragraph{j}" for j in range(400)])
    fat_a = " ".join(["tok"] * 200)
    fat_b = " ".join(["tokb"] * 200)

    def fake_ollama_cm(
        messages: list,
        system: str | None = None,
        **kwargs: object,
    ) -> str:
        user_payload = ""
        for m in messages or []:
            if (m.get("role") or "") == "user":
                user_payload = str(m.get("content") or "")
                break
        if "Excerpt block" in user_payload:
            return "- First idea from fragment.\n- Second idea from fragment."
        return "- Summary bullet one.\n- Summary bullet two.\n- Summary bullet three."

    doc = _fake_doc(body)
    ollama_cm = MagicMock(side_effect=fake_ollama_cm)
    with patch.object(ss, "_retrieve_relevant_chunks_from_document", return_value=[fat_a, fat_b]):
        with patch("app.services.ollama_service.ollama_available", return_value=True):
            with patch("app.services.ollama_service.ollama_chat_messages", ollama_cm):
                out = ss.generate_summary(
                    doc,
                    mode="summary",
                    level="normal",
                    summary_format="bullets",
                    summary_length="medium",
                )
    assert ollama_cm.call_count > 0
    assert out.pipeline_llm_calls > 0
    assert out.pipeline_duration_ms >= 0
    assert isinstance(out.pipeline_used_map_reduce, bool)
    assert out.text


@pytest.mark.skipif(
    not os.getenv("STUDYFLOW_RUN_PDF_INTEGRATION"),
    reason="Set STUDYFLOW_RUN_PDF_INTEGRATION=1 and place backend/tests/fixtures/sample.pdf to run.",
)
def test_real_pdf_fixture_if_present() -> None:
    import pathlib

    p = pathlib.Path(__file__).resolve().parent / "fixtures" / "sample.pdf"
    if not p.is_file():
        pytest.skip("sample.pdf missing")
    pytest.importorskip("fitz", reason="PyMuPDF optional")
    # If you add real ingest here, wire pdf_text extraction + create_document.
    assert p.stat().st_size > 0

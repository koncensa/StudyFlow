"""Unit tests for summary pipeline helpers (no live LLM)."""

import os

import pytest

from app.services import summary_service as ss


def test_normalize_summary_format_enum_defaults_to_bullets() -> None:
    assert ss.normalize_summary_format_enum(None) == "bullets"
    assert ss.normalize_summary_format_enum("") == "bullets"
    assert ss.normalize_summary_format_enum("  ") == "bullets"


def test_normalize_summary_format_enum_aliases() -> None:
    assert ss.normalize_summary_format_enum("PROSE") == "prose"
    assert ss.normalize_summary_format_enum("Mixed") == "mixed"
    assert ss.normalize_summary_format_enum("bullets") == "bullets"


def test_map_chunks_quality_filter_drops_tiny_chunks() -> None:
    tiny = "word " * 5  # well below default min words
    good = " ".join([f"word{i}" for i in range(80)])
    out, dropped = ss._map_chunks_quality_filter([tiny, good, good])
    assert len(out) == 2
    assert dropped >= 1


def test_map_reduce_should_run_respects_word_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MAP_REDUCE", "true")
    monkeypatch.setenv("SUMMARY_MAP_REDUCE_MIN_WORDS", "200")
    monkeypatch.setenv("SUMMARY_MAP_MIN_CHUNK_WORDS", "12")
    monkeypatch.setenv("SUMMARY_MAP_MIN_LETTER_RATIO", "0.15")
    a = " ".join(["alpha"] * 120)
    b = " ".join(["beta"] * 120)
    assert ss._map_reduce_should_run([a, b]) is True


def test_map_reduce_should_run_false_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MAP_REDUCE", "false")
    a = " ".join(["alpha"] * 500)
    b = " ".join(["beta"] * 500)
    assert ss._map_reduce_should_run([a, b]) is False


def test_retrieval_top_k_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_RETRIEVAL_TOP_K_EXTRA", "99")
    k = ss._retrieval_top_k_for_mode("summary", "long")
    assert k == 18


def test_chunk_ok_for_map_letter_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MAP_MIN_CHUNK_WORDS", "10")
    monkeypatch.setenv("SUMMARY_MAP_MIN_LETTER_RATIO", "0.5")
    prose = "This is a normal English paragraph with enough words to pass the minimum word gate easily."
    assert ss._chunk_ok_for_map(prose) is True
    noise = "@@@ ### $$$ %%% " * 30
    assert ss._chunk_ok_for_map(noise) is False


def test_extractive_fallback_bullets_nonempty() -> None:
    chunks = [" ".join([f"sentence{j} word" for j in range(40)]) for _ in range(3)]
    out = ss._extractive_fallback_bullets(chunks, max_bullets=5)
    assert out
    assert "- " in out


def test_merge_digest_bullet_lines_counts_dashes() -> None:
    assert ss._merge_digest_bullet_lines("- a\n- b\n") == 2


def test_skip_llm_quality_retry_on_fallback() -> None:
    pm = ss.SummaryPipelineMetrics(fallback_path="extractive")
    assert ss._skip_llm_quality_retry(pm) is True
    pm2 = ss.SummaryPipelineMetrics()
    assert ss._skip_llm_quality_retry(pm2) is False


def test_summary_result_cache_key_core_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_RESULT_CACHE_ENABLED", "true")
    monkeypatch.setenv("SUMMARY_RESULT_CACHE_KEY_MODE", "core")
    from app.services.document_store import StoredDocument

    doc = StoredDocument(
        document_id="doc-cache-core-12345678",
        user_id=1,
        filename="x.pdf",
        study_text="hello",
        chunks=["a"],
        chunk_topics=["t"],
        chunk_embeddings=[None],
        chunk_topic_embeddings=[None],
        content_hash="samehash",
        created_at=0.0,
    )
    k1 = ss._summary_result_cache_key(
        doc,
        mode="summary",
        level="normal",
        summary_format="bullets",
        summary_length="short",
        summary_style="balanced",
        focus_topics=["a"],
        challenge_topics=[],
    )
    k2 = ss._summary_result_cache_key(
        doc,
        mode="summary",
        level="normal",
        summary_format="bullets",
        summary_length="long",
        summary_style="detailed",
        focus_topics=["b"],
        challenge_topics=["z"],
    )
    assert k1 == k2


def test_classify_summary_quality_partial_on_fallback() -> None:
    pm = ss.SummaryPipelineMetrics(partial_output=True)
    q = ss._classify_summary_quality(
        "- One\n- Two\n- Three\n- Four\n- Five",
        pm,
        normalized_mode="summary",
        normalized_level="normal",
        normalized_length="medium",
        normalized_format="bullets",
    )
    assert q == "partial"


def test_build_fallback_reason_joins_tags() -> None:
    pm = ss.SummaryPipelineMetrics(
        fallback_path="extractive",
        partial_output=True,
        recovered_after_cloud_timeout=True,
    )
    assert "cloud_timeout" in ss._build_fallback_reason(pm)


def test_summary_result_cache_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_RESULT_CACHE_ENABLED", "true")
    monkeypatch.setenv("SUMMARY_RESULT_CACHE_TTL_SECONDS", "600")
    from app.services.document_store import StoredDocument

    doc = StoredDocument(
        document_id="doc-cache-test-12345678",
        user_id=1,
        filename="x.pdf",
        study_text="hello",
        chunks=["a"],
        chunk_topics=["t"],
        chunk_embeddings=[None],
        chunk_topic_embeddings=[None],
        content_hash="hashabc123",
        created_at=0.0,
    )
    k = ss._summary_result_cache_key(
        doc,
        mode="summary",
        level="normal",
        summary_format="bullets",
        summary_length="medium",
        summary_style="balanced",
        focus_topics=[],
        challenge_topics=[],
    )
    assert k
    blob = {
        "text": "- Cached line.",
        "mode": "summary",
        "level": "normal",
        "retrieved_chunks": ["x"],
        "pipeline_duration_ms": 12.0,
        "pipeline_llm_calls": 2,
        "pipeline_used_map_reduce": False,
        "pipeline_map_chunks_in": 1,
        "pipeline_map_chunks_after_quality": 1,
        "pipeline_map_chunks_skipped_quality": 0,
        "pipeline_map_chunks_out": 0,
        "pipeline_map_phase_ms": 0.0,
        "pipeline_cache_hit": False,
        "pipeline_fallback_path": "",
        "summary_quality": "good",
        "summary_warning": None,
        "used_fallback": False,
        "fallback_reason": "",
        "summary_quality_warning": None,
        "summary_generation_note": "Old note.",
    }
    ss._summary_result_cache_set(k, blob)
    got = ss._summary_result_cache_get(k)
    assert got and got.get("text") == "- Cached line."

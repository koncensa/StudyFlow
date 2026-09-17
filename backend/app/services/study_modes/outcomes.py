from __future__ import annotations

import warnings
from typing import Literal

from app.services.document_store import StoredDocument

OutcomeKind = Literal["exam_focus", "quick_summary", "explain_overview"]

def outcome_for(doc: StoredDocument, kind: OutcomeKind, explain_level: str = "normal") -> str:
    """
    DEPRECATED: no longer produces summary-like content.
    Summary/explain/exam outputs must come from summary_service.generate_summary().
    """
    _ = doc
    _ = kind
    _ = explain_level
    warnings.warn(
        "outcome_for() is deprecated for summary-like outputs; use generate_summary().",
        DeprecationWarning,
        stacklevel=2,
    )
    return (
        "Study outcome generation moved to the unified summary pipeline. "
        "Use /pdf/generate-summary (generate_summary) for summary/explain/exam output."
    )

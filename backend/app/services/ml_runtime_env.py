# svc: ml_runtime_env | tr: huggingface/transformers ortamını ayarla, log gürültüsünü azalt / en: configure hf/transformers env, reduce log noise

from __future__ import annotations

import logging
import os
import warnings
from typing import Optional

_configured = False


# fn: configure_ml_runtime_env | tr: ml kütüphaneleri yüklenmeden önce bir kez çalıştır / en: run once before ml libs load
def configure_ml_runtime_env(*, force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    _configured = True

    # cfg: hf telemetry ve progress bar kapat / en: disable hf telemetry and progress bars
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # cfg: transformers load report tablosunu gizle / en: hide transformers load report table
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

    # tr: hf token uyarılarını filtrele / en: filter hf token warning messages
    for pat in (
        ".*HF_TOKEN.*",
        ".*Hugging Face.*",
        ".*HuggingFace.*",
        ".*unauthenticated.*",
        ".*HF Hub.*",
    ):
        warnings.filterwarnings("ignore", message=pat, category=UserWarning)
        warnings.filterwarnings("ignore", message=pat, category=FutureWarning)

    _quiet_logger("transformers")
    _quiet_logger("transformers.utils.loading_report")
    _quiet_logger("sentence_transformers")
    _quiet_logger("huggingface_hub")
    _quiet_logger("huggingface_hub.utils")
    _quiet_logger("huggingface_hub.utils._http")
    _quiet_logger("huggingface_hub.file_download")
    _quiet_logger("urllib3")


# fn: _quiet_logger | tr: logger seviyesini error yap / en: set logger level to error
def _quiet_logger(name: str, level: int = logging.ERROR) -> None:
    lg: Optional[logging.Logger] = logging.getLogger(name)
    if lg is not None:
        lg.setLevel(level)

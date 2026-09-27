"""faster-whisper ohne torch und transformers laden.

faster-whisper laedt sonst torch und transformers mit - 1,4 s pro Start fuer
zwei Pakete, die dieses Projekt weder auffuehrt noch aufruft. Der Vorlauf muss
vor dem Import stehen, sonst ist er laengst gelaufen.
"""

from wisper.models import preload_backend_without_torch

preload_backend_without_torch()

from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402

__all__ = ["BatchedInferencePipeline", "WhisperModel"]

"""faster-whisper, imported after the CUDA libraries were made reachable (see backend_preload.py)."""

from hams.backend_preload import add_cuda_libraries

add_cuda_libraries()

from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402

__all__ = ["BatchedInferencePipeline", "WhisperModel"]

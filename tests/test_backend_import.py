"""The CUDA libraries must be reachable once faster-whisper is imported, and the start must not pull in torch.

CTranslate2 needs cuBLAS and cuDNN for the GPU but does not bring them; `backend_preload` registers them from the
NVIDIA wheels or the app's download before `hams.backend` imports faster-whisper. Hams' own dependencies never
include torch: the start has to stay without it.

Measured from outside: in the running test process faster-whisper is long imported, and what is in `sys.modules`
says nothing about the order at start.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def _in_a_fresh_interpreter(code: str, tmp_path: Path) -> dict:
    """Fuehrt Code in einem eigenen Interpreter aus und liest sein JSON."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=PROJECT_ROOT,
        # Die Umgebung wird geerbt und nur ergaenzt. Eine kuenstlich magere
        # Umgebung ohne SystemDrive/ProgramData laesst Windows Pfade nicht
        # aufloesen und legt einen Ordner namens "%SystemDrive%" im Projekt an.
        env={
            **os.environ,
            "HAMS_CONFIG": str(tmp_path / "config.toml"),
            "HAMS_LOG_DIR": str(tmp_path / "logs"),
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestTheStart:
    @pytest.mark.skipif(importlib.util.find_spec("torch") is not None, reason="torch is installed in this environment")
    def test_importing_the_app_leaves_torch_alone(self, tmp_path):
        bericht = _in_a_fresh_interpreter(
            """
            import json, sys
            import hams.main
            print(json.dumps({
                "torch": "torch" in sys.modules,
                "transformers": "transformers" in sys.modules,
                "backend": "faster_whisper" in sys.modules,
            }))
        """,
            tmp_path,
        )
        assert bericht["backend"] is True, "faster-whisper muss geladen sein"
        assert bericht["torch"] is False
        assert bericht["transformers"] is False

    def test_the_backend_is_usable(self, tmp_path):
        bericht = _in_a_fresh_interpreter(
            """
            import json
            from hams.backend import BatchedInferencePipeline, WhisperModel
            print(json.dumps({
                "klasse": WhisperModel.__name__,
                "kann_transkribieren": callable(WhisperModel.transcribe),
                "gebatcht": BatchedInferencePipeline.__name__,
            }))
        """,
            tmp_path,
        )
        assert bericht == {
            "klasse": "WhisperModel",
            "kann_transkribieren": True,
            "gebatcht": "BatchedInferencePipeline",
        }

    def test_the_old_import_guard_is_gone(self):
        """Audit finding H16: hiding torch during the import only helped environments that happen to have it."""
        from hams import backend_preload

        assert not hasattr(backend_preload, "preload_backend_without_torch")
        assert [package for package, _ in backend_preload.CUDA_LIBRARY_PACKAGES] == ["nvidia.cublas", "nvidia.cudnn"]


class TestTheCudaLibrariesStayReachable:
    def test_a_directory_is_registered(self):
        from hams.backend_preload import add_cuda_libraries

        folder = add_cuda_libraries()
        if not folder:
            pytest.skip("hier liegen keine CUDA-Bibliotheken")
        assert all(Path(o).is_dir() for o in folder), folder

    def test_the_registered_directories_hold_libraries(self):
        """Ein leeres Verzeichnis anzumelden waere ein stiller Fehlschlag."""
        from hams.backend_preload import add_cuda_libraries

        folder = add_cuda_libraries()
        if not folder:
            pytest.skip("hier liegen keine CUDA-Bibliotheken")
        muster = "*.dll" if sys.platform == "win32" else "*.so*"
        for o in folder:
            assert list(Path(o).glob(muster)), o

    @pytest.mark.skipif(sys.platform != "win32", reason="nur Windows")
    def test_the_standalone_cublas_is_in_the_process_before_ctranslate2_needs_it(self, tmp_path):
        """ctranslate2 laedt cuBLAS erst beim ersten Rechnen nach, und zwar ueber
        den PATH. Ein mit add_dll_directory angemeldeter Ordner reicht dafuer nicht:
        mit nvidia-cublas-cu12 scheiterte die erste Transkription an

            RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
        """
        bericht = _in_a_fresh_interpreter(
            """
            import ctypes, importlib.util, json
            import hams.backend
            # erst das Elternpaket: find_spec("nvidia.cublas") wirft, wenn es "nvidia" gar nicht gibt
            vorhanden = importlib.util.find_spec("nvidia") is not None and (
                importlib.util.find_spec("nvidia.cublas") is not None
            )
            handle = ctypes.windll.kernel32.GetModuleHandleW("cublas64_12.dll")
            print(json.dumps({"vorhanden": vorhanden, "im_prozess": bool(handle)}))
        """,
            tmp_path,
        )
        if not bericht["vorhanden"]:
            pytest.skip("nvidia-cublas-cu12 ist hier nicht installiert")
        assert bericht["im_prozess"] is True, "cuBLAS muss vorgeladen sein, sonst faellt die GPU aus"

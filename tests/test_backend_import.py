"""Der Python-Import von torch gehoert nicht in den Start — seine DLLs schon.

`ctranslate2/__init__.py` importiert `converters` bedingungslos, und dort wird
torch versucht — geschuetzt mit `try: import torch / except ImportError: pass`.
Gemessen kostet das 943 ms fuer torch und 651 ms fuer transformers, bei jedem
Start, fuer zwei Pakete, die in keiner Abhaengigkeitsliste dieses Projekts
stehen und deren Python-API hier niemand aufruft.

Von torch gebraucht wird trotzdem etwas, nur auf einer Ebene, die kein Grep
nach `import torch` findet: sein Ordner `lib` liefert cublas64_12.dll und
cudnn64_9.dll, und ohne die faellt CUDA aus. Beides ist zu trennen — die
Bibliotheken anmelden, den Import sparen.

Gemessen werden muss das von aussen: im laufenden Testprozess ist
faster-whisper laengst importiert, und was einmal in `sys.modules` steht,
verraet nichts mehr ueber die Reihenfolge beim Start.
"""

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
            "WISPER_CONFIG": str(tmp_path / "config.toml"),
            "WISPER_LOG_DIR": str(tmp_path / "logs"),
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestDerStartZiehtTorchNichtMit:

    def test_importing_the_app_leaves_torch_alone(self, tmp_path):
        bericht = _in_a_fresh_interpreter("""
            import json, sys
            import wisper.main
            print(json.dumps({
                "torch": "torch" in sys.modules,
                "transformers": "transformers" in sys.modules,
                "backend": "faster_whisper" in sys.modules,
            }))
        """, tmp_path)
        assert bericht["backend"] is True, "faster-whisper muss geladen sein"
        assert bericht["torch"] is False
        assert bericht["transformers"] is False

    def test_the_backend_is_usable_afterwards(self, tmp_path):
        """Nicht geladen zu haben nuetzt nichts, wenn danach nichts geht."""
        bericht = _in_a_fresh_interpreter("""
            import json, sys
            from wisper.models import preload_backend_without_torch
            ohne_torch = preload_backend_without_torch()
            from faster_whisper import WhisperModel
            print(json.dumps({
                "ohne_torch": ohne_torch,
                "torch": "torch" in sys.modules,
                "klasse": WhisperModel.__name__,
                "kann_transkribieren": callable(WhisperModel.transcribe),
            }))
        """, tmp_path)
        assert bericht["ohne_torch"] is True
        assert bericht["torch"] is False
        assert bericht["klasse"] == "WhisperModel"
        assert bericht["kann_transkribieren"] is True

    def test_a_second_call_changes_nothing(self, tmp_path):
        """Zweimal aufgerufen darf die Sperre nicht erneut greifen."""
        bericht = _in_a_fresh_interpreter("""
            import json, sys
            from wisper.models import preload_backend_without_torch
            erst = preload_backend_without_torch()
            dann = preload_backend_without_torch()
            print(json.dumps({"erst": erst, "dann": dann,
                              "sperren_uebrig": sum(
                                  1 for f in sys.meta_path
                                  if type(f).__name__ == "_AbgeschalteteImporte")}))
        """, tmp_path)
        assert bericht["erst"] is True
        assert bericht["dann"] is True
        assert bericht["sperren_uebrig"] == 0, "Die Sperre muss abgeraeumt sein"


class TestWennDieSperreNichtTraegt:
    """Der Rueckfall. Ein langsamer Start ist hinnehmbar, keiner nicht."""

    def test_a_failing_guarded_import_is_retried_without_it(self, tmp_path):
        """ctranslate2 ist *nicht* mit try/except geschuetzt.

        Wird es mitgesperrt, scheitert der Import unter der Sperre — genau der
        Fall, den der Rueckfall auffangen soll. Er muss dann ohne Sperre
        erneut laden statt die Anwendung stehen zu lassen.
        """
        bericht = _in_a_fresh_interpreter("""
            import json, sys
            sys.path.insert(0, ".")
            from wisper import models
            models._AbgeschalteteImporte.__init__ = (
                lambda self, *n: object.__setattr__(self, "_namen",
                                                    frozenset(n) | {"ctranslate2"}))
            ohne_torch = models.preload_backend_without_torch()
            from faster_whisper import WhisperModel
            print(json.dumps({
                "ohne_torch": ohne_torch,
                "backend": "faster_whisper" in sys.modules,
                "klasse": WhisperModel.__name__,
                "sperren_uebrig": sum(1 for f in sys.meta_path
                                      if type(f).__name__ == "_AbgeschalteteImporte"),
            }))
        """, tmp_path)
        assert bericht["ohne_torch"] is False, "Der Rueckfall muss sich melden"
        assert bericht["backend"] is True, "Die Anwendung muss trotzdem starten"
        assert bericht["klasse"] == "WhisperModel"
        assert bericht["sperren_uebrig"] == 0


class TestDieCudaBibliothekenBleibenErreichbar:
    """Der Fehler, den die erste Fassung dieser Datei nicht gesehen hat.

    torch ist auf einer typischen Windows-Installation die einzige Quelle von
    cublas64_12.dll und cudnn64_9.dll. Sie kamen bisher als Nebenwirkung des
    Imports in den DLL-Suchpfad. Wird nur der Import gespart, meldet die
    Transkription auf der GPU

        RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

    Die erste Fassung dieser Tests hat das nicht bemerkt, weil ihr Vergleich
    auf der CPU lief - dort braucht ctranslate2 kein cuBLAS.
    """

    def test_a_directory_is_registered(self):
        from wisper.models import add_cuda_libraries

        ordner = add_cuda_libraries()
        if not ordner:
            pytest.skip("hier liegen keine CUDA-Bibliotheken")
        assert all(Path(o).is_dir() for o in ordner), ordner

    def test_the_registered_directories_hold_libraries(self):
        """Ein leeres Verzeichnis anzumelden waere ein stiller Fehlschlag."""
        from wisper.models import add_cuda_libraries

        ordner = add_cuda_libraries()
        if not ordner:
            pytest.skip("hier liegen keine CUDA-Bibliotheken")
        for o in ordner:
            assert list(Path(o).glob("*.dll")), o

    def test_the_standalone_packages_come_first(self):
        """Wer nvidia-cublas-cu12 installiert, soll nicht doch torch ziehen.

        Die Reihenfolge ist der Unterschied zwischen einer 1,9 GB und einer
        6,6 GB grossen Installation.
        """
        from wisper.models import CUDA_LIBRARY_PACKAGES

        namen = [paket for paket, _ in CUDA_LIBRARY_PACKAGES]
        assert namen.index("nvidia.cublas") < namen.index("torch")
        assert namen.index("nvidia.cudnn") < namen.index("torch")

    def test_cublas_can_actually_be_loaded_afterwards(self, tmp_path):
        """Nicht der Pfad zaehlt, sondern ob Windows die Bibliothek findet."""
        bericht = _in_a_fresh_interpreter("""
            import ctypes, glob, json, os, sys
            sys.path.insert(0, ".")
            from wisper.models import preload_backend_without_torch
            ohne_torch = preload_backend_without_torch()
            import importlib.util
            spec = importlib.util.find_spec("torch")
            treffer = glob.glob(os.path.join(
                os.path.dirname(spec.origin), "lib", "cublas64_*.dll")) if spec else []
            geladen = None
            if treffer:
                try:
                    ctypes.WinDLL(os.path.basename(treffer[0]))
                    geladen = True
                except OSError:
                    geladen = False
            print(json.dumps({"ohne_torch": ohne_torch, "hat_cublas": bool(treffer),
                              "geladen": geladen, "torch": "torch" in sys.modules}))
        """, tmp_path)
        if not bericht["hat_cublas"]:
            pytest.skip("kein cuBLAS in dieser Umgebung")
        assert bericht["torch"] is False, "torch soll weiterhin ungeladen bleiben"
        assert bericht["geladen"] is True, (
            "cuBLAS ist nach dem Backend-Import nicht auffindbar — "
            "die GPU-Transkription wuerde scheitern"
        )

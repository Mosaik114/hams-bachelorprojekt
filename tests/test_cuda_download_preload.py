"""The downloaded CUDA libraries are found and preloaded; without them the GPU is skipped.

Nothing real is loaded: `os.add_dll_directory` and `ctypes.WinDLL` are replaced.
"""

import json
import sys

import pytest

from hams import backend_preload, gpu_support, whisper_model

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


@pytest.fixture
def downloaded(tmp_path, monkeypatch):
    """A complete downloaded set in a temporary data folder, and no pip packages."""
    monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path))
    folder = tmp_path / "cuda"
    folder.mkdir()
    files = {}
    for _package, names in backend_preload.CUDA_LIBRARIES_WINDOWS:
        for name in names:
            (folder / name).write_bytes(b"dll")
            files[name] = 3
    manifest = {"bundle": gpu_support.bundle_id(), "files": files}
    (folder / gpu_support.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(backend_preload, "_paket_verzeichnis", lambda paket, unterordner: None)
    return folder


@pytest.fixture
def fake_loader(monkeypatch):
    import ctypes

    calls = {"dirs": [], "dlls": []}
    monkeypatch.setattr(backend_preload.os, "add_dll_directory", lambda d: calls["dirs"].append(d), raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: calls["dlls"].append(path), raising=False)
    return calls


@windows_only
class TestDownloadedLibraries:
    def test_the_folder_is_registered_and_the_dlls_preloaded(self, downloaded, fake_loader):
        assert backend_preload.add_cuda_libraries() == (str(downloaded),)
        assert fake_loader["dirs"] == [str(downloaded)]
        assert [p.rsplit("\\", 1)[-1] for p in map(str, fake_loader["dlls"])] == [
            "cublasLt64_12.dll",
            "cublas64_12.dll",
            "cudnn64_9.dll",
        ]
        assert backend_preload.cuda_libraries_found()

    def test_an_incomplete_download_is_ignored(self, downloaded, fake_loader, monkeypatch):
        (downloaded / "cublas64_12.dll").unlink()
        monkeypatch.setattr("ctypes.util.find_library", lambda name: None)
        assert backend_preload.add_cuda_libraries() == ()
        assert fake_loader["dlls"] == []
        assert not backend_preload.cuda_libraries_found()

    def test_a_cuda_toolkit_on_the_path_counts(self, downloaded, monkeypatch):
        (downloaded / gpu_support.MANIFEST_NAME).unlink()
        monkeypatch.setattr("ctypes.util.find_library", lambda name: r"C:\CUDA\bin\cublas64_12.dll")
        assert backend_preload.cuda_libraries_found()


class TestUsableDevices:
    def test_without_cublas_the_gpu_is_skipped(self, monkeypatch):
        monkeypatch.setattr(whisper_model, "cuda_libraries_found", lambda: False)
        assert whisper_model.usable_devices(("cuda", "cpu")) == ("cpu",)

    def test_with_cublas_the_order_stays(self, monkeypatch):
        monkeypatch.setattr(whisper_model, "cuda_libraries_found", lambda: True)
        assert whisper_model.usable_devices(["cuda", "cpu"]) == ("cuda", "cpu")

    def test_cpu_only_never_asks(self, monkeypatch):
        monkeypatch.setattr(whisper_model, "cuda_libraries_found", lambda: pytest.fail("not needed"))
        assert whisper_model.usable_devices(("cpu",)) == ("cpu",)

    def test_off_windows_the_load_attempt_decides(self, monkeypatch):
        monkeypatch.setattr(backend_preload.sys, "platform", "linux")
        assert backend_preload.cuda_libraries_found()

    def test_the_model_matrix_uses_it(self, monkeypatch):
        tried = []

        class FakeModel:
            def __init__(self, model_id, device, compute_type):
                tried.append(device)

        monkeypatch.setattr(whisper_model, "WhisperModel", FakeModel)
        monkeypatch.setattr(whisper_model, "cuda_libraries_found", lambda: False)
        model, device, _info = whisper_model.WhisperModelMixin._build_model(object(), "tiny")
        assert device == "cpu"
        assert tried == ["cpu"]


@windows_only
class TestRegistrationFailures:
    def test_a_folder_windows_refuses_costs_only_the_gpu(self, downloaded, monkeypatch):
        import ctypes

        def refused(_folder):
            raise OSError(87, "The parameter is incorrect")

        monkeypatch.setattr(backend_preload.os, "add_dll_directory", refused, raising=False)
        monkeypatch.setattr(ctypes, "WinDLL", lambda path: None, raising=False)
        assert backend_preload.add_cuda_libraries() == ()

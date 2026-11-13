"""On-demand download of the CUDA libraries (hams/gpu_support.py) - with fake wheels and a fake server.

Nothing here touches the network or the real ``%LOCALAPPDATA%\\Hams``: every
test installs into ``tmp_path`` and serves small zip files built on the fly.
"""

import base64
import errno
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from hams import app_paths, gpu_support


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(name: str, files: dict[str, bytes], *, bad_record: bool = False) -> bytes:
    """A minimal wheel: the given files plus a RECORD with their hashes."""
    buffer = io.BytesIO()
    record_lines = []
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
            recorded = _record_hash(b"other" if bad_record else data)
            record_lines.append(f"{path},{recorded},{len(data)}")
        record = f"{name}-1.0.dist-info/RECORD"
        record_lines.append(f"{record},,")
        archive.writestr(record, "\n".join(record_lines) + "\n")
    return buffer.getvalue()


def as_pinned(name: str, data: bytes) -> gpu_support.Wheel:
    return gpu_support.Wheel(
        name,
        "1.0",
        f"https://files.example/{name}-1.0-py3-none-win_amd64.whl",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int) -> None:
        super().__init__(data)
        self.status = status


class FakeServer:
    """Serves wheels by URL; honours ``Range`` unless told not to; can drop the connection."""

    def __init__(self, wheels: dict[str, bytes], *, ranges: bool = True) -> None:
        self.wheels = wheels
        self.ranges = ranges
        self.requests: list[tuple[str, str | None]] = []
        self.fail_after: int | None = None

    def __call__(self, request, timeout=None):
        url = request.full_url
        header = request.get_header("Range")
        self.requests.append((url, header))
        data = self.wheels[url]
        if header and self.ranges:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            response = FakeResponse(data[start:], 206)
        else:
            response = FakeResponse(data, 200)
        if self.fail_after is not None:
            limit, self.fail_after = self.fail_after, None
            original = response.read

            def read(size=-1):
                if response.tell() >= limit:
                    raise ConnectionResetError("connection dropped")
                return original(min(size, limit - response.tell()))

            response.read = read
        return response


CUBLAS = {
    "nvidia/cublas/bin/cublas64_12.dll": b"cublas" * 1000,
    "nvidia/cublas/bin/cublasLt64_12.dll": b"lt" * 3000,
    "nvidia/cublas/bin/nvblas64_12.dll": b"not needed",
    "nvidia/cublas/include/cublas.h": b"header",
    "nvidia/cublas/__init__.py": b"",
}
CUDNN = {"nvidia/cudnn/bin/cudnn64_9.dll": b"cudnn" * 2000, "nvidia/cudnn/bin/cudnn_ops64_9.dll": b"ops" * 500}


@pytest.fixture
def wheels():
    data = {"cublas": make_wheel("nvidia_cublas", CUBLAS), "cudnn": make_wheel("nvidia_cudnn", CUDNN)}
    pinned = (as_pinned("cublas", data["cublas"]), as_pinned("cudnn", data["cudnn"]))
    server = FakeServer({w.url: data[w.name] for w in pinned})
    return pinned, server


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    """Several chunks per file, so cancel and progress happen mid-file."""
    monkeypatch.setattr(gpu_support, "CHUNK_SIZE", 1024)


def install(target, wheels, server, **kwargs):
    return gpu_support.install(target=target, wheels=wheels, opener=server, **kwargs)


class TestPins:
    def test_the_real_pins_point_at_pypi_with_a_hash(self):
        for wheel in gpu_support.CUDA_WHEELS:
            assert wheel.url.startswith("https://files.pythonhosted.org/packages/")
            assert wheel.filename.endswith("-win_amd64.whl")
            assert len(wheel.sha256) == 64
            assert int(wheel.sha256, 16) >= 0
            assert wheel.size > 10_000_000

    def test_cublas_and_cudnn_are_both_pinned(self):
        names = {wheel.name for wheel in gpu_support.CUDA_WHEELS}
        assert {"nvidia-cublas-cu12", "nvidia-cudnn-cu12"} <= names

    def test_the_bundle_id_names_every_version(self):
        assert "nvidia-cublas-cu12==12.9.2.10" in gpu_support.bundle_id()

    def test_the_target_is_the_local_data_folder(self):
        assert app_paths.cuda_dir() == app_paths.local_data_dir() / "cuda"


class TestWantedMembers:
    def test_only_the_dlls_from_bin(self):
        assert sorted(gpu_support.wanted_members(CUBLAS)) == [
            "nvidia/cublas/bin/cublas64_12.dll",
            "nvidia/cublas/bin/cublasLt64_12.dll",
        ]

    def test_nothing_outside_nvidia(self):
        assert gpu_support.wanted_members(["other/x/bin/a.dll", "nvidia/bin/a.dll", "nvidia/a/bin/b/c.dll"]) == []


class TestInstall:
    def test_installs_the_dlls_with_a_manifest(self, tmp_path, wheels):
        pinned, server = wheels
        target = tmp_path / "cuda"
        assert install(target, pinned, server) == target
        names = sorted(p.name for p in target.iterdir())
        assert names == sorted(
            ["cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "manifest.json"]
        )
        assert (target / "cudnn64_9.dll").read_bytes() == CUDNN["nvidia/cudnn/bin/cudnn64_9.dll"]
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["bundle"] == gpu_support.bundle_id(pinned)
        assert gpu_support.installed_dir(target, pinned) == target

    def test_cleans_up_downloads_and_staging(self, tmp_path, wheels):
        pinned, server = wheels
        install(tmp_path / "cuda", pinned, server)
        # The lock file stays on purpose: deleting it would race a second process that just opened it.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cuda", "cuda.lock"]

    def test_reports_progress_up_to_the_total(self, tmp_path, wheels):
        pinned, server = wheels
        seen = []
        install(tmp_path / "cuda", pinned, server, progress=lambda *args: seen.append(args))
        downloads = [s for s in seen if s[0] == "download"]
        extracts = [s for s in seen if s[0] == "extract"]
        assert downloads[-1][1] == downloads[-1][2] == sum(w.size for w in pinned)
        assert extracts[-1][1] == extracts[-1][2]
        assert all(a[1] <= b[1] for a, b in zip(downloads, downloads[1:], strict=False))
        assert len(downloads) > 2  # per chunk, not just at the end

    def test_a_complete_install_is_not_repeated(self, tmp_path, wheels):
        pinned, server = wheels
        install(tmp_path / "cuda", pinned, server)
        server.requests.clear()
        install(tmp_path / "cuda", pinned, server)
        assert server.requests == []

    def test_a_new_pin_replaces_the_old_folder(self, tmp_path, wheels):
        pinned, server = wheels
        target = tmp_path / "cuda"
        install(target, pinned[:1], server)
        assert gpu_support.installed_dir(target, pinned) is None
        install(target, pinned, server)
        assert gpu_support.installed_dir(target, pinned) == target
        assert not (tmp_path / "cuda.old").exists()

    def test_a_downloaded_wheel_is_reused(self, tmp_path, wheels):
        pinned, server = wheels
        downloads = tmp_path / "cuda.download"
        downloads.mkdir()
        (downloads / pinned[0].filename).write_bytes(server.wheels[pinned[0].url])
        install(tmp_path / "cuda", pinned, server)
        assert [url for url, _ in server.requests] == [pinned[1].url]


class TestVerification:
    def test_a_wrong_download_is_rejected_and_nothing_installed(self, tmp_path, wheels):
        pinned, server = wheels
        server.wheels[pinned[1].url] = server.wheels[pinned[1].url][:-1] + b"X"
        with pytest.raises(gpu_support.GpuSupportError, match="SHA-256"):
            install(tmp_path / "cuda", pinned, server)
        assert not (tmp_path / "cuda").exists()
        assert not (tmp_path / "cuda.download" / (pinned[1].filename + ".part")).exists()

    def test_a_dll_that_does_not_match_the_record_is_rejected(self, tmp_path):
        data = make_wheel("nvidia_cudnn", CUDNN, bad_record=True)
        pinned = (as_pinned("cudnn", data),)
        with pytest.raises(gpu_support.GpuSupportError, match="RECORD"):
            install(tmp_path / "cuda", pinned, FakeServer({pinned[0].url: data}))
        assert not (tmp_path / "cuda").exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cuda.download", "cuda.lock"]  # the wheel stays

    def test_a_wheel_without_record_is_rejected(self, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("nvidia/cudnn/bin/cudnn64_9.dll", b"x")
        pinned = (as_pinned("cudnn", buffer.getvalue()),)
        with pytest.raises(gpu_support.GpuSupportError, match="RECORD"):
            install(tmp_path / "cuda", pinned, FakeServer({pinned[0].url: buffer.getvalue()}))

    def test_a_file_that_is_no_zip_is_rejected(self, tmp_path):
        data = b"no zip at all" * 10
        pinned = (as_pinned("cudnn", data),)
        with pytest.raises(gpu_support.GpuSupportError):
            install(tmp_path / "cuda", pinned, FakeServer({pinned[0].url: data}))
        assert not (tmp_path / "cuda").exists()

    def test_network_errors_become_gpu_support_errors(self, tmp_path, wheels):
        pinned, _server = wheels

        def offline(request, timeout=None):
            raise OSError("no network")

        with pytest.raises(gpu_support.GpuSupportError, match="no network"):
            install(tmp_path / "cuda", pinned, offline)


class TestCancelAndResume:
    def test_cancel_stops_and_keeps_the_partial_download(self, tmp_path, wheels):
        pinned, server = wheels
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 3

        with pytest.raises(gpu_support.InstallCancelled):
            install(tmp_path / "cuda", pinned, server, cancel=cancel)
        part = tmp_path / "cuda.download" / (pinned[0].filename + ".part")
        assert 0 < part.stat().st_size < pinned[0].size
        assert gpu_support.installed_dir(tmp_path / "cuda", pinned) is None

    def test_a_resumed_download_asks_for_the_rest_only(self, tmp_path, wheels):
        pinned, server = wheels
        downloads = tmp_path / "cuda.download"
        downloads.mkdir()
        data = server.wheels[pinned[0].url]
        (downloads / (pinned[0].filename + ".part")).write_bytes(data[:1500])
        install(tmp_path / "cuda", pinned, server)
        assert server.requests[0] == (pinned[0].url, "bytes=1500-")
        assert gpu_support.installed_dir(tmp_path / "cuda", pinned) is not None

    def test_a_server_without_ranges_starts_over(self, tmp_path, wheels):
        pinned, server = wheels
        server.ranges = False
        downloads = tmp_path / "cuda.download"
        downloads.mkdir()
        (downloads / (pinned[0].filename + ".part")).write_bytes(b"garbage" * 100)
        install(tmp_path / "cuda", pinned, server)
        assert gpu_support.installed_dir(tmp_path / "cuda", pinned) is not None

    def test_a_dropped_connection_can_be_resumed(self, tmp_path, wheels):
        pinned, server = wheels
        server.fail_after = 2048
        with pytest.raises(gpu_support.GpuSupportError, match="dropped"):
            install(tmp_path / "cuda", pinned, server)
        install(tmp_path / "cuda", pinned, server)
        assert server.requests[-2][1] == "bytes=2048-"
        assert gpu_support.installed_dir(tmp_path / "cuda", pinned) is not None

    def test_an_oversized_part_file_is_discarded(self, tmp_path, wheels):
        pinned, server = wheels
        downloads = tmp_path / "cuda.download"
        downloads.mkdir()
        (downloads / (pinned[0].filename + ".part")).write_bytes(b"x" * (pinned[0].size + 10))
        install(tmp_path / "cuda", pinned, server)
        assert server.requests[0] == (pinned[0].url, None)

    def test_a_complete_part_file_is_only_verified(self, tmp_path, wheels):
        pinned, server = wheels
        downloads = tmp_path / "cuda.download"
        downloads.mkdir()
        (downloads / (pinned[0].filename + ".part")).write_bytes(server.wheels[pinned[0].url])
        install(tmp_path / "cuda", pinned, server)
        assert [url for url, _ in server.requests] == [pinned[1].url]


class TestInstalledDir:
    @pytest.fixture
    def installed(self, tmp_path, wheels):
        pinned, server = wheels
        return install(tmp_path / "cuda", pinned, server), pinned

    def test_a_missing_folder(self, tmp_path):
        assert gpu_support.installed_dir(tmp_path / "nichts") is None

    def test_a_changed_file_size_invalidates(self, installed):
        target, pinned = installed
        (target / "cudnn64_9.dll").write_bytes(b"short")
        assert gpu_support.installed_dir(target, pinned) is None

    def test_a_missing_file_invalidates(self, installed):
        target, pinned = installed
        (target / "cublas64_12.dll").unlink()
        assert gpu_support.installed_dir(target, pinned) is None

    @pytest.mark.parametrize("content", ["kaputt", "[]", '{"bundle": "x"}'])
    def test_a_broken_manifest_invalidates(self, installed, content):
        target, pinned = installed
        (target / "manifest.json").write_text(content, encoding="utf-8")
        assert gpu_support.installed_dir(target, pinned) is None

    def test_a_manifest_without_files_invalidates(self, installed):
        target, pinned = installed
        manifest = {"bundle": gpu_support.bundle_id(pinned), "files": {}}
        (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert gpu_support.installed_dir(target, pinned) is None

    def test_remove_deletes_everything(self, installed, tmp_path):
        target, _pinned = installed
        (tmp_path / "cuda.download").mkdir()
        gpu_support.remove(target)
        assert list(tmp_path.iterdir()) == []

    def test_the_default_target_follows_the_data_folder(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HAMS_DATA_DIR", str(tmp_path))
        assert gpu_support.installed_dir() is None
        gpu_support.remove()
        assert app_paths.cuda_dir() == tmp_path / "cuda"


class TestPlatform:
    def test_supported_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(gpu_support.sys, "platform", "linux")
        assert not gpu_support.is_supported()
        assert not gpu_support.nvidia_gpu_present()

    @pytest.mark.skipif(gpu_support.sys.platform != "win32", reason="Windows only")
    def test_the_driver_check_asks_for_nvcuda(self, monkeypatch):
        import ctypes

        loaded = []

        def fake(name):
            loaded.append(name)
            raise OSError("missing")

        monkeypatch.setattr(ctypes, "WinDLL", fake)
        assert not gpu_support.nvidia_gpu_present()
        assert loaded == ["nvcuda.dll"]
        monkeypatch.setattr(ctypes, "WinDLL", lambda name: object())
        assert gpu_support.nvidia_gpu_present()


class TestCli:
    @pytest.fixture
    def windows_with_gpu(self, monkeypatch):
        monkeypatch.setattr(gpu_support, "is_supported", lambda: True)
        monkeypatch.setattr(gpu_support, "nvidia_gpu_present", lambda: True)

    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(gpu_support, "is_supported", lambda: False)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 2
        assert ".[cuda]" in out.getvalue()

    def test_no_nvidia_driver(self, monkeypatch):
        monkeypatch.setattr(gpu_support, "is_supported", lambda: True)
        monkeypatch.setattr(gpu_support, "nvidia_gpu_present", lambda: False)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 2
        assert "CPU" in out.getvalue()

    def test_success_prints_progress_and_the_folder(self, monkeypatch, windows_with_gpu, tmp_path):
        def fake_install(progress):
            for done in (0, 50, 50, 100):
                progress("download", done, 100)
            progress("extract", 0, 0)
            return tmp_path

        monkeypatch.setattr(gpu_support, "install", fake_install)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 0
        text = out.getvalue()
        assert "download:  50 %" in text
        assert text.count("download:  50 %") == 1
        assert "extract: 100 %" in text
        assert str(tmp_path) in text

    def test_failure_is_reported(self, monkeypatch, windows_with_gpu):
        def failing(progress):
            raise gpu_support.GpuSupportError("hash mismatch")

        monkeypatch.setattr(gpu_support, "install", failing)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 1
        assert "hash mismatch" in out.getvalue()

    def test_defaults_to_stdout(self, monkeypatch, capsys):
        monkeypatch.setattr(gpu_support, "is_supported", lambda: False)
        gpu_support.install_cli()
        assert "Windows" in capsys.readouterr().out


# --------------------------------------------------------- local failures


class TestLocalFailures:
    """A full disk or a missing write right is a GpuSupportError too, never a bare OSError."""

    def test_a_target_that_cannot_be_created(self, tmp_path, wheels):
        pinned, server = wheels
        blocker = tmp_path / "file"
        blocker.write_text("not a folder", encoding="utf-8")
        with pytest.raises(gpu_support.GpuSupportError) as caught:
            install(blocker / "cuda", pinned, server)
        assert isinstance(caught.value.__cause__, OSError)

    def test_a_full_disk_while_unpacking(self, tmp_path, wheels, monkeypatch):
        pinned, server = wheels

        def full(*_args):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(gpu_support, "_extract", full)
        with pytest.raises(gpu_support.GpuSupportError, match="No space") as caught:
            install(tmp_path / "cuda", pinned, server)
        assert caught.value.__cause__.errno == errno.ENOSPC
        assert not (tmp_path / "cuda").exists()
        assert not list(tmp_path.glob("cuda.tmp-*")), "the staging folder is cleaned up"

    def test_a_refused_rename(self, tmp_path, wheels, monkeypatch):
        pinned, server = wheels

        def refused(*_args):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(gpu_support.os, "replace", refused)
        with pytest.raises(gpu_support.GpuSupportError) as caught:
            install(tmp_path / "cuda", pinned, server)
        assert isinstance(caught.value.__cause__, PermissionError)

    def test_the_lock_is_released_after_a_failure(self, tmp_path, wheels, monkeypatch):
        pinned, server = wheels
        monkeypatch.setattr(gpu_support, "_extract", lambda *_a: (_ for _ in ()).throw(OSError("kaputt")))
        with pytest.raises(gpu_support.GpuSupportError):
            install(tmp_path / "cuda", pinned, server)
        monkeypatch.undo()
        monkeypatch.setattr(gpu_support, "CHUNK_SIZE", 1024)
        assert install(tmp_path / "cuda", pinned, server) == tmp_path / "cuda"


class TestInstallLock:
    def test_a_second_install_is_refused_while_one_runs(self, tmp_path, wheels):
        pinned, server = wheels
        target = tmp_path / "cuda"
        with gpu_support._install_lock(gpu_support.lock_path(target)), pytest.raises(gpu_support.InstallBusy):
            install(target, pinned, server)
        assert server.requests == [], "nothing was downloaded into the shared .part files"
        assert install(target, pinned, server) == target

    def test_busy_is_a_gpu_support_error(self):
        assert issubclass(gpu_support.InstallBusy, gpu_support.GpuSupportError)

    def test_another_process_holding_the_lock(self, tmp_path, wheels):
        """The real case: the CLI downloads while the wizard starts its own install."""
        pinned, server = wheels
        target = tmp_path / "cuda"
        root = Path(__file__).resolve().parent.parent
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; from hams import gpu_support as g\n"
                "with g._install_lock(Path(sys.argv[1])):\n"
                "    print('locked', flush=True); sys.stdin.readline()",
                str(gpu_support.lock_path(target)),
            ],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            with pytest.raises(gpu_support.InstallBusy):
                install(target, pinned, server)
        finally:
            holder.communicate("go\n", timeout=10)
        # The lock dies with the holder: no stale lock file blocks the next try.
        assert install(target, pinned, server) == target

    def test_a_leftover_lock_file_does_not_block(self, tmp_path, wheels):
        pinned, server = wheels
        target = tmp_path / "cuda"
        gpu_support.lock_path(target).write_bytes(b"from a crashed run")
        assert install(target, pinned, server) == target

    def test_remove_also_removes_the_lock_file(self, tmp_path, wheels):
        pinned, server = wheels
        target = tmp_path / "cuda"
        install(target, pinned, server)
        gpu_support.remove(target)
        assert list(tmp_path.iterdir()) == []


class TestCliFailures:
    @pytest.fixture(autouse=True)
    def windows_with_gpu(self, monkeypatch):
        monkeypatch.setattr(gpu_support, "is_supported", lambda: True)
        monkeypatch.setattr(gpu_support, "nvidia_gpu_present", lambda: True)

    def test_an_unexpected_error_is_a_sentence_not_a_traceback(self, monkeypatch):
        def failing(progress):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(gpu_support, "install", failing)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 1
        assert "No space left" in out.getvalue()
        assert "Traceback" not in out.getvalue()

    def test_a_running_install_is_named(self, monkeypatch):
        def busy(progress):
            raise gpu_support.InstallBusy("GPU support is already being installed by another Hams process")

        monkeypatch.setattr(gpu_support, "install", busy)
        out = io.StringIO()
        assert gpu_support.install_cli(out) == 1
        assert "already being installed" in out.getvalue()

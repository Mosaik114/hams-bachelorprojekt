"""The model cache without links: error 448 (RedirectionGuard) and its kin.

huggingface_hub links ``snapshots/<rev>/model.bin`` to ``blobs/<hash>`` where Windows lets it (developer mode).
A Hams started by a setup with RedirectionGuard could not follow those links: every access failed with 448.

Creating a file link needs developer mode or administrator rights, which the test machine may not have. The link
handling therefore runs with a stand-in (`_link_kind` and `os.readlink` replaced); a real link test runs where
Windows allows one, and the real error 448 is produced with a junction, which any user may create.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hams import models

TINY = models.find("tiny")
ROOT = Path(__file__).resolve().parent.parent
REPO = "models--Systran--faster-whisper-tiny"


class Untrusted(OSError):
    """An OSError with a Windows error number, on any platform."""

    def __init__(self, winerror=448):
        super().__init__(22, "Der Pfad kann nicht durchlaufen werden", "C:\\x\\model.bin")
        self.winerror = winerror


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """A cache with Tiny: a blob and, in the snapshot, a stand-in for the link to it."""
    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    blob = tmp_path / REPO / "blobs" / "abc123"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"real model")
    snapshot = tmp_path / REPO / "snapshots" / "rev1"
    snapshot.mkdir(parents=True)
    link = snapshot / models.MODEL_FILE
    link.write_bytes(b"LINK")  # stands in for the link itself
    plain = snapshot / "config.json"
    plain.write_bytes(b"{}")
    links = {os.path.normcase(str(link)): os.path.join("..", "..", "blobs", "abc123")}

    def link_kind(path):
        return models._IO_REPARSE_TAG_SYMLINK if os.path.normcase(str(path)) in links else 0

    def readlink(path):
        return links[os.path.normcase(str(path))]

    monkeypatch.setattr(models, "_link_kind", link_kind)
    monkeypatch.setattr(models.os, "readlink", readlink)
    return {"blob": blob, "link": link, "plain": plain, "links": links}


class TestReplaceLinks:
    def test_a_link_becomes_the_file_it_pointed_to(self, cache):
        assert models.replace_cache_links([TINY]) == 1
        assert cache["link"].read_bytes() == b"real model"
        assert cache["blob"].read_bytes() == b"real model"  # the blob stays
        assert cache["plain"].read_bytes() == b"{}"  # plain files are not touched

    def test_as_a_hard_link_where_possible(self, cache):
        models.replace_cache_links([TINY])
        assert os.stat(cache["link"]).st_ino == os.stat(cache["blob"]).st_ino  # no second copy on the disk

    def test_a_copy_where_hard_links_fail(self, cache, monkeypatch):
        def no_hard_links(*_a):
            raise OSError("no hard links here")

        monkeypatch.setattr(models.os, "link", no_hard_links)
        models.replace_cache_links([TINY])
        assert cache["link"].read_bytes() == b"real model"

    def test_a_link_without_its_blob_is_removed(self, cache):
        cache["blob"].unlink()
        assert models.replace_cache_links([TINY]) == 1
        assert not cache["link"].exists()  # the next download fetches it again

    def test_once_replaced_nothing_more_to_do(self, cache):
        models.replace_cache_links([TINY])
        cache["links"].clear()
        assert models.replace_cache_links([TINY]) == 0

    def test_other_programs_models_stay(self, cache, tmp_path):
        other = tmp_path / "models--someone--else" / "snapshots" / "r" / "weights.bin"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"LINK")
        cache["links"][os.path.normcase(str(other))] = "x"
        models.replace_cache_links()
        assert other.read_bytes() == b"LINK"

    def test_no_cache_is_no_problem(self, monkeypatch, tmp_path):
        monkeypatch.setattr(models, "cache_root", lambda: tmp_path / "missing")
        assert models.replace_cache_links() == 0


class TestTarget:
    def test_relative_and_long_paths(self, monkeypatch, tmp_path):
        link = str(tmp_path / "snapshots" / "rev" / "model.bin")
        monkeypatch.setattr(
            models.os, "readlink", lambda _p: "..\\..\\blobs\\h" if os.name == "nt" else "../../blobs/h"
        )
        assert models._link_target(link) == os.path.normpath(str(tmp_path / "blobs" / "h"))
        monkeypatch.setattr(models.os, "readlink", lambda _p: "\\\\?\\C:\\cache\\blobs\\h")
        assert models._link_target(link).endswith(os.path.normpath("C:\\cache\\blobs\\h"))


class TestPrepare:
    @pytest.mark.skipif(sys.platform != "win32", reason="the links are a Windows matter")
    def test_new_downloads_are_plain_files(self):
        from huggingface_hub import file_download

        models.prepare_cache()
        assert file_download.are_symlinks_supported() is False

    @pytest.mark.skipif(sys.platform != "win32", reason="the links are a Windows matter")
    def test_once_per_process_unless_forced(self, monkeypatch):
        calls = []
        monkeypatch.setattr(models, "replace_cache_links", lambda: calls.append(1) or 0)
        models.prepare_cache()
        models.prepare_cache()
        models.prepare_cache(force=True)
        assert len(calls) == 2

    @pytest.mark.skipif(sys.platform == "win32", reason="elsewhere only")
    def test_elsewhere_nothing_changes(self, monkeypatch):
        from huggingface_hub import file_download

        before = file_download.are_symlinks_supported
        assert models.prepare_cache() == 0
        assert file_download.are_symlinks_supported is before


class TestErrors:
    def test_448_has_its_own_sentence_and_number(self):
        text = models.describe_download_error(Untrusted())
        assert text.startswith("Windows lässt Hams nicht in den Ordner für Sprachmodelle.")
        assert models.split_code(text)[1] == "448"

    def test_also_behind_the_hubs_wrapper(self):
        try:
            try:
                raise Untrusted()
            except OSError as inner:
                raise RuntimeError("snapshot failed") from inner
        except RuntimeError as outer:
            assert models.error_kind(outer) == "blocked_folder"

    @pytest.mark.parametrize("number", [362, 395, 1920])
    def test_onedrive_and_unreachable_files_count_too(self, number):
        assert models.error_kind(Untrusted(number)) == "blocked_folder"

    def test_not_for_a_full_disk_or_rights(self):
        assert models.error_kind(PermissionError(13, "denied")) == "permission"
        assert models.untrusted_path(OSError(28, "no space")) is None

    def test_it_is_no_leftover_and_no_hiccup(self):
        assert not models.leftover_can_cause(Untrusted())
        assert not models.passes_by_itself(Untrusted())


class TestDownloadRepairsLinks:
    def _download(self, monkeypatch, behaviour):
        import huggingface_hub

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        monkeypatch.setattr(models, "prepare_cache", lambda **_k: 0)

        def snapshot_download(_repo, **_kwargs):
            behaviour()
            folder = Path(os.environ.get("TMP", "/tmp")) / "hams-448-snapshot"
            folder.mkdir(exist_ok=True)
            (folder / models.MODEL_FILE).write_bytes(b"m")
            return str(folder)

        monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
        downloader, finished = models.ModelDownloader(), []
        downloader.start(TINY, lambda *_a: None, lambda m, e: finished.append((m, e)))
        downloader._thread.join(timeout=10)
        return finished

    def test_links_replaced_then_once_more(self, monkeypatch):
        calls = []
        monkeypatch.setattr(models, "replace_cache_links", lambda infos=None: 1)

        def behaviour():
            calls.append(1)
            if len(calls) == 1:
                raise Untrusted()

        assert self._download(monkeypatch, behaviour) == [("tiny", "")]
        assert len(calls) == 2

    def test_nothing_to_replace_says_so(self, monkeypatch):
        monkeypatch.setattr(models, "replace_cache_links", lambda infos=None: 0)

        def behaviour():
            raise Untrusted()

        finished = self._download(monkeypatch, behaviour)
        assert models.split_code(finished[0][1])[1] == "448"


class TestScan:
    def test_a_blocked_cache_is_read_model_by_model(self, monkeypatch, tmp_path):
        import huggingface_hub
        from huggingface_hub.utils import _cache_manager

        monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
        monkeypatch.setattr(models, "prepare_cache", lambda **_k: 0)
        (tmp_path / REPO).mkdir()

        def blocked(*_a, **_k):
            raise Untrusted()

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", blocked)
        seen = []
        monkeypatch.setattr(_cache_manager, "_scan_cached_repo", lambda path: seen.append(path) or "repo")
        assert models.scan_cache().repos == ["repo"]
        assert seen == [tmp_path / REPO]

    def test_other_errors_stay_errors(self, monkeypatch):
        import huggingface_hub

        monkeypatch.setattr(models, "prepare_cache", lambda **_k: 0)
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: (_ for _ in ()).throw(OSError(5, "x")))
        with pytest.raises(OSError):
            models.scan_cache()


class TestDelete:
    def test_a_blocked_cache_still_lets_the_model_go(self, monkeypatch, tmp_path):
        import huggingface_hub

        monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
        folder = tmp_path / REPO / "snapshots" / "r"
        folder.mkdir(parents=True)
        (folder / models.MODEL_FILE).write_bytes(b"x" * 10)
        locks = tmp_path / ".locks" / REPO
        locks.mkdir(parents=True)
        entry = models.CachedModel("tiny", "Tiny", 10, True, ("r",))
        monkeypatch.setattr(models, "cached_whisper_models", lambda: (entry,))

        def blocked():
            raise Untrusted()

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", blocked)
        ok, freed, _text = models.delete_whisper_model("tiny")
        assert ok and freed == 10
        assert not (tmp_path / REPO).exists()
        assert not locks.exists()


def _can_symlink(folder: Path) -> bool:
    try:
        os.symlink(folder / "missing", folder / "probe")
    except (OSError, NotImplementedError):
        return False
    os.unlink(folder / "probe")
    return True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows links")
class TestForReal:
    def test_a_real_file_link_is_replaced(self, monkeypatch, tmp_path):
        if not _can_symlink(tmp_path):
            pytest.skip("this Windows lets only administrators or developer mode create links")
        monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
        blob = tmp_path / REPO / "blobs" / "h"
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"real")
        link = tmp_path / REPO / "snapshots" / "r" / models.MODEL_FILE
        link.parent.mkdir(parents=True)
        os.symlink(os.path.join("..", "..", "blobs", "h"), link)
        assert models.replace_cache_links([TINY]) == 1
        assert not os.path.islink(link)
        assert link.read_bytes() == b"real"

    def test_a_revision_junction_is_removed_not_followed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
        real = tmp_path / "elsewhere"
        real.mkdir()
        (real / "keep.txt").write_text("keep")
        snapshots = tmp_path / REPO / "snapshots"
        snapshots.mkdir(parents=True)
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(snapshots / "rev"), str(real)], check=True, capture_output=True
        )
        assert models.replace_cache_links([TINY]) == 1
        assert not os.path.lexists(snapshots / "rev")
        assert (real / "keep.txt").read_text() == "keep"  # what it pointed to is untouched

    def test_the_real_448_gets_the_sentence(self, tmp_path):
        """A junction and RedirectionGuard, in a child process: the error the user saw, and Hams' answer to it."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "model.bin").write_bytes(b"m")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(tmp_path / "link"), str(real)], check=True, capture_output=True
        )
        code = textwrap.dedent(
            f"""
            import ctypes, sys
            from ctypes import wintypes
            from pathlib import Path
            sys.path.insert(0, {str(ROOT)!r})
            from hams import i18n, models
            i18n.set_language("de")
            k = ctypes.WinDLL("kernel32")
            k.SetProcessMitigationPolicy.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
            flags = wintypes.DWORD(1)
            if not k.SetProcessMitigationPolicy(16, ctypes.byref(flags), ctypes.sizeof(flags)):
                print("NO-GUARD"); raise SystemExit
            try:
                Path({str(tmp_path / "link" / "model.bin")!r}).is_file()
                print("OPENED")
            except OSError as exc:
                print(exc.winerror, models.describe_download_error(exc))
            """
        )
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60
        )
        assert out.returncode == 0, out.stderr
        if "NO-GUARD" in out.stdout:
            pytest.skip("this Windows has no RedirectionGuard")
        assert out.stdout.startswith("448 Windows lässt Hams nicht")
        assert "(448)" in out.stdout

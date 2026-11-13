"""Modelle wieder loswerden: Bestand, Loeschregeln, Loeschen selbst.

Der Cache wird durchweg nachgebildet. Ein Test, der den echten
Hugging-Face-Cache anfasst, waere kein Test mehr, sondern ein Eingriff in die
Installation des Nutzers — und beim zweiten Lauf haette er nichts mehr zu tun.
"""

from types import SimpleNamespace

import kit as uk
import pytest

from hams import models as whisper_models
from hams.settings_window import SettingsWindow
from hams.ui_kit.icons import _ICON_PAINTERS

# ------------------------------------------------------------------ Attrappen


class _File:
    def __init__(self, name):
        self.file_name = name


class _Revision:
    def __init__(self, commit_hash, file_names):
        self.commit_hash = commit_hash
        self.files = [_File(n) for n in file_names]


class _Repo:
    def __init__(self, repo_id, size_text, revision_hashes):
        self.repo_id = repo_id
        self.size_on_disk = size_text
        self.revisions = revision_hashes


class _Cache:
    """Ersatz fuer `scan_cache_dir()` — merkt sich, was geloescht werden sollte."""

    def __init__(self, repos, log=None, error=None):
        self.repos = repos
        self._log = log if log is not None else []
        self._error = error

    def delete_revisions(self, *revision_hashes):
        cache = self

        class _Strategy:
            expected_freed_size = 4_000_000

            def execute(self):
                if cache._error is not None:
                    raise cache._error
                cache._log.append(tuple(revision_hashes))

        return _Strategy()


TURBO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
TINY = "Systran/faster-whisper-tiny"
BASE = "Systran/faster-whisper-base"
VOLL = ["config.json", "model.bin", "tokenizer.json"]
REST = ["config.json", "tokenizer.json"]


@pytest.fixture
def cache(monkeypatch):
    """Setzt einen nachgebildeten Cache ein und liefert das Loeschprotokoll."""
    log = []

    def _setze(repos, error=None):
        faked = _Cache(repos, log, error)
        monkeypatch.setattr("huggingface_hub.scan_cache_dir", lambda: faked)
        return faked

    _setze.log = log
    return _setze


# --------------------------------------------------------------- Bestand


class TestCachedModels:
    def test_a_complete_model_is_reported_with_its_size(self, cache):
        cache([_Repo(TINY, 78_203_619, [_Revision("aaa111", VOLL)])])
        (entry,) = whisper_models.cached_whisper_models()
        assert entry.model_id == "tiny"
        assert entry.label == "Tiny"
        assert entry.size_bytes == 78_203_619
        assert entry.complete is True
        assert entry.revisions == ("aaa111",)

    def test_a_cancelled_download_counts_as_incomplete(self, cache):
        """Ohne `model.bin` laesst sich nichts laden — der Rest belegt nur Platz."""
        cache([_Repo(BASE, 12_000_000, [_Revision("bbb222", REST)])])
        (entry,) = whisper_models.cached_whisper_models()
        assert entry.model_id == "base"
        assert entry.complete is False
        assert "unvollständig" in entry.meta

    def test_an_incomplete_model_is_not_reported_as_installed(self, cache):
        """Der Unterschied zu `installed_whisper_models()` — beide gleichzeitig."""
        cache(
            [_Repo(BASE, 12_000_000, [_Revision("bbb222", REST)]), _Repo(TINY, 78_203_619, [_Revision("aaa111", VOLL)])]
        )
        cached = {e.model_id for e in whisper_models.cached_whisper_models()}
        assert cached == {"base", "tiny"}
        assert whisper_models.installed_whisper_models() == {"tiny"}

    def test_foreign_repositories_are_ignored(self, cache):
        cache(
            [
                _Repo("meta-llama/Llama-3", 9_000_000_000, [_Revision("ccc333", VOLL)]),
                _Repo(TINY, 78_203_619, [_Revision("aaa111", VOLL)]),
            ]
        )
        assert [e.model_id for e in whisper_models.cached_whisper_models()] == ["tiny"]

    def test_the_order_follows_the_catalogue(self, cache):
        cache(
            [
                _Repo(TINY, 1, [_Revision("a", VOLL)]),
                _Repo(TURBO, 2, [_Revision("b", VOLL)]),
                _Repo(BASE, 3, [_Revision("c", VOLL)]),
            ]
        )
        ids = [e.model_id for e in whisper_models.cached_whisper_models()]
        katalog = [i.model_id for i in whisper_models.CATALOG]
        assert ids == [i for i in katalog if i in set(ids)]

    def test_a_repository_without_revisions_is_skipped(self, cache):
        cache([_Repo(TINY, 0, [])])
        assert whisper_models.cached_whisper_models() == ()

    def test_an_unreadable_cache_reports_nothing(self, monkeypatch):
        """Dieselbe vorsichtige Haltung wie bei der Installationserkennung."""

        def _broken():
            raise OSError("Cache nicht lesbar")

        monkeypatch.setattr("huggingface_hub.scan_cache_dir", _broken)
        assert whisper_models.cached_whisper_models() == ()


# ---------------------------------------------------------------- Loeschen


class TestDelete:
    def test_it_deletes_exactly_the_revisions_of_that_model(self, cache):
        cache(
            [
                _Repo(TINY, 78_203_619, [_Revision("aaa111", VOLL)]),
                _Repo(TURBO, 1_621_665_983, [_Revision("zzz999", VOLL)]),
            ]
        )
        ok, free, message = whisper_models.delete_whisper_model("tiny")
        assert ok is True
        assert cache.log == [("aaa111",)]
        assert free == 4_000_000
        assert "Tiny" in message and "gelöscht" in message

    def test_all_revisions_of_the_repository_go(self, cache):
        cache([_Repo(TINY, 100, [_Revision("a1", VOLL), _Revision("b2", VOLL)])])
        assert whisper_models.delete_whisper_model("tiny")[0] is True
        assert cache.log == [("a1", "b2")]

    def test_an_incomplete_leftover_can_be_deleted(self, cache):
        cache([_Repo(BASE, 12_000_000, [_Revision("bbb222", REST)])])
        ok, _free, _message = whisper_models.delete_whisper_model("base")
        assert ok is True
        assert cache.log == [("bbb222",)]

    def test_without_freed_space_the_message_carries_no_number(self, cache, monkeypatch):
        faked = cache([_Repo(BASE, 0, [_Revision("bbb222", REST)])])

        class _Nothing:
            expected_freed_size = 0

            def execute(self):
                pass

        monkeypatch.setattr(faked, "delete_revisions", lambda *r: _Nothing())
        ok, free, message = whisper_models.delete_whisper_model("base")
        assert ok is True and free == 0
        assert message == "Base gelöscht"

    def test_a_model_that_is_not_there_is_reported_not_deleted(self, cache):
        cache([_Repo(TINY, 100, [_Revision("a1", VOLL)])])
        ok, free, message = whisper_models.delete_whisper_model("turbo")
        assert ok is False and free == 0
        assert "liegt nicht" in message
        assert cache.log == []

    def test_an_unknown_id_is_handled(self, cache):
        cache([])
        ok, _free, message = whisper_models.delete_whisper_model("phantom")
        assert ok is False and "phantom" in message

    def test_a_model_in_use_gets_its_own_message(self, cache):
        """Windows gibt eine geladene Modelldatei nicht her."""
        cache([_Repo(TURBO, 1_621_665_983, [_Revision("zzz999", VOLL)])], error=PermissionError(32, "in Benutzung"))
        ok, free, message = whisper_models.delete_whisper_model("turbo")
        assert ok is False and free == 0
        assert message == "Turbo wird gerade benutzt"

    def test_any_other_failure_does_not_escape(self, cache):
        cache([_Repo(TINY, 100, [_Revision("a1", VOLL)])], error=RuntimeError("irgendwas"))
        ok, _free, message = whisper_models.delete_whisper_model("tiny")
        assert ok is False
        assert "konnte nicht gelöscht werden" in message

    def test_it_falls_back_to_the_measured_size(self, cache, monkeypatch):
        """Meldet die Strategie keine Groesse, zaehlt der gemessene Bestand."""
        faked = cache([_Repo(TINY, 78_203_619, [_Revision("a1", VOLL)])])

        class _WithoutSize:
            expected_freed_size = 0

            def execute(self):
                pass

        monkeypatch.setattr(faked, "delete_revisions", lambda *r: _WithoutSize())
        ok, free, _message = whisper_models.delete_whisper_model("tiny")
        assert ok is True and free == 78_203_619


# ------------------------------------------------------------- Loeschregeln


class _App:
    def __init__(self, active=None):
        self.models = SimpleNamespace(active_model_size=active)


def _window(active=None):
    window = SettingsWindow.__new__(SettingsWindow)
    window.app = _App(active)
    return window


class TestDeletableRules:
    @pytest.fixture(autouse=True)
    def konfiguriert(self, monkeypatch):
        monkeypatch.setattr("hams.config.CFG.model_size", "turbo")

    def test_the_loaded_model_stays(self, cache):
        cache([_Repo(TURBO, 1_621_665_983, [_Revision("z", VOLL)]), _Repo(TINY, 78_203_619, [_Revision("a", VOLL)])])
        allowed = {e.model_id for e in _window(active="turbo").deletable_models()}
        assert allowed == {"tiny"}

    def test_the_configured_model_stays_while_it_is_complete(self, cache):
        """Sonst laedt faster-whisper es beim naechsten Start wortlos nach."""
        cache([_Repo(TURBO, 1_621_665_983, [_Revision("z", VOLL)]), _Repo(TINY, 78_203_619, [_Revision("a", VOLL)])])
        allowed = {e.model_id for e in _window(active=None).deletable_models()}
        assert allowed == {"tiny"}

    def test_an_incomplete_leftover_of_the_configured_model_may_go(self, cache):
        """Es ist ohnehin nicht ladbar — die Regel schuetzt hier nichts."""
        cache([_Repo(TURBO, 400_000, [_Revision("z", REST)])])
        allowed = {e.model_id for e in _window(active=None).deletable_models()}
        assert allowed == {"turbo"}

    def test_nothing_is_deletable_when_only_the_active_model_is_there(self, cache):
        cache([_Repo(TURBO, 1_621_665_983, [_Revision("z", VOLL)])])
        assert _window(active="turbo").deletable_models() == []

    def test_an_empty_cache_offers_nothing(self, cache):
        cache([])
        assert _window(active="turbo").deletable_models() == []


class TestStorageText:
    def test_it_counts_the_models_and_adds_up_their_size(self):
        cached = [
            whisper_models.CachedModel("turbo", "Turbo", 1_621_665_983, True, ()),
            whisper_models.CachedModel("tiny", "Tiny", 78_203_619, True, ()),
        ]
        assert SettingsWindow.storage_text(cached) == "2 Modelle · 1,7 GB"

    def test_one_model_is_singular(self):
        cached = [whisper_models.CachedModel("tiny", "Tiny", 78_203_619, True, ())]
        assert SettingsWindow.storage_text(cached) == "1 Modell · 78 MB"

    def test_an_empty_disk_says_so(self):
        assert SettingsWindow.storage_text([]) == "Noch nichts gespeichert"

    def test_leftovers_count_towards_the_total(self):
        cached = [whisper_models.CachedModel("base", "Base", 12_000_000, False, ())]
        assert "12 MB" in SettingsWindow.storage_text(cached)

    def test_the_line_stays_short_enough_for_the_row(self):
        """Neben dem Auswahlfeld bleiben rund 150 px — etwa 34 Zeichen."""
        cached = [
            whisper_models.CachedModel(i.model_id, i.label, 3_000_000_000, True, ()) for i in whisper_models.CATALOG
        ]
        assert len(SettingsWindow.storage_text(cached)) <= 34


class TestConfirmText:
    def test_a_complete_model_names_the_space_it_frees(self):
        entry = whisper_models.CachedModel("turbo", "Turbo", 1_621_665_983, True, ())
        assert SettingsWindow.confirm_text(entry) == "Turbo löschen? 1,6 GB frei"

    def test_a_leftover_asks_without_a_number(self):
        """Bei 0 B waere „gibt 0 B frei" eine Meldung ueber nichts."""
        entry = whisper_models.CachedModel("base", "Base", 0, False, ())
        assert SettingsWindow.confirm_text(entry) == "Rest von Base entfernen?"

    def test_both_forms_fit_the_row(self):
        for entry in (
            whisper_models.CachedModel("large-v3", "Large v3", 3_090_835_702, True, ()),
            whisper_models.CachedModel("large-v3", "Large v3", 0, False, ()),
        ):
            assert len(SettingsWindow.confirm_text(entry)) <= 34


# ------------------------------------------------------------------- Symbol


class TestTrashIcon:
    def test_the_icon_exists(self):
        assert "trash" in _ICON_PAINTERS

    def test_it_is_drawn_within_its_box(self):
        image = uk.render_icon("trash", 32, "#1C1C1E")
        assert image.size == (32, 32)
        alpha = image.getchannel("A")
        assert alpha.getbbox() is not None, "das Motiv darf nicht leer sein"
        left, top, right, bottom = alpha.getbbox()
        assert left >= 1 and top >= 1 and right <= 31 and bottom <= 31

    def test_it_stays_legible_at_sixteen_pixels(self):
        """Bei 16 px muss noch genug Deckung uebrig bleiben, um etwas zu erkennen."""
        alpha = uk.render_icon("trash", 16, "#1C1C1E").getchannel("A")
        deckend = sum(1 for value in alpha.getdata() if value > 128)
        assert deckend >= 25, f"nur {deckend} deckende Bildpunkte"

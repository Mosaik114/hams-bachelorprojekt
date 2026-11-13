"""Tests des Whisper-Modellkatalogs, der Cache-Erkennung und der Auswahl."""

from types import SimpleNamespace

import pytest
from setup_fakes import InlineThread

from hams import models, settings_models
from hams.config import CFG

#: The catalog ids in their order.
CATALOG_IDS = tuple(info.model_id for info in models.CATALOG)


class TestCatalog:
    def test_exactly_the_six_curated_models(self):
        assert CATALOG_IDS == ("turbo", "large-v3", "medium", "small", "base", "tiny")

    def test_no_english_only_or_distil_variants(self):
        for model_id in CATALOG_IDS:
            assert not model_id.endswith(".en")
            assert not model_id.startswith("distil")
        assert "large" not in CATALOG_IDS  # der Alias fehlt bewusst

    @pytest.mark.parametrize(
        "model_id,label",
        [
            ("turbo", "Turbo"),
            ("large-v3", "Large v3"),
            ("medium", "Medium"),
            ("small", "Small"),
            ("base", "Base"),
            ("tiny", "Tiny"),
        ],
    )
    def test_labels(self, model_id, label):
        assert models.find(model_id).label == label

    def test_every_entry_is_complete(self):
        for info in models.CATALOG:
            assert info.summary and info.size_label and info.repo_id
            assert info.size_bytes > 0
            assert "·" in info.meta

    def test_sizes_are_ordered_sensibly(self):
        assert models.find("tiny").size_bytes < models.find("base").size_bytes
        assert models.find("small").size_bytes < models.find("medium").size_bytes
        assert models.find("medium").size_bytes < models.find("large-v3").size_bytes

    def test_turbo_points_at_the_converted_repository(self):
        assert models.find("turbo").repo_id.endswith("faster-whisper-large-v3-turbo")

    def test_unknown_id_returns_none(self):
        assert models.find("gibtsnicht") is None


class TestCacheDetection:
    def _real_cache(self, monkeypatch, tmp_path, names):
        """Echter Cache-Ordner, so wie huggingface_hub ihn anlegt, im Temp-Ordner.

        Die anderen Faelle bauen die Antwort von scan_cache_dir nach. Nur hier
        laeuft die echte Funktion: aendert eine neue huggingface_hub-Version den
        Aufbau ihrer Antwort, faellt es hier auf und nicht erst beim Nutzer.
        """
        import inspect

        import huggingface_hub

        repo = models.find("tiny").repo_id
        snapshot = tmp_path / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        for name in names:
            (snapshot / name).write_bytes(b"x" * 64)
        (snapshot.parent.parent / "refs").mkdir()
        (snapshot.parent.parent / "refs" / "main").write_text("abc123")
        cache_module = inspect.getmodule(huggingface_hub.scan_cache_dir)
        monkeypatch.setattr(cache_module, "HF_HUB_CACHE", str(tmp_path))

    def test_real_cache_with_model_file_counts(self, monkeypatch, tmp_path):
        self._real_cache(monkeypatch, tmp_path, ["config.json", "model.bin"])
        assert models.installed_whisper_models() == {"tiny"}

    def test_real_cache_without_model_file_does_not_count(self, monkeypatch, tmp_path):
        self._real_cache(monkeypatch, tmp_path, ["config.json", "tokenizer.json"])
        assert models.installed_whisper_models() == set()

    def test_returns_only_catalog_ids(self):
        assert models.installed_whisper_models() <= set(CATALOG_IDS)

    def test_broken_cache_api_is_not_fatal(self, monkeypatch):
        """Ein unbekannter Zustand darf nicht als „installiert“ erfunden werden."""
        import huggingface_hub

        def boom():
            raise OSError("Cache kaputt")

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", boom)
        assert models.installed_whisper_models() == set()

    def test_empty_cache_reports_nothing(self, monkeypatch):
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: type("C", (), {"repos": ()})())
        assert models.installed_whisper_models() == set()

    def test_foreign_repositories_are_ignored(self, monkeypatch):
        import huggingface_hub

        repo = type("R", (), {"repo_id": "openai/whisper-large", "size_on_disk": 999, "revisions": [object()]})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: type("C", (), {"repos": (repo,)})())
        assert models.installed_whisper_models() == set()

    def test_empty_repo_folder_does_not_count(self, monkeypatch):
        import huggingface_hub

        repo = type("R", (), {"repo_id": models.find("tiny").repo_id, "size_on_disk": 0, "revisions": []})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: type("C", (), {"repos": (repo,)})())
        assert "tiny" not in models.installed_whisper_models()

    def _cache_with(self, monkeypatch, names):
        import huggingface_hub

        files = [type("F", (), {"file_name": name})() for name in names]
        revision = type("V", (), {"files": files})()
        repo = type("R", (), {"repo_id": models.find("tiny").repo_id, "size_on_disk": 4096, "revisions": [revision]})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: type("C", (), {"repos": (repo,)})())

    def test_a_cancelled_download_does_not_count_as_installed(self, monkeypatch):
        """Ein Abbruch hinterlaesst die kleinen Dateien — nur model.bin zaehlt."""
        self._cache_with(monkeypatch, ["config.json", "tokenizer.json"])
        assert "tiny" not in models.installed_whisper_models()

    def test_a_complete_download_counts(self, monkeypatch):
        self._cache_with(monkeypatch, ["config.json", "model.bin", "vocabulary.txt"])
        assert "tiny" in models.installed_whisper_models()

    def test_cache_root_is_a_path_or_none(self):
        root = models.cache_root()
        assert root is None or root.name

    def test_free_space_is_plausible(self):
        free = models.free_space_bytes()
        assert free is None or free > 0


class FakeDownloader:
    """`ModelDownloader` ohne Netz: merkt sich nur, was gestartet wurde."""

    def __init__(self):
        self.model_id, self.active, self.started = None, False, []

    def start(self, info, on_progress, on_finished):
        self.started.append(info.model_id)
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.active = False


@pytest.fixture
def settings(live_app, monkeypatch):
    """Oeffnet das echte Einstellungsfenster und liefert seine Gruppe "Transkription".

    `settings(installed, chosen, active="turbo", switch_ok=True)`: die installierten Modelle, das gewaehlte
    (`CFG.model_size`) und das geladene. Ein Wechsel wird nur aufgezeichnet (`live_app.switched`), ein Download
    nur gestartet (`live_app.models.downloader.started`).
    """
    monkeypatch.setattr(settings_models, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(models, "cached_whisper_models", lambda: ())
    live_app.models.downloader = FakeDownloader()
    live_app.switched = []

    def _open(installed, chosen, active="turbo", switch_ok=True):
        monkeypatch.setattr(models, "installed_whisper_models", lambda: set(installed))
        monkeypatch.setattr(CFG, "model_size", chosen)
        monkeypatch.setattr(live_app, "switch_model", lambda model_id: live_app.switched.append(model_id) or switch_ok)
        live_app.models.active_model_size = active
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win.transcription

    return _open


def description(section) -> str:
    return section.row_model.sub_text


class TestOptions:
    def test_two_groups(self, settings):
        options = settings({"turbo"}, "turbo").model_select.options
        assert {o.group for o in options} == {"Installiert", "Verfügbar"}

    def test_installed_model_has_no_download_marker(self, settings):
        options = settings({"turbo"}, "turbo").model_select.options
        turbo = next(o for o in options if o.value == "turbo")
        assert turbo.group == "Installiert" and turbo.trailing == ""

    def test_available_models_show_their_size(self, settings):
        options = settings({"turbo"}, "turbo").model_select.options
        large = next(o for o in options if o.value == "large-v3")
        assert large.group == "Verfügbar" and "3,1 GB" in large.trailing

    def test_meta_lines_are_present(self, settings):
        for option in settings({"turbo"}, "turbo").model_select.options:
            assert option.meta

    def test_custom_config_value_is_shown_not_reset(self, settings):
        """Ein eigener Wert darf nicht stillschweigend auf Turbo fallen."""
        tr = settings({"turbo"}, "large-v2")
        options = tr.model_select.options
        assert options[0].value == "large-v2"
        assert "Benutzerdefiniert" in options[0].label
        assert tr.model_select.value == "large-v2"

    def test_all_six_are_offered(self, settings):
        values = [o.value for o in settings({"turbo"}, "turbo").model_select.options]
        assert set(CATALOG_IDS) <= set(values)


class TestSelection:
    def test_installed_model_triggers_a_switch(self, settings, hams, live_app):
        """Gespeichert wird erst nach erfolgreichem Wechsel, nicht beim Klick."""
        tr = settings({"turbo", "small"}, "turbo")
        tr.select_model("small")
        assert live_app.switched == ["small"]
        assert hams.saved == [], "Config darf noch nicht auf das neue Modell zeigen"

    def test_uninstalled_model_is_not_persisted(self, settings, hams, live_app):
        """Sonst zöge der nächste Start wortlos mehrere Gigabyte nach."""
        tr = settings({"turbo"}, "turbo")
        tr.select_model("large-v3")
        assert hams.saved == []
        assert tr.model_select.value == "turbo", "Auswahl muss zurückspringen"
        assert live_app.models.downloader.started == ["large-v3"], "Auswahl startet den Download"
        assert live_app.switched == []

    def test_choosing_the_active_model_changes_nothing(self, settings, hams, live_app):
        tr = settings({"turbo"}, "turbo")
        tr.select_model("turbo")
        assert hams.saved == []
        assert live_app.switched == []

    def test_rejected_switch_restores_the_selection(self, settings):
        """Waehrend einer Aufnahme lehnt die Anwendung ab — die Zeile sagt es."""
        tr = settings({"turbo", "small"}, "turbo", switch_ok=False)
        tr.select_model("small")
        assert tr.model_select.value == "turbo"
        assert "Aufnahme" in description(tr)

    def test_switch_disables_the_select_while_loading(self, settings):
        tr = settings({"turbo", "small"}, "turbo")
        tr.select_model("small")
        assert tr.model_select.enabled is False
        assert "geladen" in description(tr)


class TestHint:
    def test_normal_state(self, settings):
        tr = settings({"turbo"}, "turbo", active="turbo")
        tr.update_model_hint()
        assert description(tr) == models.find("turbo").meta

    def test_chosen_differs_from_active(self, settings):
        """Aktiv und für den nächsten Start gewählt sind zwei Zustände."""
        tr = settings({"turbo", "small"}, "small", active="turbo")
        tr.update_model_hint()
        assert "nächsten Start" in description(tr)

    def test_not_installed_is_named(self, settings):
        tr = settings({"turbo"}, "medium", active="turbo")
        tr.update_model_hint()
        assert "geladen" in description(tr)


class TestModelSizeWarning:
    """`model.size` may name any faster-whisper model; one outside the catalog is loaded as given, with a warning."""

    def _load(self, tmp_path, monkeypatch, caplog, size):
        import logging

        from hams import config as config_mod

        path = tmp_path / "config.toml"
        path.write_text(f'[model]\nsize = "{size}"\n', encoding="utf-8")
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        with caplog.at_level(logging.WARNING, logger="hams"):
            cfg = config_mod._load_config()
        return cfg, [r.getMessage() for r in caplog.records if "model.size" in r.getMessage()]

    @pytest.mark.parametrize("model_id", [info.model_id for info in models.CATALOG])
    def test_curated_models_pass(self, model_id, tmp_path, monkeypatch, caplog):
        cfg, warnings = self._load(tmp_path, monkeypatch, caplog, model_id)
        assert cfg.model_size == model_id and warnings == []

    def test_custom_value_is_reported_but_kept(self, tmp_path, monkeypatch, caplog):
        cfg, warnings = self._load(tmp_path, monkeypatch, caplog, "large-v2")
        assert cfg.model_size == "large-v2"
        assert len(warnings) == 1

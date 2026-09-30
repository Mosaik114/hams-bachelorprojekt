"""Tests des Whisper-Modellkatalogs, der Cache-Erkennung und der Auswahl."""

import pytest

from wisper import models
from wisper.config_validation import validate_config
from wisper.main import SettingsWindow as SW
from wisper.main import Config


class TestCatalog:
    def test_exactly_the_six_curated_models(self):
        assert models.catalog_ids() == (
            "turbo", "large-v3", "medium", "small", "base", "tiny")

    def test_no_english_only_or_distil_variants(self):
        for model_id in models.catalog_ids():
            assert not model_id.endswith(".en")
            assert not model_id.startswith("distil")
        assert "large" not in models.catalog_ids()      # der Alias fehlt bewusst

    @pytest.mark.parametrize("model_id,label", [
        ("turbo", "Turbo"), ("large-v3", "Large v3"), ("medium", "Medium"),
        ("small", "Small"), ("base", "Base"), ("tiny", "Tiny"),
    ])
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
    def test_turbo_is_installed_here(self):
        assert "turbo" in models.installed_whisper_models()

    def test_returns_only_catalog_ids(self):
        assert models.installed_whisper_models() <= set(models.catalog_ids())

    def test_broken_cache_api_is_not_fatal(self, monkeypatch):
        """Ein unbekannter Zustand darf nicht als „installiert“ erfunden werden."""
        import huggingface_hub

        def boom():
            raise OSError("Cache kaputt")

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir", boom)
        assert models.installed_whisper_models() == set()

    def test_empty_cache_reports_nothing(self, monkeypatch):
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "scan_cache_dir",
                            lambda: type("C", (), {"repos": ()})())
        assert models.installed_whisper_models() == set()

    def test_foreign_repositories_are_ignored(self, monkeypatch):
        import huggingface_hub

        repo = type("R", (), {"repo_id": "openai/whisper-large",
                              "size_on_disk": 999, "revisions": [object()]})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir",
                            lambda: type("C", (), {"repos": (repo,)})())
        assert models.installed_whisper_models() == set()

    def test_empty_repo_folder_does_not_count(self, monkeypatch):
        import huggingface_hub

        repo = type("R", (), {"repo_id": models.find("tiny").repo_id,
                              "size_on_disk": 0, "revisions": []})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir",
                            lambda: type("C", (), {"repos": (repo,)})())
        assert "tiny" not in models.installed_whisper_models()

    def _cache_with(self, monkeypatch, names):
        import huggingface_hub

        files = [type("F", (), {"file_name": name})() for name in names]
        revision = type("V", (), {"files": files})()
        repo = type("R", (), {"repo_id": models.find("tiny").repo_id,
                              "size_on_disk": 4096, "revisions": [revision]})()
        monkeypatch.setattr(huggingface_hub, "scan_cache_dir",
                            lambda: type("C", (), {"repos": (repo,)})())

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


class _StubWindow:
    """Nur die Felder, die model_options, _select_model und der Download anfassen."""

    model_options = SW.model_options
    _update_model_hint = SW._update_model_hint
    _select_model = SW._select_model
    _begin_download = SW._begin_download
    _cancel_download = SW._cancel_download
    _attach_download = SW._attach_download
    on_download_progress = SW.on_download_progress
    on_download_finished = SW.on_download_finished
    download_text = staticmethod(SW.download_text)
    MODEL_HINT = SW.MODEL_HINT
    CANCELLED = None

    def _refresh_storage(self):
        """Baut im echten Fenster die Bestandszeile neu auf — hier nur gezaehlt."""
        self.storage_refreshed += 1

    def __init__(self, installed, active="turbo", switch_ok=True, problem=""):
        self._installed_models = set(installed)
        self._downloading = ""
        self.storage_refreshed = 0
        switched = []
        started = []
        cancelled = []

        class _App:
            active_model_size = active
            downloading_model = None
            download_done = 0
            download_total = 0
            download_percent = 0

            def __init__(self):
                self.switched = switched
                self.started = started
                self.cancelled = cancelled

            def switch_model(self, model_id):
                switched.append(model_id)
                return switch_ok

            def start_download(self, model_id):
                started.append(model_id)
                if not problem:
                    self.downloading_model = model_id
                return problem

            def cancel_download(self):
                cancelled.append(self.downloading_model)

        self.app = _App()
        self.description = SW.MODEL_HINT
        self.selected = None
        self.control = None
        self.row_model = type("R", (), {
            "set_description": lambda _s, text: setattr(self, "description", text),
            "set_control": lambda _s, widget: setattr(self, "control", widget)})()
        self.enabled = True
        self.cancel_enabled = True
        self.model_select = type("S", (), {
            "set_value": lambda _s, value: setattr(self, "selected", value),
            "set_options": lambda _s, options: None,
            "set_enabled": lambda _s, value: setattr(self, "enabled", value)})()
        self.cancel_button = type("B", (), {
            "set_enabled": lambda _s, value: setattr(self, "cancel_enabled", value)})()

    def alive(self):
        return True


@pytest.fixture
def config(monkeypatch):
    def _set(model_id):
        monkeypatch.setattr("wisper.main.CFG.model_size", model_id)

    return _set


class TestOptions:
    def test_two_groups(self, config):
        config("turbo")
        options = _StubWindow({"turbo"}).model_options()
        assert {o.group for o in options} == {"Installiert", "Verfügbar"}

    def test_installed_model_has_no_download_marker(self, config):
        config("turbo")
        options = _StubWindow({"turbo"}).model_options()
        turbo = next(o for o in options if o.value == "turbo")
        assert turbo.group == "Installiert" and turbo.trailing == ""

    def test_available_models_show_their_size(self, config):
        config("turbo")
        options = _StubWindow({"turbo"}).model_options()
        large = next(o for o in options if o.value == "large-v3")
        assert large.group == "Verfügbar" and "3,1 GB" in large.trailing

    def test_meta_lines_are_present(self, config):
        config("turbo")
        for option in _StubWindow({"turbo"}).model_options():
            assert option.meta

    def test_custom_config_value_is_shown_not_reset(self, config):
        """Ein eigener Wert darf nicht stillschweigend auf Turbo fallen."""
        config("large-v2")
        options = _StubWindow({"turbo"}).model_options()
        assert options[0].value == "large-v2"
        assert "Benutzerdefiniert" in options[0].label

    def test_all_six_are_offered(self, config):
        config("turbo")
        values = [o.value for o in _StubWindow({"turbo"}).model_options()]
        assert set(models.catalog_ids()) <= set(values)


class TestSelection:
    def test_installed_model_triggers_a_switch(self, config, monkeypatch):
        """Gespeichert wird erst nach erfolgreichem Wechsel, nicht beim Klick."""
        config("turbo")
        written = {}
        monkeypatch.setattr("wisper.main._persist_config_value",
                            lambda s, k, v: written.update({(s, k): v}))
        window = _StubWindow({"turbo", "small"})
        window._select_model("small")
        assert window.app.switched == ["small"]
        assert written == {}, "Config darf noch nicht auf das neue Modell zeigen"

    def test_uninstalled_model_is_not_persisted(self, config, monkeypatch):
        """Sonst zöge der nächste Start wortlos mehrere Gigabyte nach."""
        config("turbo")
        written = {}
        monkeypatch.setattr("wisper.main._persist_config_value",
                            lambda s, k, v: written.update({(s, k): v}))
        window = _StubWindow({"turbo"})
        window._select_model("large-v3")
        assert written == {}
        assert window.selected == "turbo", "Auswahl muss zurückspringen"
        assert window.app.started == ["large-v3"], "Auswahl startet den Download"

    def test_choosing_the_active_model_changes_nothing(self, config, monkeypatch):
        config("turbo")
        written = {}
        monkeypatch.setattr("wisper.main._persist_config_value",
                            lambda s, k, v: written.update({(s, k): v}))
        window = _StubWindow({"turbo"})
        window._select_model("turbo")
        assert written == {}
        assert window.app.switched == []

    def test_rejected_switch_restores_the_selection(self, config):
        """Waehrend einer Aufnahme lehnt die Anwendung ab — die Zeile sagt es."""
        config("turbo")
        window = _StubWindow({"turbo", "small"}, switch_ok=False)
        window._select_model("small")
        assert window.selected == "turbo"
        assert "Aufnahme" in window.description

    def test_switch_disables_the_select_while_loading(self, config):
        config("turbo")
        window = _StubWindow({"turbo", "small"})
        window._select_model("small")
        assert window.enabled is False
        assert "geladen" in window.description


class TestHint:
    def test_normal_state(self, config):
        config("turbo")
        window = _StubWindow({"turbo"}, active="turbo")
        window._update_model_hint()
        assert window.description == SW.MODEL_HINT

    def test_chosen_differs_from_active(self, config):
        """Aktiv und für den nächsten Start gewählt sind zwei Zustände."""
        config("small")
        window = _StubWindow({"turbo", "small"}, active="turbo")
        window._update_model_hint()
        assert "nächsten Start" in window.description

    def test_not_installed_is_named(self, config):
        config("medium")
        window = _StubWindow({"turbo"}, active="turbo")
        window._update_model_hint()
        assert "geladen" in window.description


class TestValidator:
    @pytest.mark.parametrize("model_id", list(models.catalog_ids()))
    def test_curated_models_pass(self, model_id):
        config = Config()
        config.model_size = model_id
        assert not [e for e in validate_config(config) if "model.size" in e]

    def test_custom_value_is_reported_but_not_fatal(self):
        config = Config()
        config.model_size = "large-v2"
        errors = [e for e in validate_config(config) if "model.size" in e]
        assert len(errors) == 1      # nur ein Hinweis, kein Abbruch

"""Loading a model that isn't cached goes through the download with progress, never silently.

faster-whisper fetches a missing model by itself, without any progress: the main window would say
"Loading model ..." for minutes while gigabytes come down. Every load of an uncached catalog model
therefore starts the app's own download first, and the main window shows its percentage.
"""

import pytest

from hams import models as whisper_models
from hams.config import CFG
from hams.whisper_model import WhisperModelMixin


class FakeDownloader:
    """`ModelDownloader` without network: remembers the start, the test plays the rest."""

    def __init__(self, problem=""):
        self.problem = problem
        self.model_id = None
        self.active = False
        self.started = []

    def start(self, info, on_progress, on_finished):
        if self.problem:
            return self.problem
        self.started.append(info.model_id)
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.active = False


@pytest.fixture
def app(live_app, model_cache_check, monkeypatch):
    """The live app whose configured model `small` is not cached; loads are recorded, not run."""
    live_app.downloader = FakeDownloader()
    live_app.requested = []
    monkeypatch.setattr(live_app, "_start_switch_thread", lambda model_id, *_: live_app.requested.append(model_id))
    # the real one (the live app replaces it with a counter), so "Try again" goes through it too
    monkeypatch.setattr(live_app, "_load_model_async", lambda: WhisperModelMixin._load_model_async(live_app))
    CFG.model_size = "small"
    live_app._apply_state("model_loading")
    return live_app


def load(app) -> None:
    app._load_model_async()


def status(app) -> str:
    return app.status_label.cget("text")


def finish(app, error="") -> None:
    app.downloader.active = False
    app._on_download_finished("small", error)
    app.root.update()


class TestAnUncachedModel:
    def test_is_downloaded_with_progress_instead_of_loaded(self, app):
        load(app)
        assert app.downloader.started == ["small"]
        assert app.requested == [], "faster-whisper would download it silently"
        assert status(app) == "Small wird heruntergeladen · 0 %"
        app._on_download_progress(243_106_186, 486_212_372)
        app.root.update()
        assert status(app) == "Small wird heruntergeladen · 50 %"

    def test_is_loaded_once_the_download_is_through(self, app):
        load(app)
        finish(app)
        assert app.requested == ["small"]
        assert status(app) == "Small wird geladen …"
        assert app._load_after_download is None

    def test_a_failed_download_is_an_error_with_try_again(self, app):
        load(app)
        finish(app, "Server nicht erreichbar")
        assert app._state == "error"
        assert status(app) == "Small konnte nicht heruntergeladen werden"
        assert app.footer_label.cget("text") == "Server nicht erreichbar"
        assert app.requested == []
        app._retry()
        assert app.downloader.started == ["small", "small"], "Try again downloads again"

    def test_a_cancelled_download_says_so(self, app):
        load(app)
        finish(app, whisper_models.CANCELLED)
        assert app.footer_label.cget("text") == "Der Download wurde abgebrochen."

    def test_a_download_that_cannot_start_is_reported(self, app):
        app.downloader = FakeDownloader(problem="Nicht genug Speicherplatz")
        load(app)
        assert app._state == "error"
        assert app.footer_label.cget("text") == "Nicht genug Speicherplatz"
        assert app.model_error == "Nicht genug Speicherplatz"

    def test_a_running_download_of_it_is_joined_not_restarted(self, app):
        app.downloader.active, app.downloader.model_id = True, "small"
        load(app)
        assert app.downloader.started == []
        assert app._load_after_download == "small"

    def test_progress_does_not_draw_over_a_recording(self, app):
        load(app)
        app._apply_state("recording")
        app._on_download_progress(100, 486_212_372)
        app.root.update()
        assert status(app) == "Aufnahme läuft"


class TestACachedModel:
    def test_loads_right_away(self, app, model_cache_check):
        model_cache_check.add("small")
        load(app)
        assert app.requested == ["small"]
        assert app.downloader.started == []

    def test_a_custom_model_is_never_downloaded(self, app):
        CFG.model_size = "C:/models/eigenes"
        load(app)
        assert app.requested == ["C:/models/eigenes"]


class TestStartDownload:
    def test_a_running_download_keeps_its_numbers(self, app):
        """Regression: the numbers were reset before the check for a running download."""
        app.downloader.active, app.downloader.model_id = True, "turbo"
        app.download_done, app.download_total, app.download_percent = 10, 100, 10
        assert app.start_download("small") == "Es läuft bereits ein Download"
        assert (app.download_done, app.download_total, app.download_percent) == (10, 100, 10)


class TestAFailedSwitchIsShown:
    def test_main_window_and_settings_say_which_model_failed(self, live_app):
        live_app.active_model_size = "turbo"
        live_app.model_ready.set()
        live_app._after_switch("turbo", True, persist=True, requested="large-v3")
        assert status(live_app) == "Large v3 ließ sich nicht laden"
        assert live_app.footer_label.cget("text").startswith("Weiter mit Turbo")
        live_app._open_settings()
        live_app.root.update()
        win = live_app.settings_win
        assert win.row_model._sub_text == "Large v3 ließ sich nicht laden · das bisherige Modell bleibt aktiv"

    def test_ready_names_the_loaded_model(self, live_app):
        CFG.model_size = "small"
        live_app.active_model_size = "turbo"
        live_app._apply_state("ready")
        assert status(live_app) == "Bereit · Turbo"

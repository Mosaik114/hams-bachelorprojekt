"""Der echte Einrichtungsassistent, geöffnet aus der echten Anwendung.

Die Entscheidungen prüft `test_setup_wizard.py` ohne Fenster. Hier geht es um das Zusammenspiel:
Seitenfolge mit Knöpfen, Fortschritt und Fehler mit „Erneut versuchen", der Modell-Download über
die Anwendung, Mikrofon mit Pegel, Ollama an und aus, und was beim Abschluss geschrieben wird.
GPU, Netz, Ollama und Mikrofon sind Attrappen (`setup_fakes`, `live_app`).
"""

import sys
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest
from setup_fakes import InlineThread, fake_services, online

from hams import gpu_support, i18n
from hams import models as whisper_models
from hams import setup_window as window_mod
from hams import setup_wizard as sw
from hams import ui_kit as uk
from hams.config import CFG
from hams.ollama import OllamaState
from hams.setup_window import SetupWindow


class FakeDownloader:
    """`ModelDownloader` ohne Netz: merkt sich die Rückrufe, der Test spielt den Verlauf."""

    def __init__(self, problem=""):
        self.problem = problem
        self.model_id = None
        self.active = False
        self.cancelled = False

    def start(self, info, on_progress, on_finished):
        if self.problem:
            return self.problem
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def wizard(live_app, hams, monkeypatch, tmp_path):
    """Öffnet den Assistenten mit Attrappen. `open_(**services)` überschreibt einzelne Dienste."""
    monkeypatch.setattr(sw, "TaskThread", InlineThread)
    monkeypatch.setattr(window_mod, "CONFIG_PATH", tmp_path / "config.toml")
    switched = []
    monkeypatch.setattr(live_app, "_start_switch_thread", lambda model_id, *_: switched.append(model_id))
    live_app.downloader = FakeDownloader()
    hams.switched = switched
    hams.marker = tmp_path / sw.MARKER_NAME
    CFG.model_size = "turbo"
    CFG.ollama_cleanup_enabled = True
    CFG.mic_device_raw = "Headset"

    def open_(**overrides):
        services = fake_services(**overrides)
        live_app.setup_win = SetupWindow(live_app, services)
        live_app.root.update()
        return live_app.setup_win

    yield open_
    win = live_app.setup_win
    if win is not None and win.alive():
        win.close()
    live_app.root.update()


def go_to(win, page):
    while win.controller.page != page:
        win.controller.next()
    win.show_page()
    win.app.root.update()


def texts(widget) -> list:
    """Alle Beschriftungen unterhalb eines Widgets (Labels und Einstellungszeilen)."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label) and child.cget("text"):
            found.append(child.cget("text"))
        found.extend(texts(child))
    return found


def page_text(win) -> str:
    return "\n".join(texts(win.scroll.body))


def status(row) -> tuple:
    return row.status_dot.status, row._sub_text


class TestOpening:
    def test_starts_on_the_welcome_page(self, wizard, hams):
        win = wizard()
        assert win.controller.page == "welcome"
        assert win.win.title() == "Hams — Einrichtungsassistent"
        assert win.step_label.cget("text") == "Schritt 1 von 6"
        assert not win.back_button.enabled
        assert win.skip_button.winfo_manager() == ""
        assert win.next_button.cget("text") == "Weiter"
        assert "Willkommen" in page_text(win)
        assert sw.read_marker(hams.marker) is False, "ein Abbruch soll beim naechsten Start wiederkommen"

    def test_a_completed_marker_is_not_reset(self, wizard, hams):
        sw.write_marker(hams.marker, True)
        wizard()
        assert sw.read_marker(hams.marker) is True

    def test_opening_twice_focuses_the_open_window(self, wizard, live_app):
        win = wizard()
        live_app._open_setup()
        live_app.root.update()
        assert live_app.setup_win is win

    def test_the_app_opens_it(self, live_app, wizard, monkeypatch):
        monkeypatch.setattr(window_mod.setup_wizard, "default_services", fake_services)
        live_app._open_setup()
        live_app.root.update()
        assert isinstance(live_app.setup_win, SetupWindow)


class TestWelcome:
    def test_dictation_language_is_saved(self, wizard, hams):
        win = wizard()
        win._select_dictation_language("en")
        assert CFG.transcription_language == "en"
        assert ("transcription", "language", "en") in hams.saved
        win._select_dictation_language("klingonisch")
        assert CFG.transcription_language == "en"

    def test_interface_language_switches_the_wizard_at_once(self, wizard, hams, live_app):
        win = wizard()
        win._select_ui_language("en")
        live_app.root.update()
        assert i18n.language() == "en"
        assert ("ui", "language", "en") in hams.saved
        assert win.step_label.cget("text") == "Step 1 of 6"
        assert "Welcome" in page_text(win)
        win.go_next()  # die neu gebaute Fusszeile funktioniert
        assert win.controller.page == "hardware"
        win._select_ui_language("en")  # unveraendert: nichts passiert
        assert win.controller.page == "hardware"


class TestNavigation:
    def test_next_back_and_skip(self, wizard):
        win = wizard()
        win.go_next()
        assert win.controller.page == "hardware"
        assert win.skip_button.winfo_manager() == "", "Weiter genügt: Überspringen täte dasselbe"
        assert win.back_button.enabled
        win.go_back()
        assert win.controller.page == "welcome"
        win.go_back()  # auf der ersten Seite wirkungslos
        assert win.controller.page == "welcome"
        go_to(win, "hardware")
        win.skip()  # ohne Knopf auch per Aufruf wirkungslos
        assert win.controller.page == "hardware"
        go_to(win, "cleanup")
        assert win.skip_button.winfo_manager() == "pack"
        win.skip()
        assert win.controller.page == "finish"

    def test_model_page_blocks_until_a_model_is_there(self, wizard):
        win = wizard()
        go_to(win, "model")
        assert not win.next_button.enabled
        win.go_next()
        assert win.controller.page == "model"

    def test_the_last_page_says_finish(self, wizard):
        win = wizard()
        go_to(win, "finish")
        assert win.next_button.cget("text") == "Fertig"
        assert win.step_label.cget("text") == "Schritt 6 von 6"


class TestHardware:
    def test_cpu_is_explained(self, wizard):
        win = wizard()
        go_to(win, "hardware")
        assert "Prozessor (CPU)" in page_text(win)
        assert win.gpu_row is None

    def test_ready_gpu_needs_no_download(self, wizard):
        win = wizard(gpu_present=lambda: True, gpu_libraries_found=lambda: True)
        go_to(win, "hardware")
        assert "NVIDIA-Grafikkarte ist bereit" in page_text(win)
        assert win.gpu_row is None

    def test_source_install_is_told_about_pip(self, wizard):
        win = wizard(gpu_present=lambda: True, gpu_download_supported=lambda: False)
        go_to(win, "hardware")
        assert 'pip install -e ".[cuda]"' in page_text(win)

    def test_download_with_progress_then_gpu_ready(self, wizard, live_app):
        found = {"libs": False}
        seen = []

        def install(progress, cancelled):
            progress(700_000_000, 1_400_000_000, "download")
            seen.append(status(live_app.setup_win.gpu_row.row))
            found["libs"] = True

        win = wizard(gpu_present=lambda: True, gpu_libraries_found=lambda: found["libs"], install_gpu=install)
        go_to(win, "hardware")
        row = win.gpu_row
        assert status(row.row) == ("idle", "NVIDIA-Bibliotheken von PyPI (1,4 GB), nach dem Laden geprüft")
        assert row.row._control is row.button
        row.button._invoke()
        live_app.root.update()
        assert win.controller.gpu_task.state == sw.DONE
        assert status(row.row) == ("ready", "Installiert · ab jetzt rechnet die GPU")
        assert row.row._control is None
        assert "NVIDIA-Grafikkarte ist bereit" in page_text(win)
        assert win.controller.hardware == sw.GPU_READY

    def test_running_download_shows_bar_and_cancel_and_holds_the_page(self, wizard, live_app):
        win = wizard(gpu_present=lambda: True)
        go_to(win, "hardware")
        win.controller.gpu_task.begin()
        win.controller.gpu_task.advance(700_000_000, 2_800_000_000, "download")
        win._after_task_change()
        live_app.root.update()
        row = win.gpu_row
        assert row.row._control is row.cancel_button
        assert row.bar_box.winfo_manager() == "pack"
        assert row.bar.level == pytest.approx(0.25)
        assert status(row.row) == ("processing", "25 % · 0,7/2,8 GB")
        assert not (win.next_button.enabled or win.back_button.enabled)
        row.cancel_button._invoke()
        assert win.controller.gpu_task.cancel_event.is_set()

    def test_network_failure_offers_retry(self, wizard, live_app):
        attempts = []

        def install(progress, cancelled):
            attempts.append(1)
            if len(attempts) == 1:
                raise gpu_support.GpuSupportError("cublas.whl: <urlopen error [Errno 11001] getaddrinfo failed>")

        win = wizard(gpu_present=lambda: True, install_gpu=install)
        go_to(win, "hardware")
        win._start_gpu_download()
        live_app.root.update()
        row = win.gpu_row
        assert status(row.row) == ("error", "Keine Verbindung zum Modell-Server")
        assert row.button.cget("text") == "Erneut versuchen"
        assert "Traceback" not in page_text(win)
        win._start_gpu_download()
        live_app.root.update()
        assert win.controller.gpu_task.state == sw.DONE

    def test_cancel_keeps_the_offer(self, wizard, live_app):
        def install(progress, cancelled):
            live_app.setup_win.controller.gpu_task.cancel()
            raise gpu_support.InstallCancelled("weg")

        win = wizard(gpu_present=lambda: True, install_gpu=install)
        go_to(win, "hardware")
        win._start_gpu_download()
        live_app.root.update()
        row_status, text = status(win.gpu_row.row)
        assert row_status == "warning" and text.startswith("Abgebrochen")
        assert win.gpu_row.button.cget("text") == "Laden"

    def test_full_disk_is_said_before_downloading(self, wizard, live_app):
        win = wizard(gpu_present=lambda: True, free_space=lambda: 500_000_000)
        go_to(win, "hardware")
        win._start_gpu_download()
        live_app.root.update()
        row_status, text = status(win.gpu_row.row)
        assert row_status == "error" and text.startswith("Zu wenig Speicherplatz")


class TestModel:
    def test_cpu_preselects_small_and_downloads_it_through_the_app(self, wizard, live_app, hams):
        win = wizard()
        go_to(win, "model")
        assert win.controller.choices.model == "small"
        assert win.model_select.value == "small"
        assert "empfehlen wir Small" in page_text(win)
        assert status(win.model_row.row) == ("idle", "486 MB · noch nicht geladen")
        win._start_model_download()
        live_app.root.update()
        assert live_app.downloading_model == "small"
        assert win.next_button.enabled, "der Download laeuft in der Anwendung weiter"
        live_app._on_download_progress(243_106_186, 486_212_372)
        live_app.root.update()
        assert status(win.model_row.row) == ("processing", "50 % · 243/486 MB")
        assert not win.model_select.enabled
        win._choose_model("tiny")  # waehrend des Downloads gesperrt
        assert win.controller.choices.model == "small"
        live_app.downloader.active = False
        win.controller.services.state.installed = {"small"}
        live_app._on_download_finished("small", "")
        live_app.root.update()
        assert status(win.model_row.row) == ("ready", "Geladen und bereit")
        assert hams.switched == ["small"], "die Anwendung laedt das fertige Modell"
        assert win.next_button.enabled
        win.go_next()
        assert win.controller.page == "microphone"

    def test_failed_download_shows_the_reason_and_retry(self, wizard, live_app):
        win = wizard()
        go_to(win, "model")
        win._start_model_download()
        live_app.downloader.active = False
        live_app._on_download_finished("small", "Die Festplatte ist voll")
        live_app.root.update()
        assert status(win.model_row.row) == ("error", "Die Festplatte ist voll")
        assert win.model_row.button.cget("text") == "Erneut versuchen"
        assert not win.next_button.enabled

    def test_cancelled_download(self, wizard, live_app):
        win = wizard()
        go_to(win, "model")
        win._start_model_download()
        win.model_row.cancel_button._invoke()
        assert live_app.downloader.cancelled
        live_app.downloader.active = False
        live_app._on_download_finished("small", whisper_models.CANCELLED)
        live_app.root.update()
        assert win.model_task.state == sw.CANCELLED

    def test_refused_start_is_shown(self, wizard, live_app):
        win = wizard()
        live_app.downloader = FakeDownloader(problem="Nicht genug Speicherplatz")
        go_to(win, "model")
        win._start_model_download()
        assert status(win.model_row.row) == ("error", "Nicht genug Speicherplatz")

    def test_news_about_another_model_is_ignored(self, wizard, live_app):
        win = wizard()
        go_to(win, "model")
        win.on_download_finished("tiny", "", False)
        assert win.model_task.state == sw.IDLE

    def test_installed_choice_is_loaded_when_leaving(self, wizard, live_app, hams):
        win = wizard()
        win.controller.services.state.installed = {"base"}
        go_to(win, "model")
        win._choose_model("base")
        assert status(win.model_row.row) == ("ready", "Geladen und bereit")
        assert win.next_button.enabled
        win.go_next()
        assert win.controller.page == "microphone"
        assert hams.switched == ["base"]

    def test_gpu_installed_during_setup_reloads_a_cpu_model(self, wizard, live_app, hams):
        win = wizard(gpu_present=lambda: True, gpu_libraries_found=lambda: True)
        win.controller.services.state.installed = {"turbo"}
        live_app.active_model_size = "turbo"
        live_app._active_device = "cpu"
        live_app.model_ready.set()
        go_to(win, "model")
        win.go_next()
        assert hams.switched == ["turbo"]

    def test_nothing_reloads_when_the_model_is_active(self, wizard, live_app, hams):
        win = wizard(gpu_present=lambda: True, gpu_libraries_found=lambda: True)
        win.controller.services.state.installed = {"turbo"}
        live_app.active_model_size = "turbo"
        live_app._active_device = "cuda"
        live_app.model_ready.set()
        go_to(win, "model")
        win.go_next()
        assert hams.switched == []

    def test_a_running_download_is_shown_when_the_wizard_opens(self, wizard, live_app):
        live_app.downloader = SimpleNamespace(active=True, model_id="medium", cancel=lambda: None)
        live_app.download_done, live_app.download_total = 10, 100
        win = wizard()
        go_to(win, "model")
        assert win.controller.choices.model == "medium"
        assert win.model_task.running
        assert win.model_row.row._control is win.model_row.cancel_button


class TestMicrophone:
    def test_level_and_sound_check(self, wizard, hams, live_app):
        win = wizard()
        go_to(win, "microphone")
        stream = hams.streams[-1]
        assert stream.running
        assert status(win.row_check) == ("idle", "Sag etwas …")
        stream.feed(np.full(1600, 0.3))
        win._tick()
        assert win.meter.level > 0.4
        assert status(win.row_check) == ("ready", "Gehört · das Mikrofon funktioniert")
        assert win.row_mic.status_dot.status == "ready"
        win.go_next()
        assert stream.closed, "der Pegel laeuft nur auf der Mikrofonseite"

    def test_choosing_a_device_saves_it(self, wizard, hams):
        win = wizard()
        go_to(win, "microphone")
        win._select_device("Headset")
        assert ("audio", "device", "Headset") in hams.saved

    def test_a_recording_borrows_the_microphone(self, wizard, hams, live_app):
        win = wizard()
        go_to(win, "microphone")
        first = hams.streams[-1]
        live_app._suspend_mic_consumers()
        assert first.closed
        live_app._resume_mic_consumers()
        assert hams.streams[-1] is not first and hams.streams[-1].running
        go_to(win, "cleanup")
        count = len(hams.streams)
        live_app._suspend_mic_consumers()
        live_app._resume_mic_consumers()
        assert len(hams.streams) == count, "ausserhalb der Mikrofonseite bleibt es zu"


class TestCleanup:
    def test_without_ollama_it_stays_calm_and_off(self, wizard, live_app):
        win = wizard()
        go_to(win, "cleanup")
        live_app.root.update()
        text = page_text(win)
        assert "Läuft nicht auf diesem Rechner" in text
        assert "funktioniert ohne sie" in text
        assert win.controller.choices.cleanup is False
        get = next(
            w for w in win.cleanup_box.winfo_children()[0].body.winfo_children() if isinstance(w, uk.SecondaryButton)
        )
        get._invoke()
        assert win.controller.services.state.opened == [window_mod.OLLAMA_DOWNLOAD_URL]

    def test_check_again_finds_a_started_ollama(self, wizard, live_app):
        answers = [OllamaState(False, error="aus"), online("qwen3.5:9b")]
        win = wizard(probe_ollama=lambda: answers.pop(0))
        go_to(win, "cleanup")
        live_app.root.update()
        win.check_ollama()
        live_app.root.update()
        text = page_text(win)
        assert "qwen3.5:9b ist installiert" in text
        assert win.controller.choices.cleanup is True

    def test_a_crashing_probe_still_ends_the_check(self, wizard, live_app):
        """Regression: an exception in the probe thread left the page on "checking" for good."""

        def crash():
            raise AttributeError("'list' object has no attribute 'get'")

        win = wizard(probe_ollama=crash)
        go_to(win, "cleanup")
        live_app.root.update()
        assert win.controller.ollama is not None
        assert not win.controller.ollama.online
        assert win.controller.ollama.error

    def test_missing_model_can_be_pulled(self, wizard, live_app):
        answers = [online("llama3.2:3b"), online("llama3.2:3b", "qwen3.5:9b")]
        progress_seen = []

        def pull(name, progress, cancelled):
            progress(3_000_000_000, 6_000_000_000)
            progress_seen.append(status(live_app.setup_win.pull_row.row))

        win = wizard(probe_ollama=lambda: answers.pop(0), pull_ollama=pull)
        go_to(win, "cleanup")
        live_app.root.update()
        assert status(win.pull_row.row)[1].startswith("qwen3.5:9b ist noch nicht installiert")
        win._start_pull()
        live_app.root.update()
        live_app.root.update()
        assert win.controller.choices.cleanup is True
        assert "qwen3.5:9b ist installiert" in page_text(win)

    def test_running_pull_updates_its_row(self, wizard, live_app):
        win = wizard(probe_ollama=lambda: online())
        go_to(win, "cleanup")
        live_app.root.update()
        row = win.pull_row
        win.controller.pull_task.begin()
        win.controller.pull_task.advance(1_000_000_000, 4_000_000_000)
        win._after_task_change()
        assert win.pull_row is row, "Fortschritt baut die Zeile nicht neu"
        assert status(row.row) == ("processing", "25 % · 1,0/4,0 GB")
        assert not win.next_button.enabled

    def test_skip_turns_cleanup_off(self, wizard, live_app):
        win = wizard(probe_ollama=lambda: online("qwen3.5:9b"))
        go_to(win, "cleanup")
        live_app.root.update()
        win.skip()
        assert win.controller.choices.cleanup is False


class TestFinish:
    def test_shortcuts_status_and_finish(self, wizard, live_app, hams):
        win = wizard(probe_ollama=lambda: online("qwen3.5:9b"))
        go_to(win, "cleanup")
        live_app.root.update()
        go_to(win, "finish")
        text = page_text(win)
        assert "Strg+Umschalt+Leertaste drücken" in text
        if sys.platform == "win32":
            assert "Einstellungen → Kurzbefehle ändern" in text
        else:  # KDE manages the portal shortcuts
            assert "Tastatur → Kurzbefehle" in text
        assert "[hotkeys]" not in text
        live_app.status_label.config(text="Bereit · Turbo")
        win._poll_status()
        assert win.status_row.value_label.cget("text") == "Bereit · Turbo"
        win.go_next()
        live_app.root.update()
        assert sw.read_marker(hams.marker) is True
        assert ("ollama", "cleanup_enabled", True) in win.controller.services.state.persisted
        assert CFG.ollama_cleanup_enabled is True
        assert live_app.setup_win is None
        assert not win.alive()
        assert live_app._window_visible

    def test_finishing_without_ollama_switches_cleanup_off(self, wizard, live_app):
        win = wizard()
        go_to(win, "cleanup")
        live_app.root.update()
        go_to(win, "finish")
        win.finish()
        assert CFG.ollama_cleanup_enabled is False


class TestClosing:
    def test_closing_early_keeps_the_setup_open_for_next_time(self, wizard, live_app, hams):
        win = wizard(gpu_present=lambda: True)
        go_to(win, "microphone")
        stream = hams.streams[-1]
        win.controller.gpu_task.begin()
        win.close_button._invoke()
        live_app.root.update()
        assert stream.closed
        assert win not in live_app._mic_consumers
        assert win.controller.gpu_task.cancel_event.is_set()
        assert sw.read_marker(hams.marker) is False
        win.close()  # zweimal schliessen schadet nicht
        assert live_app.setup_win is None

    def test_late_messages_after_closing_are_dropped(self, wizard, live_app):
        win = wizard()
        win.close()
        win.post(lambda: pytest.fail("darf nicht mehr laufen"))
        live_app.root.update()
        win.on_download_progress(1, 2, 50)
        assert not win.alive()

    def test_retint_follows_the_appearance(self, wizard, live_app):
        win = wizard()
        live_app.set_appearance(uk.Theme.DARK)
        assert win.win.cget("bg") == uk.Theme.palette(uk.Theme.DARK)["WINDOW_BG"]

    def test_scale_change_resizes(self, wizard):
        win = wizard()
        win._on_scale_change(1.0)
        win.close()
        win._on_scale_change(1.0)  # geschlossen: nichts mehr anfassen


class TestDrag:
    def test_dragging_moves_the_window(self, wizard, live_app):
        win = wizard()
        win._start_drag(SimpleNamespace(x=5, y=5))
        before = win.win.winfo_x()
        win._on_drag(SimpleNamespace(x=25, y=5))
        live_app.root.update()
        assert win.win.winfo_x() != before or win.win.winfo_x() == before + 20


class TestAppIntegration:
    def test_tray_offers_the_setup(self, live_app):
        labels = [item.text for item in live_app._build_tray_menu().items]
        assert "Einrichtungsassistent …" in labels

    def test_settings_open_the_setup(self, live_app, wizard, monkeypatch):
        opened = []
        monkeypatch.setattr(live_app, "_open_setup", lambda *a: opened.append(1))
        live_app._open_settings()
        live_app.root.update()
        settings = live_app.settings_win
        assert settings.row_setup._title_text == "Einrichtungsassistent"
        settings.setup_button._invoke()
        assert opened == [1]

    def test_settings_call_a_missing_ollama_optional_when_cleanup_is_off(self, live_app, monkeypatch):
        monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: {"turbo"})
        monkeypatch.setattr(whisper_models, "cached_whisper_models", lambda: ())
        CFG.ollama_cleanup_enabled = False
        live_app.ollama_checked = True
        live_app.ollama_state = OllamaState(False, error="Ollama ist nicht erreichbar")
        live_app._open_settings()
        live_app.root.update()
        settings = live_app.settings_win
        assert status(settings.row_cleanup_model) == ("idle", "Optional · Ollama läuft nicht")
        settings._on_cleanup(True)
        assert status(settings.row_cleanup_model)[0] == "error", "eingeschaltet ist es wieder ein Problem"

    def test_closing_releases_a_deferred_model(self, live_app, hams):
        before = hams.loads  # der Start der Testanwendung hat schon einmal geladen
        live_app._model_deferred = True
        live_app.on_setup_closed(False)
        assert hams.loads == before + 1
        live_app.on_setup_closed(False)
        assert hams.loads == before + 1, "nur einmal"

    def test_a_running_download_takes_care_of_the_model(self, live_app, hams):
        live_app._model_deferred = True
        live_app.downloader = SimpleNamespace(active=True, model_id="small", cancel=lambda: None)
        before = hams.loads
        live_app.on_setup_closed(True)
        assert hams.loads == before


class TestDeferredStart:
    @pytest.fixture
    def deferred_app(self, root, hams, monkeypatch):
        from hams.app import FloatingTranscriberApp as App

        monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: set())
        window = tk.Toplevel(root)
        app = App(window, setup=True)
        yield app
        app._shutting_down = True
        app._release_resources()
        app._cancel_position_job()
        window.destroy()

    def test_first_start_waits_for_the_wizard(self, deferred_app, hams):
        assert hams.loads == 0
        assert deferred_app.status_label.cget("text") == "Einrichtung abschließen, um ein Sprachmodell zu laden"
        deferred_app.on_setup_closed(False)
        assert hams.loads == 1

    def test_an_installed_model_loads_right_away(self, root, hams, monkeypatch):
        from hams.app import FloatingTranscriberApp as App

        monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: {CFG.model_size})
        window = tk.Toplevel(root)
        app = App(window, setup=True)
        try:
            assert hams.loads == 1
        finally:
            app._shutting_down = True
            app._release_resources()
            app._cancel_position_job()
            window.destroy()

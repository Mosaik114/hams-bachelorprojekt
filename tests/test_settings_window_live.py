"""Das echte Einstellungsfenster, geoeffnet aus der echten Anwendung.

Die Logik der Abschnitte pruefen andere Dateien an Attrappen ohne Fenster.
Hier geht es darum, dass das Zusammenspiel stimmt: Aufbau, Live-Pegel,
Mikrofon-Teilhabe mit der Aufnahme, Ollama-Abfrage, Schalter, Modellwahl,
Download- und Loeschanzeige und das saubere Schliessen.
"""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from hams import models as whisper_models
from hams import ollama as ollama_api
from hams import settings_models as models_mod
from hams import settings_status as status_mod
from hams import ui_kit as uk
from hams.config import CFG
from hams.models import CachedModel
from hams.ui_kit import window as uk_window


@pytest.fixture
def disk(monkeypatch):
    """Der Modellbestand auf der Platte - ohne den echten Cache zu lesen."""
    state = SimpleNamespace(installed={"large-v3-turbo"}, cached=[], deleted=[])

    def delete(model_id):
        state.deleted.append(model_id)
        state.cached = [e for e in state.cached if e.model_id != model_id]
        return True, 1000, "Tiny gelöscht · 75 MB frei"

    monkeypatch.setattr(whisper_models, "installed_whisper_models", lambda: set(state.installed))
    monkeypatch.setattr(whisper_models, "cached_whisper_models", lambda: tuple(state.cached))
    monkeypatch.setattr(whisper_models, "delete_whisper_model", delete)
    return state


class InlineThread:
    """Fuehrt die Arbeit beim Start sofort aus, auf dem Testthread.

    Die Hintergrundarbeit meldet sich per `win.after` zurueck; ohne laufende
    Tk-Hauptschleife geht das von einem fremden Thread aus ins Leere.
    """

    def __init__(self, target, args=(), **_options):
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture
def open_settings(live_app, disk, monkeypatch):
    """Oeffnet das Fenster wie ein Klick aufs Zahnrad, samt erster Ollama-Abfrage."""
    inline = SimpleNamespace(Thread=InlineThread)
    monkeypatch.setattr(status_mod, "threading", inline)
    monkeypatch.setattr(models_mod, "threading", inline)
    CFG.model_size = "large-v3-turbo"
    CFG.mic_device_raw = "Headset"

    def _open():
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win

    return _open


def sub(row) -> str:
    return row._sub_text


def dot(row) -> str:
    return row.status_dot._status


class TestOpening:
    def test_the_microphone_list_shows_the_configured_device(self, open_settings):
        win = open_settings()
        values = [option.value for option in win.mic_select.options]
        assert values == [None, "Headset"]
        assert win.mic_select.value == "Headset"
        assert win._device_error == ""

    def test_an_unplugged_device_is_named(self, open_settings):
        CFG.mic_device_raw = "Studio-Mikro"
        win = open_settings()
        win._tick()
        assert dot(win.row_mic) == "error"
        assert sub(win.row_mic) == "„Studio-Mikro“ ist nicht verbunden"

    def test_without_any_microphone_nothing_is_opened(self, open_settings, hams):
        hams.devices = []
        win = open_settings()
        assert not win.mic_select.enabled
        assert hams.streams == []
        assert sub(win.row_mic) == "Kein Mikrofon gefunden"

    def test_the_scan_result_is_shown_without_waiting_for_a_level_tick(self, open_settings, hams, monkeypatch):
        """Regression (Linux CI): the status came only from the next 60 ms tick, so a fast machine still read
        "device unavailable" right after opening. The scan result now sets it itself."""
        from hams.settings_window import SettingsWindow

        monkeypatch.setattr(SettingsWindow, "_tick", lambda self: None)
        hams.devices = []
        win = open_settings()
        assert sub(win.row_mic) == "Kein Mikrofon gefunden"
        assert dot(win.row_mic) == "error"

    def test_the_hardware_row_follows_the_model(self, open_settings, live_app):
        win = open_settings()
        assert win.row_hardware.value_label.cget("text") == "Modell wird geladen …"
        live_app._active_device = "cuda"
        live_app.model_ready.set()
        win._refresh_status()
        assert win.row_hardware.value_label.cget("text") == "Automatisch · GPU"
        live_app.model_error = "kaputt"
        win._refresh_status()
        assert win.row_hardware.value_label.cget("text") == "Nicht geladen · siehe Fehlerbehebung"

    def test_it_registers_as_a_microphone_user(self, open_settings, live_app):
        win = open_settings()
        assert win in live_app._mic_consumers


class TestLevel:
    @pytest.mark.parametrize(
        "amplitude, status, text",
        [
            (0.3, "ready", "Gutes Signal"),
            (0.03, "warning", "Leiser Pegel"),
            (0.0, "error", "Kein Signal"),
        ],
    )
    def test_the_live_level_is_named_not_only_coloured(self, open_settings, hams, amplitude, status, text):
        win = open_settings()
        hams.streams[-1].feed(np.full(256, amplitude))
        win._tick()
        assert (dot(win.row_mic), sub(win.row_mic)) == (status, text)

    def test_a_monitor_that_cannot_open_is_reported(self, open_settings, hams):
        from live_app import FakeStream

        FakeStream.fail_with = OSError("belegt")
        win = open_settings()
        win._tick()
        assert sub(win.row_mic) == "Gerät nicht verfügbar"

    def test_a_recording_takes_the_microphone_and_gives_it_back(self, open_settings, live_app, hams, monkeypatch):
        monkeypatch.setattr(live_app, "_transcribe_audio", lambda audio, **_job: None)
        win = open_settings()
        monitor = hams.streams[-1]
        live_app.start_recording()
        assert monitor.closed and win._mon_stream is None
        recording = hams.streams[-1]
        recording.feed(np.full(256, 0.3))
        win._tick()
        assert win.meter._level > 0, "waehrend der Aufnahme zeigt es deren Pegel"
        live_app.stop_recording_and_transcribe()
        assert win._mon_stream is not None and win._mon_stream is not monitor

    def test_a_new_device_reopens_the_monitor(self, open_settings, hams):
        win = open_settings()
        first = hams.streams[-1]
        win._select_device("Headset")
        assert first.closed and hams.streams[-1] is not first
        assert ("audio", "device", "Headset") in hams.saved
        win._select_device(None)
        assert CFG.mic_device_raw == "default" and CFG.mic_device is None
        assert ("audio", "device", "default") in hams.saved


class TestOllama:
    def test_an_unreachable_service_is_shown_everywhere_it_matters(self, open_settings, live_app):
        CFG.ollama_cleanup_enabled = True
        win = open_settings()
        assert live_app.ollama_checked
        assert (dot(win.row_cleanup_model), sub(win.row_cleanup_model)) == ("error", "aus")
        assert sub(win.row_prompt_model) == "Ollama nicht erreichbar"
        assert sub(win.row_cleanup) == "Ollama ist nicht erreichbar — Rohtext wird eingefügt"
        assert not win.row_cleanup_model.enabled

    def test_a_running_service_fills_both_lists(self, open_settings, hams, live_app):
        hams.probe = ollama_api.OllamaState(
            True, models=[ollama_api.OllamaModel("qwen3:8b"), ollama_api.OllamaModel("gemma3:4b")], warm=["qwen3:8b"]
        )
        CFG.ollama_model_cleanup = "qwen3:8b"
        win = open_settings()
        names = [o.value for o in win.select_cleanup_model.options if o.value]
        assert names == ["qwen3:8b", "gemma3:4b"]
        assert live_app.ollama_state is hams.probe
        assert sub(win.row_cleanup_model) == "Ollama erreichbar"
        assert win.row_prompt_model.enabled

    def test_the_first_answer_before_any_check_says_checking(self, open_settings, live_app):
        win = open_settings()
        live_app.ollama_checked = False
        win._show_ollama_status()
        assert (dot(win.row_cleanup_model), sub(win.row_cleanup_model)) == ("idle", "Ollama wird geprüft …")

    def test_each_list_writes_only_its_own_key(self, open_settings, hams):
        win = open_settings()
        CFG.ollama_model_cleanup, CFG.ollama_model_prompt = "a", "b"
        win._select_cleanup_model("c")
        win._select_prompt_model("d")
        win._select_cleanup_model("c")  # schon gewaehlt
        win._select_prompt_model("")  # Platzhalter
        assert hams.saved == [("ollama", "model_transcript_cleanup", "c"), ("ollama", "model_prompt_generate", "d")]


class TestSwitches:
    def test_cleanup_is_saved_and_updates_its_dependants(self, open_settings, hams):
        CFG.ollama_cleanup_enabled = False
        win = open_settings()
        win._toggle_cleanup()
        assert CFG.ollama_cleanup_enabled is True
        assert ("ollama", "cleanup_enabled", True) in hams.saved
        assert "Rohtext" in sub(win.row_cleanup)

    def test_the_signal_tone_is_saved(self, open_settings, hams):
        CFG.sound_feedback = True
        win = open_settings()
        win._toggle_sound()
        assert CFG.sound_feedback is False
        assert ("ui", "sound_feedback", False) in hams.saved

    def test_choosing_the_current_appearance_writes_nothing(self, open_settings, hams):
        win = open_settings()
        win._select_appearance(uk.Theme.mode)
        assert not [s for s in hams.saved if s[1] == "appearance"]

    def test_a_new_appearance_is_saved_and_applied(self, open_settings, hams):
        win = open_settings()
        other = uk.Theme.LIGHT if uk.Theme.mode == uk.Theme.DARK else uk.Theme.DARK
        win._select_appearance(other)
        assert ("ui", "appearance", other) in hams.saved
        assert uk.Theme.mode == other
        assert win.win.cget("bg") == uk.Theme.WINDOW_BG


class TestWhisperModel:
    def test_the_current_model_only_refreshes_the_hint(self, open_settings, live_app, monkeypatch):
        win = open_settings()
        monkeypatch.setattr(live_app, "switch_model", lambda m: pytest.fail("gewechselt"))
        win._select_model("large-v3-turbo")
        assert sub(win.row_model) == win.model_hint("large-v3-turbo")

    def test_an_installed_model_is_switched_to(self, open_settings, live_app, disk, monkeypatch):
        disk.installed.add("tiny")
        win = open_settings()
        win._refresh_models()
        monkeypatch.setattr(live_app, "switch_model", lambda m: True)
        win._select_model("tiny")
        assert not win.model_select.enabled
        assert sub(win.row_model) == "Modell wird geladen …"

    def test_a_refused_switch_keeps_the_old_choice(self, open_settings, live_app, disk, monkeypatch):
        disk.installed.add("tiny")
        win = open_settings()
        win._refresh_models()
        monkeypatch.setattr(live_app, "switch_model", lambda m: False)
        win._select_model("tiny")
        assert win.model_select.value == "large-v3-turbo"
        assert sub(win.row_model) == "Während einer Aufnahme nicht wechselbar"

    def test_a_missing_model_is_downloaded_not_saved(self, open_settings, live_app, hams, monkeypatch):
        win = open_settings()
        started = []
        monkeypatch.setattr(live_app, "start_download", lambda m: started.append(m) or "")
        win._select_model("tiny")
        assert started == ["tiny"]
        assert win.row_model._control is win.cancel_button
        assert win.model_select.value == "large-v3-turbo"
        assert not [s for s in hams.saved if s[1] == "model_size"]
        win.on_download_progress(50, 100, 50)
        assert "50 %" in sub(win.row_model)

    def test_a_refused_download_says_why(self, open_settings, live_app, monkeypatch):
        win = open_settings()
        monkeypatch.setattr(live_app, "start_download", lambda m: "Kein Speicherplatz")
        win._select_model("tiny")
        assert sub(win.row_model) == "Kein Speicherplatz"
        assert win.row_model._control is win.model_select

    @pytest.mark.parametrize(
        "error, switching, text, enabled",
        [
            (whisper_models.CANCELLED, False, "Tiny: Download abgebrochen", True),
            ("Netz weg", False, "Netz weg — erneut auswählen", True),
            ("", True, "Tiny wird geladen …", False),
        ],
    )
    def test_the_end_of_a_download(self, open_settings, live_app, monkeypatch, error, switching, text, enabled):
        win = open_settings()
        monkeypatch.setattr(live_app, "start_download", lambda m: "")
        win._select_model("tiny")
        win.on_download_finished("tiny", error, switching)
        assert win.row_model._control is win.model_select
        assert sub(win.row_model) == text
        assert win.model_select.enabled is enabled

    def test_cancelling_asks_the_app(self, open_settings, live_app, monkeypatch):
        win = open_settings()
        cancelled = []
        monkeypatch.setattr(live_app, "cancel_download", lambda: cancelled.append(1))
        win._cancel_download()
        assert cancelled == [1]
        assert sub(win.row_model) == "Wird abgebrochen …"

    def test_a_running_download_is_shown_when_the_window_opens(self, open_settings, live_app):
        live_app.downloader = SimpleNamespace(active=True, model_id="tiny", cancel=lambda: None)
        live_app.download_done, live_app.download_total = 0, 0
        win = open_settings()
        assert win.row_model._control is win.cancel_button
        assert sub(win.row_model).startswith("Tiny · ")


class TestDeleting:
    def test_nothing_to_delete_shows_no_control(self, open_settings, disk):
        disk.cached = [CachedModel("large-v3-turbo", "Turbo", 1_600_000_000, True, ("a",))]
        win = open_settings()
        assert win.row_storage._control is None
        assert sub(win.row_storage).startswith("1 Modell · ")

    def test_delete_asks_first_then_deletes_in_the_background(self, open_settings, disk, live_app):
        disk.cached = [CachedModel("tiny", "Tiny", 78_000_000, True, ("a",))]
        win = open_settings()
        assert win.row_storage._control is win.select_delete
        win._choose_delete("tiny")
        assert win.row_storage._control is win._delete_box
        assert sub(win.row_storage).startswith("Tiny löschen?")
        assert disk.deleted == []
        win._confirm_delete()
        live_app.root.update()
        assert disk.deleted == ["tiny"]
        assert sub(win.row_storage) == "Tiny gelöscht · 75 MB frei"
        win._clear_storage_note()
        assert sub(win.row_storage) == "Noch nichts gespeichert"

    def test_aborting_leaves_the_model(self, open_settings, disk):
        disk.cached = [CachedModel("tiny", "Tiny", 78_000_000, True, ("a",))]
        win = open_settings()
        win._choose_delete("tiny")
        win._abort_delete()
        win._confirm_delete()  # nichts mehr vorgemerkt
        assert disk.deleted == []
        assert win.row_storage._control is win.select_delete

    def test_an_unknown_choice_only_refreshes(self, open_settings, disk):
        win = open_settings()
        win._choose_delete("gibt-es-nicht")
        assert win._delete_target == ""


class TestWindow:
    def test_closing_releases_everything(self, open_settings, live_app, hams):
        win = open_settings()
        monitor = hams.streams[-1]
        window = win.win
        win.close()
        live_app.root.update()
        assert monitor.closed
        assert win not in live_app._mic_consumers
        assert live_app.settings_win is None
        assert not window.winfo_exists()
        assert not win.alive()

    def test_a_closed_window_ignores_late_answers(self, open_settings):
        win = open_settings()
        win.close()
        win._apply_ollama_state(ollama_api.OllamaState(True))
        win._refresh_status()
        win._tick()
        win._on_scale_change(1.5)
        win.on_download_progress(1, 2, 50)

    def test_dragging_moves_the_window(self, open_settings, live_app, monkeypatch):
        # wie unter Windows: randlos, gezogen wird am eigenen Kopf
        monkeypatch.setattr(uk, "system_frame", lambda: False)
        monkeypatch.setattr(uk_window, "system_frame", lambda: False)
        win = open_settings()
        win.win.geometry("+100+100")
        live_app.root.update()
        win._start_drag(SimpleNamespace(x=5, y=5))
        win._on_drag(SimpleNamespace(x=25, y=15))
        live_app.root.update()
        assert (win.win.winfo_x(), win.win.winfo_y()) == (120, 110)

    def test_the_header_line_appears_only_when_scrolled(self, open_settings):
        win = open_settings()
        win._on_scroll(0)
        assert win._head_line.cget("bg") == uk.Theme.WINDOW_BG
        win._on_scroll(40)
        assert win._head_line.cget("bg") == uk.Theme.HAIRLINE

    def test_repainting_keeps_the_scroll_position(self, open_settings, live_app):
        win = open_settings()
        win.scroll.scroll_to(60)
        before = win.scroll.offset
        assert win.retint() > 0
        assert win.scroll.offset == before

    def test_focus_brings_it_back(self, open_settings, live_app):
        win = open_settings()
        win.win.withdraw()
        win.focus()
        live_app.root.update()
        assert win.win.state() == "normal"


@pytest.mark.skipif(sys.platform == "win32", reason="nur Linux: unter Windows zeichnet Hams den Rahmen selbst")
class TestSystemFrame:
    def test_on_linux_the_settings_window_is_managed_by_kde(self, open_settings):
        win = open_settings()
        assert uk.system_frame()
        assert not win.win.overrideredirect()
        assert not win.win.attributes("-topmost")
        assert not win._close_btn.winfo_ismapped()

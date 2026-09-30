"""Tests des Modell-Downloads.

Das Netz bleibt hier aussen vor: `snapshot_download` wird ersetzt, damit
Fortschritt, Abbruch und Fehlerwege deterministisch pruefbar sind. Der echte
Durchlauf mit Tiny lief als Integrationstest ausserhalb dieser Suite.
"""

import threading
import time

import pytest

from wisper import models
from wisper.main import FloatingTranscriberApp as App
from wisper.main import SettingsWindow as SW

TINY = models.find("tiny")
LARGE = models.find("large-v3")


@pytest.fixture(autouse=True)
def clean_bar():
    """Die Balkenklasse traegt Zustand — nach jedem Test zuruecksetzen."""
    yield
    models._ProgressBar.cancel_flag = None
    models._ProgressBar.report = None
    if hasattr(models._ProgressBar, "_lock"):
        del models._ProgressBar._lock


# --------------------------------------------------------------- Fortschritt


class TestProgressBar:
    def test_byte_bar_reports(self):
        seen = []
        models._ProgressBar.report = lambda done, total: seen.append((done, total))
        bar = models._ProgressBar(total=100)
        bar.update(30)
        bar.update(20)
        assert seen == [(30, 100), (50, 100)]

    def test_file_bar_does_not_report(self):
        """Der zweite Balken zaehlt Dateien — er darf die Bytes nicht faelschen."""
        seen = []
        models._ProgressBar.report = lambda done, total: seen.append((done, total))
        bar = models._ProgressBar(iter(["a", "b"]), total=2)
        list(bar)
        assert seen == []

    def test_file_bar_consumes_its_iterable(self):
        """Sonst holt niemand die Futures ab und Fehler verschwinden."""
        consumed = list(models._ProgressBar(iter([1, 2, 3]), total=3))
        assert consumed == [1, 2, 3]

    def test_total_never_shrinks(self):
        """Der Hub addiert aus acht Threads ohne Sperre auf `total`."""
        bar = models._ProgressBar(total=0)
        bar.total += 78_000_000
        bar.total = 76_000_000      # veralteter Schreibvorgang
        assert bar.total == 78_000_000

    def test_total_can_grow_from_outside(self):
        """Der Hub meldet jede Datei einzeln an und erhoeht `total` direkt."""
        seen = []
        models._ProgressBar.report = lambda done, total: seen.append((done, total))
        bar = models._ProgressBar(total=0)
        bar.total += 500
        bar.update(100)
        assert seen == [(100, 500)]

    def test_initial_counts_as_progress(self):
        """Bei einer Fortsetzung zaehlen die schon liegenden Bytes mit."""
        bar = models._ProgressBar(total=100, initial=40)
        assert bar.n == 40

    def test_cancel_raises_inside_update(self):
        flag = threading.Event()
        flag.set()
        models._ProgressBar.cancel_flag = flag
        bar = models._ProgressBar(total=10)
        with pytest.raises(models.DownloadCancelled):
            bar.update(1)

    def test_progress_is_monotonic_under_threads(self):
        values = []
        models._ProgressBar.report = lambda done, _t: values.append(done)
        bar = models._ProgressBar(total=8000)
        def worker():
            for _ in range(100):
                bar.update(10)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert bar.n == 8000, "verlorene Zaehlschritte"
        assert values == sorted(values)

    def test_tqdm_lock_protocol(self):
        """`thread_map` verlangt diese beiden Methoden, sonst laedt nichts."""
        lock = models._ProgressBar.get_lock()
        assert lock is not None
        models._ProgressBar.set_lock(lock)
        assert models._ProgressBar._lock is lock

    def test_unused_tqdm_methods_are_harmless(self):
        bar = models._ProgressBar(total=1)
        with bar:
            bar.refresh()
            bar.set_description("x")
            bar.set_postfix(a=1)
            bar.write("x")
        bar.close()


# ------------------------------------------------------------------ Platz


class TestSpace:
    def test_enough_space_is_no_obstacle(self, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        assert models.ModelDownloader().check_space(LARGE) == ""

    def test_too_little_space_is_named(self, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 1024 ** 3)
        message = models.ModelDownloader().check_space(LARGE)
        assert "Speicherplatz" in message and "GB" in message

    def test_margin_is_applied(self, monkeypatch):
        """Genau die Modellgroesse reicht nicht — beim Entpacken braucht es Luft."""
        monkeypatch.setattr(models, "free_space_bytes", lambda: LARGE.size_bytes)
        assert models.ModelDownloader().check_space(LARGE) != ""

    def test_unknown_space_does_not_block(self, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: None)
        assert models.ModelDownloader().check_space(LARGE) == ""

    def test_start_refuses_without_space(self, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 1024)
        downloader = models.ModelDownloader()
        assert "Speicherplatz" in downloader.start(LARGE, None, None)
        assert not downloader.active


# ---------------------------------------------------------------- Downloader


def fake_hub(monkeypatch, behaviour):
    """Ersetzt `snapshot_download`; `behaviour` bekommt die Balkenklasse."""
    import huggingface_hub

    def _download(repo_id, **kwargs):
        return behaviour(kwargs["tqdm_class"])

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _download)


def run_download(monkeypatch, behaviour, downloader=None, info=TINY):
    downloader = downloader or models.ModelDownloader()
    monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
    fake_hub(monkeypatch, behaviour)
    progress, finished = [], []
    downloader.REPORT_INTERVAL = 0.0
    problem = downloader.start(info, lambda d, t: progress.append((d, t)),
                               lambda mid, err: finished.append((mid, err)))
    if not problem:
        downloader._thread.join(timeout=10)
    return downloader, problem, progress, finished


class TestDownloader:
    def test_successful_download(self, monkeypatch):
        def behaviour(bar_class):
            bar = bar_class(total=0, unit="B")
            bar.total += 1000
            bar.update(1000)

        _d, problem, progress, finished = run_download(monkeypatch, behaviour)
        assert problem == ""
        assert progress[-1] == (1000, 1000)
        assert finished == [("tiny", "")]

    def test_allow_patterns_limit_the_download(self, monkeypatch):
        """Ohne diese Liste zieht der Hub das ganze Repository."""
        import huggingface_hub
        seen = {}
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        monkeypatch.setattr(huggingface_hub, "snapshot_download",
                            lambda repo_id, **kw: seen.update(kw, repo=repo_id))
        downloader = models.ModelDownloader()
        downloader.start(TINY, lambda *_a: None, lambda *_a: None)
        downloader._thread.join(timeout=10)
        assert seen["repo"] == TINY.repo_id
        assert "model.bin" in seen["allow_patterns"]
        assert "config.json" in seen["allow_patterns"]

    def test_cancel_ends_the_download(self, monkeypatch):
        downloader = models.ModelDownloader()

        def behaviour(bar_class):
            bar = bar_class(total=10_000, unit="B")
            for _ in range(100):
                bar.update(100)          # bricht mittendrin ab
                time.sleep(0.005)

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        fake_hub(monkeypatch, behaviour)
        finished = []
        started = threading.Event()
        downloader.REPORT_INTERVAL = 0.0
        downloader.start(TINY, lambda d, t: started.set(),
                         lambda mid, err: finished.append((mid, err)))
        assert started.wait(timeout=5)
        downloader.cancel()
        downloader._thread.join(timeout=5)
        assert finished == [("tiny", models.CANCELLED)]
        assert not downloader.active

    def test_cancel_wins_over_a_wrapped_exception(self, monkeypatch):
        """Der Abbruch kommt aus einem Arbeitsthread oft eingepackt zurueck."""
        def behaviour(bar_class):
            raise RuntimeError("A future raised: DownloadCancelled")

        downloader = models.ModelDownloader()
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        fake_hub(monkeypatch, behaviour)
        downloader._cancel = threading.Event()
        downloader._cancel.set()
        finished = []
        downloader._run(TINY, lambda *_a: None, lambda m, e: finished.append((m, e)))
        assert finished == [("tiny", models.CANCELLED)]

    def test_network_error_is_translated(self, monkeypatch):
        def behaviour(_bar_class):
            raise ConnectionError("Failed to resolve huggingface.co")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert finished == [("tiny", "Keine Verbindung zum Modell-Server")]

    def test_write_error_is_translated(self, monkeypatch):
        def behaviour(_bar_class):
            raise PermissionError("Zugriff verweigert")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert finished == [("tiny", "Kein Schreibrecht im Modellordner")]

    def test_full_disk_is_translated(self, monkeypatch):
        def behaviour(_bar_class):
            raise OSError(28, "No space left on device")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert finished == [("tiny", "Kein Speicherplatz mehr frei")]

    def test_only_one_download_at_a_time(self, monkeypatch):
        release = threading.Event()

        def behaviour(_bar_class):
            release.wait(timeout=5)

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        fake_hub(monkeypatch, behaviour)
        downloader = models.ModelDownloader()
        assert downloader.start(TINY, lambda *_a: None, lambda *_a: None) == ""
        second = downloader.start(LARGE, lambda *_a: None, lambda *_a: None)
        assert "bereits" in second
        release.set()
        downloader._thread.join(timeout=5)

    def test_model_id_is_cleared_afterwards(self, monkeypatch):
        downloader, _p, _pr, _f = run_download(monkeypatch, lambda _b: None)
        assert downloader.model_id is None
        assert not downloader.active

    def test_cancel_without_a_download_is_harmless(self):
        models.ModelDownloader().cancel()


class TestErrorTexts:
    @pytest.mark.parametrize("exc,expected", [
        (ConnectionError("connection reset"), "Keine Verbindung zum Modell-Server"),
        (TimeoutError("Read timed out"), "Keine Verbindung zum Modell-Server"),
        (OSError("getaddrinfo failed"), "Keine Verbindung zum Modell-Server"),
        (PermissionError("denied"), "Kein Schreibrecht im Modellordner"),
        (ValueError("404 Client Error"), "Modell nicht gefunden"),
        (RuntimeError("irgendwas"), "Download fehlgeschlagen"),
    ])
    def test_every_text_is_actionable(self, exc, expected):
        assert models._describe_download_error(exc) == expected


class TestFormatBytes:
    @pytest.mark.parametrize("value,expected", [
        (0, "0 B"), (512, "512 B"), (148_000_000, "148 MB"),
        (1_600_000_000, "1,6 GB"),
    ])
    def test_sizes_read_naturally(self, value, expected):
        assert models.format_bytes(value) == expected

    def test_catalog_labels_match_the_measured_sizes(self):
        """Beschriftung und Fortschrittszeile duerfen nicht auseinanderlaufen."""
        for info in models.CATALOG:
            assert models.format_bytes(info.size_bytes) == info.size_label, info.model_id


class TestFormatProgress:
    @pytest.mark.parametrize("done,total,expected", [
        (13_000_000, 78_000_000, "13/78 MB"),
        (800_000_000, 1_600_000_000, "0,8/1,6 GB"),
        (0, 78_000_000, "0/78 MB"),
        (500, 1000, "500/1000 B"),
    ])
    def test_both_numbers_share_a_unit(self, done, total, expected):
        assert models.format_progress(done, total) == expected

    def test_overshoot_is_capped(self):
        """Der Hub meldet gelegentlich mehr als angekuendigt."""
        assert models.format_progress(90_000_000, 78_000_000) == "78/78 MB"


# ------------------------------------------------------------ Anwendungsseite


class _FakeRoot:
    def after(self, _delay, callback=None, *args):
        if callback is not None:
            callback(*args)
        return "job"


@pytest.fixture
def app(monkeypatch):
    instance = App.__new__(App)
    instance.root = _FakeRoot()
    instance.downloader = models.ModelDownloader()
    instance.download_done = 0
    instance.download_total = 0
    instance.download_percent = 0
    instance.settings_win = None
    instance._shutting_down = False
    instance.switched = []
    monkeypatch.setattr(App, "switch_model",
                        lambda self, model_id: self.switched.append(model_id) or True)
    return instance


class TestAppDownload:
    def test_unknown_model_is_refused(self, app):
        assert app.start_download("gibt-es-nicht") == "Unbekanntes Modell"

    def test_start_forwards_the_reason(self, app, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 1024)
        assert "Speicherplatz" in app.start_download("large-v3")

    def test_percent_never_falls_back(self, app):
        """Der Hub meldet neue Dateien nach — ohne Sperre liefe der Wert zurueck."""
        app._on_download_progress(500, 1000)
        assert app.download_percent == 50
        app._on_download_progress(600, 4000)      # neue Datei angemeldet
        assert app.download_percent == 50

    def test_percent_stops_at_99_before_the_end(self, app):
        app._on_download_progress(1000, 1000)
        assert app.download_percent == 99

    def test_success_activates_the_model(self, app):
        app._after_download("tiny", "")
        assert app.switched == ["tiny"]
        assert app.download_percent == 100

    def test_failure_does_not_activate(self, app):
        app._after_download("tiny", "Keine Verbindung zum Modell-Server")
        assert app.switched == []

    def test_cancel_does_not_activate(self, app):
        app._after_download("tiny", models.CANCELLED)
        assert app.switched == []

    def test_shutdown_swallows_late_callbacks(self, app):
        """Der Download-Thread lebt laenger als das Fenster."""
        app._shutting_down = True
        calls = []
        app._post(lambda: calls.append(1))
        assert calls == []

    def test_downloading_model_reflects_the_service(self, app, monkeypatch):
        assert app.downloading_model is None
        release = threading.Event()
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024 ** 3)
        fake_hub(monkeypatch, lambda _b: release.wait(timeout=5))
        app.start_download("tiny")
        assert app.downloading_model == "tiny"
        release.set()
        app.downloader._thread.join(timeout=5)
        assert app.downloading_model is None

    def test_dead_settings_window_is_not_touched(self, app):
        class _Dead:
            def alive(self):
                return False

            def on_download_finished(self, *_a):
                raise AssertionError("Zugriff auf ein geschlossenes Fenster")

        app.settings_win = _Dead()
        app._after_download("tiny", "")


# --------------------------------------------------------- Einstellungsfenster


class _StubRow:
    def __init__(self):
        self.description = ""
        self.control = None

    def set_description(self, text):
        self.description = text

    def set_control(self, widget):
        self.control = widget


class _StubWindow:
    """Die Download-Logik des Einstellungsfensters ohne Tk."""

    _begin_download = SW._begin_download
    _cancel_download = SW._cancel_download
    _attach_download = SW._attach_download
    _update_model_hint = SW._update_model_hint
    on_download_progress = SW.on_download_progress
    on_download_finished = SW.on_download_finished
    download_text = staticmethod(SW.download_text)
    model_options = SW.model_options
    MODEL_HINT = SW.MODEL_HINT

    def _refresh_storage(self):
        self.storage_refreshed += 1

    def __init__(self, problem="", running=None):
        self._installed_models = {"turbo"}
        self._downloading = ""
        #: Wie oft die Bestandszeile neu aufgebaut wurde. Sie sperrt die
        #: Loeschauswahl waehrend eines Downloads, also muss sie an beiden
        #: Enden mitbekommen, dass sich etwas geaendert hat.
        self.storage_refreshed = 0
        self.row_model = _StubRow()
        self.selected = None
        self.select_enabled = True
        self.cancel_enabled = True
        window = self

        class _Select:
            def set_value(self, value):
                window.selected = value

            def set_options(self, options):
                window.options = options

            def set_enabled(self, value):
                window.select_enabled = value

        class _Button:
            def set_enabled(self, value):
                window.cancel_enabled = value

        class _App:
            active_model_size = "turbo"
            downloading_model = running
            download_done = 4_000_000
            download_total = 10_000_000
            download_percent = 40

            def __init__(self):
                self.started = []
                self.cancelled = []

            def start_download(self, model_id):
                self.started.append(model_id)
                return problem

            def cancel_download(self):
                self.cancelled.append(1)

        self.app = _App()
        self.model_select = _Select()
        self.cancel_button = _Button()

    def alive(self):
        return True


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr("wisper.main.CFG.model_size", "turbo")


class TestSettingsDownload:
    def test_choosing_an_uninstalled_model_starts_the_download(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        assert window.app.started == ["tiny"]
        assert window._downloading == "tiny"

    def test_the_selection_stays_on_the_working_model(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        assert window.selected == "turbo"

    def test_the_row_offers_a_cancel(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        assert window.row_model.control is window.cancel_button

    def test_a_refused_start_is_explained(self, config):
        window = _StubWindow(problem="Es läuft bereits ein Download")
        window._begin_download("tiny")
        assert window.row_model.description == "Es läuft bereits ein Download"
        assert window.row_model.control is None
        assert window._downloading == ""

    def test_progress_names_share_and_amount(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_progress(39_000_000, 78_000_000, 49)
        text = window.row_model.description
        assert "Tiny" in text and "49 %" in text and "39/78 MB" in text

    def test_progress_without_a_known_total(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_progress(1024 ** 2, 0, 0)
        assert "geladen" in window.row_model.description

    def test_progress_is_ignored_without_a_download(self, config):
        window = _StubWindow()
        window.on_download_progress(1, 2, 50)
        assert window.row_model.description == ""

    def test_the_hint_does_not_overwrite_the_progress(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_progress(10, 100, 10)
        before = window.row_model.description
        window._update_model_hint()
        assert window.row_model.description == before

    def test_cancel_asks_the_app_and_says_so(self, config):
        window = _StubWindow()
        window._begin_download("tiny")
        window._cancel_download()
        assert window.app.cancelled == [1]
        assert "abgebrochen" in window.row_model.description.lower()
        assert window.cancel_enabled is False

    def test_success_keeps_the_select_locked_while_loading(self, config, monkeypatch):
        monkeypatch.setattr(models, "installed_whisper_models", lambda: {"turbo", "tiny"})
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_finished("tiny", "", True)
        assert window.row_model.control is window.model_select
        assert window.select_enabled is False
        assert "wird geladen" in window.row_model.description

    def test_cancel_restores_the_select(self, config, monkeypatch):
        monkeypatch.setattr(models, "installed_whisper_models", lambda: {"turbo"})
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_finished("tiny", models.CANCELLED, False)
        assert window.select_enabled is True
        assert window.cancel_enabled is True
        assert "abgebrochen" in window.row_model.description.lower()
        assert window._downloading == ""

    def test_an_error_offers_a_retry(self, config, monkeypatch):
        monkeypatch.setattr(models, "installed_whisper_models", lambda: {"turbo"})
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_finished("tiny", "Keine Verbindung zum Modell-Server", False)
        assert "erneut" in window.row_model.description
        assert window.select_enabled is True

    def test_a_finished_download_appears_as_installed(self, config, monkeypatch):
        monkeypatch.setattr(models, "installed_whisper_models", lambda: {"turbo", "tiny"})
        window = _StubWindow()
        window._begin_download("tiny")
        window.on_download_finished("tiny", "", True)
        tiny = next(o for o in window.options if o.value == "tiny")
        assert tiny.group == "Installiert" and tiny.trailing == ""

    def test_a_reopened_window_shows_the_running_download(self, config):
        """Der Download gehoert der Anwendung — das Fenster haengt sich nur an."""
        window = _StubWindow(running="tiny")
        window._attach_download()
        assert window._downloading == "tiny"
        assert window.row_model.control is window.cancel_button
        assert "40 %" in window.row_model.description

    def test_a_fresh_window_attaches_to_nothing(self, config):
        window = _StubWindow()
        window._attach_download()
        assert window._downloading == ""
        assert window.row_model.control is None


class TestTransport:
    def test_xet_is_switched_off(self):
        """Ueber Xet greift der Abbruch erst nach Minuten und ohne Fortsetzung."""
        from huggingface_hub import constants, utils

        assert models.disable_xet_transfer() is True
        assert constants.HF_HUB_DISABLE_XET is True
        assert utils._runtime.is_xet_available() is False

    def test_the_download_switches_it_off_itself(self, monkeypatch):
        from huggingface_hub import constants

        monkeypatch.setattr(constants, "HF_HUB_DISABLE_XET", False)
        run_download(monkeypatch, lambda _bar: None)
        assert constants.HF_HUB_DISABLE_XET is True

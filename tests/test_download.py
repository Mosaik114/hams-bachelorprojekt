"""Tests des Modell-Downloads.

Das Netz bleibt hier aussen vor: `snapshot_download` wird ersetzt, damit
Fortschritt, Abbruch und Fehlerwege deterministisch pruefbar sind. Der echte
Durchlauf mit Tiny lief als Integrationstest ausserhalb dieser Suite.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from setup_fakes import InlineThread

from hams import hub_progress, models, settings_models
from hams.config import CFG

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
        bar.total = 76_000_000  # veralteter Schreibvorgang
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
        with pytest.raises(hub_progress.DownloadCancelled):
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
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        assert models.ModelDownloader().check_space(LARGE) == ""

    def test_too_little_space_is_named(self, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 1024**3)
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


def fake_hub(monkeypatch, behaviour, snapshot=None):
    """Ersetzt `snapshot_download`; `behaviour` bekommt die Balkenklasse.

    Zurueck kommt wie beim echten Hub der Snapshot-Ordner; ohne `snapshot` einer mit der Modelldatei.
    """
    import tempfile

    import huggingface_hub

    if snapshot is None:
        snapshot = Path(tempfile.mkdtemp(prefix="hams-snapshot-"))
        (snapshot / models.MODEL_FILE).write_bytes(b"model")

    def _download(repo_id, **kwargs):
        behaviour(kwargs["tqdm_class"])
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _download)


def run_download(monkeypatch, behaviour, downloader=None, info=TINY):
    downloader = downloader or models.ModelDownloader()
    monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
    fake_hub(monkeypatch, behaviour)
    progress, finished = [], []
    downloader.REPORT_INTERVAL = 0.0
    problem = downloader.start(info, lambda d, t: progress.append((d, t)), lambda mid, err: finished.append((mid, err)))
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

    def test_a_download_without_the_model_file_is_a_failure(self, monkeypatch, tmp_path):
        """Success means installed: a snapshot without model.bin must not count as done."""
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        fake_hub(monkeypatch, lambda bar_class: None, snapshot=tmp_path)
        finished = []
        downloader = models.ModelDownloader()
        downloader.start(TINY, lambda d, t: None, lambda mid, err: finished.append((mid, err)))
        downloader._thread.join(timeout=10)
        assert finished == [("tiny", "Der Download ist unvollständig – bitte erneut versuchen")]

    def test_allow_patterns_limit_the_download(self, monkeypatch):
        """Ohne diese Liste zieht der Hub das ganze Repository."""
        import huggingface_hub

        seen = {}
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda repo_id, **kw: seen.update(kw, repo=repo_id))
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
                bar.update(100)  # bricht mittendrin ab
                time.sleep(0.005)

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        fake_hub(monkeypatch, behaviour)
        finished = []
        started = threading.Event()
        downloader.REPORT_INTERVAL = 0.0
        downloader.start(TINY, lambda d, t: started.set(), lambda mid, err: finished.append((mid, err)))
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
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
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
        assert finished == [("tiny", f"{NETWORK} (ConnectionError)")]

    def test_write_error_is_translated(self, monkeypatch):
        def behaviour(_bar_class):
            raise PermissionError("Zugriff verweigert")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert finished == [("tiny", f"{PERMISSION} (PermissionError)")]

    def test_full_disk_is_translated(self, monkeypatch):
        def behaviour(_bar_class):
            raise OSError(28, "No space left on device")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert finished == [("tiny", "Die Festplatte ist voll. Etwas Platz freimachen und nochmal versuchen.")]

    def test_only_one_download_at_a_time(self, monkeypatch):
        release = threading.Event()

        def behaviour(_bar_class):
            release.wait(timeout=5)

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
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


NETWORK = "Keine Internetverbindung. Internet prüfen und nochmal versuchen."
GENERIC = "Der Download hat nicht geklappt. Bitte nochmal versuchen."
PERMISSION = "Hams darf hier nicht speichern. Bitte Hams neu starten und nochmal versuchen."
DISK_FULL = "Die Festplatte ist voll. Etwas Platz freimachen und nochmal versuchen."
BLOCKED = (
    "Ein Schutzprogramm oder das Netzwerk blockiert den Download. Bitte ein anderes WLAN probieren, etwa den "
    "Handy-Hotspot."
)


class HubError(Exception):
    """Like `HfHubHTTPError`: the status sits on `response`."""

    def __init__(self, status, message="Client error"):
        super().__init__(f"{message} for url 'https://cdn.example/resolve/abc'")
        self.response = SimpleNamespace(status_code=status)


class TestErrorTexts:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (ConnectionError("connection reset"), f"{NETWORK} (ConnectionError)"),
            (TimeoutError("Read timed out"), f"{NETWORK} (TimeoutError)"),
            (OSError("getaddrinfo failed"), f"{NETWORK} (OSError)"),
            (PermissionError("denied"), f"{PERMISSION} (PermissionError)"),
            (OSError(28, "No space left on device"), DISK_FULL),  # says it all: no number
            (
                ConnectionError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate"),
                f"{BLOCKED} (ConnectionError)",
            ),
            (ConnectionError("Unable to connect to proxy"), f"{BLOCKED} (ConnectionError)"),
            (HubError(407), f"{BLOCKED} (407)"),
            (ValueError("404 Client Error"), "Dieses Modell gibt es gerade nicht. Bitte ein anderes wählen. (404)"),
            (RuntimeError("irgendwas"), f"{GENERIC} (RuntimeError)"),
            (HubError(416, "Client error '416 Range Not Satisfiable'"), f"{GENERIC} (416)"),
            (
                HubError(429),
                "Gerade laden sehr viele gleichzeitig. In ein paar Minuten nochmal versuchen. (429)",
            ),
            (HubError(503), "Die Download-Seite hat gerade eine Störung. Später nochmal versuchen. (503)"),
        ],
    )
    def test_every_text_says_what_to_do(self, exc, expected):
        assert models._describe_download_error(exc) == expected

    def test_the_status_is_read_from_the_message_too(self):
        exc = RuntimeError("Client error '416 Range Not Satisfiable' for url 'https://cdn.example/x'")
        assert models.http_status(exc) == 416

    def test_an_url_with_resolve_is_not_a_network_error(self):
        """Hub URLs contain "/resolve/"; with a status the server answered, so the connection worked."""
        assert models.error_kind(HubError(416)) == "generic"

    def test_errno_numbers_are_no_status(self):
        assert models.http_status(OSError("[Errno 11001] getaddrinfo failed")) is None

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (HubError(416), True),
            (RuntimeError("irgendwas"), True),
            (ConnectionError("connection reset"), False),
            (PermissionError("denied"), False),
            (OSError(28, "No space left on device"), False),
            (HubError(429), False),
            (HubError(502), False),
            (HubError(404), False),
        ],
    )
    def test_only_unexplained_failures_blame_a_leftover(self, exc, expected):
        assert models.leftover_can_cause(exc) is expected

    @staticmethod
    def wrapped(inner: BaseException, outer: BaseException) -> BaseException:
        """`outer` raised from `inner`, the way the hub wraps the real cause."""
        try:
            try:
                raise inner
            except BaseException as cause:
                raise outer from cause
        except BaseException as exc:  # noqa: BLE001 - the test wants the chained exception
            return exc

    def test_a_certificate_problem_behind_the_hubs_wrapper_is_named(self):
        """Offline-looking on the outside ("check your connection"), an antivirus certificate on the inside."""
        import ssl

        inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer")
        outer = RuntimeError("LocalEntryNotFoundError: cannot find the requested files. Check your connection.")
        exc = self.wrapped(inner, outer)
        assert models.error_kind(exc) == "blocked"
        assert models.describe_download_error(exc) == f"{BLOCKED} (SSLCertVerificationError)"

    def test_offline_the_innermost_cause_is_the_code(self):
        exc = self.wrapped(
            OSError("[Errno 11001] getaddrinfo failed"), RuntimeError("entry not found, check connection")
        )
        assert models.error_kind(exc) == "network"  # not "not found": the connection is the problem
        assert models.describe_download_error(exc) == f"{NETWORK} (OSError)"

    def test_a_dropped_tls_connection_is_network_trouble_not_a_block(self):
        """ "ssl" alone (an EOF in _ssl.c) passes by itself; only a certificate or a proxy blocks."""
        exc = ConnectionError("EOF occurred in violation of protocol (_ssl.c:2427)")
        assert models.error_kind(exc) == "network"

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (ConnectionError("connection reset"), True),
            (HubError(429), True),
            (HubError(503), True),
            (HubError(416), False),
            (HubError(404), False),
            (ConnectionError("CERTIFICATE_VERIFY_FAILED"), False),
            (PermissionError("denied"), False),
            (OSError(28, "No space left on device"), False),
        ],
    )
    def test_only_passing_trouble_is_tried_again_by_itself(self, exc, expected):
        assert models.passes_by_itself(exc) is expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            (f"{GENERIC} (416)", (GENERIC, "416")),
            (f"{NETWORK} (ConnectionError)", (NETWORK, "ConnectionError")),
            (DISK_FULL, (DISK_FULL, "")),
            ("Zu wenig Speicherplatz — 2 GB nötig, 1 GB frei", ("Zu wenig Speicherplatz — 2 GB nötig, 1 GB frei", "")),
            ("‫فشل التنزيل ‫(416)", ("‫فشل التنزيل", "416")),
            ("", ("", "")),
        ],
    )
    def test_the_code_comes_apart_from_the_sentence(self, text, expected):
        assert models.split_code(text) == expected


# ------------------------------------------------------------- new attempts


@pytest.fixture
def quick_retries(monkeypatch):
    """Two new attempts that wait one short tick each, instead of 5 and 20 seconds."""
    monkeypatch.setattr(models.ModelDownloader, "RETRY_DELAYS", (2, 3))
    monkeypatch.setattr(models.ModelDownloader, "WAIT_TICK", 0.001)


def run_with_wait(monkeypatch, behaviour, downloader=None):
    """Like `run_download`, plus the countdown the downloader reports."""
    downloader = downloader or models.ModelDownloader()
    monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
    fake_hub(monkeypatch, behaviour)
    waits, finished = [], []
    problem = downloader.start(TINY, lambda *_a: None, lambda m, e: finished.append((m, e)), waits.append)
    assert problem == ""
    downloader._thread.join(timeout=10)
    return downloader, waits, finished


class TestNewAttempts:
    def test_a_dropped_connection_is_tried_again_with_a_countdown(self, monkeypatch, quick_retries):
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("connection reset")

        _d, waits, finished = run_with_wait(monkeypatch, behaviour)
        assert len(calls) == 2
        assert waits == [2, 1, 0]  # every second, then 0: progress again
        assert finished == [("tiny", "")]

    def test_after_two_new_attempts_the_user_hears_of_it(self, monkeypatch, quick_retries):
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise ConnectionError("connection reset")

        _d, waits, finished = run_with_wait(monkeypatch, behaviour)
        assert len(calls) == 3
        assert waits == [2, 1, 0, 3, 2, 1, 0]
        assert finished == [("tiny", f"{NETWORK} (ConnectionError)")]

    def test_a_busy_server_is_tried_again_too(self, monkeypatch, quick_retries):
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            if len(calls) < 3:
                raise HubError(429)

        _d, _waits, finished = run_with_wait(monkeypatch, behaviour)
        assert len(calls) == 3
        assert finished == [("tiny", "")]

    def test_a_blocked_connection_is_not_tried_again(self, monkeypatch, quick_retries):
        """An antivirus does not change its mind in 20 seconds: say so at once."""
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise ConnectionError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in chain")

        _d, waits, finished = run_with_wait(monkeypatch, behaviour)
        assert len(calls) == 1
        assert waits == []
        assert finished == [("tiny", f"{BLOCKED} (ConnectionError)")]

    def test_cancel_during_the_countdown_ends_it(self, monkeypatch):
        monkeypatch.setattr(models.ModelDownloader, "RETRY_DELAYS", (30,))
        monkeypatch.setattr(models.ModelDownloader, "WAIT_TICK", 0.05)
        downloader = models.ModelDownloader()
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise ConnectionError("connection reset")

        def on_wait(seconds):
            if seconds == 29:
                downloader.cancel()

        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        fake_hub(monkeypatch, behaviour)
        finished = []
        downloader.start(TINY, lambda *_a: None, lambda m, e: finished.append((m, e)), on_wait)
        downloader._thread.join(timeout=10)
        assert finished == [("tiny", models.CANCELLED)]
        assert len(calls) == 1

    def test_the_manager_counts_down_for_the_windows(self, monkeypatch):
        posted = []
        manager = SimpleNamespace(retry_in=0, _post=posted.append, events=SimpleNamespace(download_progress="tick"))
        from hams.whisper_model import ModelManager

        ModelManager._on_download_wait(manager, 5)
        assert manager.retry_in == 5
        ModelManager._on_download_wait(manager, 0)
        assert manager.retry_in == 0
        assert posted == ["tick", "tick"]


# --------------------------------------------------------- system certificates


class TestSystemCertificates:
    def test_the_hub_gets_a_client_with_the_system_store(self, monkeypatch):
        import truststore
        from huggingface_hub.utils import _http as hub_http

        assert models.use_system_certificates() is True
        client = hub_http._GLOBAL_CLIENT_FACTORY()
        try:
            verify = client._transport._pool._ssl_context
            assert isinstance(verify, truststore.SSLContext)
            assert client.follow_redirects is True
            assert hub_http.hf_request_event_hook in client.event_hooks["request"]
        finally:
            client.close()

    def test_it_is_set_once(self, monkeypatch):
        calls = []
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "set_client_factory", calls.append)
        models.use_system_certificates()
        models.use_system_certificates()
        assert len(calls) == 1

    def test_without_truststore_certifi_stays(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_truststore(name, *args, **kwargs):
            if name == "truststore":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        calls = []
        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "set_client_factory", calls.append)
        monkeypatch.setattr(builtins, "__import__", no_truststore)
        assert models.use_system_certificates() is False
        assert calls == []


# ---------------------------------------------------------------- self-repair


@pytest.fixture
def hub_cache(monkeypatch, tmp_path):
    """A model cache of our own, with the blobs folder of Tiny."""
    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    blobs = tmp_path / "models--Systran--faster-whisper-tiny" / "blobs"
    blobs.mkdir(parents=True)
    return blobs


class TestSelfRepair:
    def test_a_broken_leftover_is_removed_and_the_download_runs_again(self, monkeypatch, hub_cache):
        piece = hub_cache / "abc.incomplete"
        piece.write_bytes(b"x" * 100)
        (hub_cache / "finished").write_bytes(b"keep")
        calls = []

        def behaviour(_bar_class):
            calls.append(piece.exists())
            if piece.exists():
                raise HubError(416, "Client error '416 Range Not Satisfiable'")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert calls == [True, False]  # second attempt without the leftover
        assert finished == [("tiny", "")]
        assert (hub_cache / "finished").exists()  # finished files stay

    def test_it_tries_again_only_once(self, monkeypatch, hub_cache):
        (hub_cache / "abc.incomplete").write_bytes(b"x")
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise HubError(416)

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert len(calls) == 2
        assert finished == [("tiny", f"{GENERIC} (416)")]

    def test_without_a_leftover_there_is_no_second_attempt(self, monkeypatch, hub_cache):
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise RuntimeError("irgendwas")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert len(calls) == 1
        assert finished == [("tiny", f"{GENERIC} (RuntimeError)")]

    def test_network_trouble_keeps_the_leftover_for_resuming(self, monkeypatch, hub_cache):
        piece = hub_cache / "abc.incomplete"
        piece.write_bytes(b"x" * 100)
        calls = []

        def behaviour(_bar_class):
            calls.append(1)
            raise ConnectionError("connection reset")

        _d, _p, _pr, finished = run_download(monkeypatch, behaviour)
        assert len(calls) == 1
        assert piece.exists()
        assert finished == [("tiny", f"{NETWORK} (ConnectionError)")]

    def test_try_again_after_such_a_failure_starts_clean(self, monkeypatch, hub_cache):
        """The leftover appeared after the repair (written by the failed second attempt): the next start clears it."""
        seen = []

        def failing(_bar_class):
            (hub_cache / "late.incomplete").write_bytes(b"x")
            raise HubError(416)

        downloader, _p, _pr, finished = run_download(monkeypatch, failing)
        assert finished[-1][1] == f"{GENERIC} (416)"
        assert (hub_cache / "late.incomplete").exists()

        def succeeding(_bar_class):
            seen.append((hub_cache / "late.incomplete").exists())

        _d, _p, _pr, finished = run_download(monkeypatch, succeeding, downloader=downloader)
        assert seen == [False]
        assert finished == [("tiny", "")]

    def test_a_cancel_never_removes_anything(self, monkeypatch, hub_cache):
        piece = hub_cache / "abc.incomplete"
        piece.write_bytes(b"x")

        def behaviour(_bar_class):
            raise RuntimeError("A future raised: DownloadCancelled")

        downloader = models.ModelDownloader()
        fake_hub(monkeypatch, behaviour)
        downloader._cancel = threading.Event()
        downloader._cancel.set()
        finished = []
        downloader._run(TINY, lambda *_a: None, lambda m, e: finished.append((m, e)))
        assert finished == [("tiny", models.CANCELLED)]
        assert piece.exists()

    def test_only_this_models_pieces_are_removed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
        mine = tmp_path / "models--Systran--faster-whisper-tiny" / "blobs"
        other = tmp_path / "models--Systran--faster-whisper-small" / "blobs"
        for folder in (mine, other):
            folder.mkdir(parents=True)
            (folder / "p.incomplete").write_bytes(b"x")
        assert models.remove_partial_downloads(TINY) == 1
        assert not (mine / "p.incomplete").exists()
        assert (other / "p.incomplete").exists()

    def test_no_cache_folder_is_no_problem(self, monkeypatch, tmp_path):
        monkeypatch.setattr(models, "cache_root", lambda: tmp_path / "missing")
        assert models.remove_partial_downloads(TINY) == 0


class TestFormatBytes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0 B"),
            (512, "512 B"),
            (148_000_000, "148 MB"),
            (1_600_000_000, "1,6 GB"),
        ],
    )
    def test_sizes_read_naturally(self, value, expected):
        assert models.format_bytes(value) == expected

    def test_catalog_labels_match_the_measured_sizes(self):
        """Beschriftung und Fortschrittszeile duerfen nicht auseinanderlaufen."""
        for info in models.CATALOG:
            assert models.format_bytes(info.size_bytes) == info.size_label, info.model_id


class TestFormatProgress:
    @pytest.mark.parametrize(
        "done,total,expected",
        [
            (13_000_000, 78_000_000, "13/78 MB"),
            (800_000_000, 1_600_000_000, "0,8/1,6 GB"),
            (0, 78_000_000, "0/78 MB"),
            (500, 1000, "500/1000 B"),
        ],
    )
    def test_both_numbers_share_a_unit(self, done, total, expected):
        assert models.format_progress(done, total) == expected

    def test_overshoot_is_capped(self):
        """Der Hub meldet gelegentlich mehr als angekuendigt."""
        assert models.format_progress(90_000_000, 78_000_000) == "78/78 MB"


# ------------------------------------------------------------ Anwendungsseite


@pytest.fixture
def app(live_app, monkeypatch):
    """The live app; download results come back at once, switches are only recorded."""
    live_app.models._ui_call = lambda fn: fn()
    live_app.switched = []
    monkeypatch.setattr(live_app, "switch_model", lambda model_id: live_app.switched.append(model_id) or True)
    return live_app


class TestAppDownload:
    def test_unknown_model_is_refused(self, app):
        assert app.models.start_download("gibt-es-nicht") == "Unbekanntes Modell"

    def test_start_forwards_the_reason(self, app, monkeypatch):
        monkeypatch.setattr(models, "free_space_bytes", lambda: 1024)
        assert "Speicherplatz" in app.models.start_download("large-v3")

    def test_percent_never_falls_back(self, app):
        """Der Hub meldet neue Dateien nach — ohne Sperre liefe der Wert zurueck."""
        app.models._on_download_progress(500, 1000)
        assert app.models.download_percent == 50
        app.models._on_download_progress(600, 4000)  # neue Datei angemeldet
        assert app.models.download_percent == 50

    def test_percent_stops_at_99_before_the_end(self, app):
        app.models._on_download_progress(1000, 1000)
        assert app.models.download_percent == 99

    def test_success_activates_the_model(self, app):
        app.models._on_download_finished("tiny", "")
        assert app.switched == ["tiny"]
        assert app.models.download_percent == 100

    def test_failure_does_not_activate(self, app):
        app.models._on_download_finished("tiny", "Keine Verbindung zum Modell-Server")
        assert app.switched == []

    def test_cancel_does_not_activate(self, app):
        app.models._on_download_finished("tiny", models.CANCELLED)
        assert app.switched == []

    def test_shutdown_swallows_late_callbacks(self, app):
        """Der Download-Thread lebt laenger als das Fenster."""
        app.models.close()
        calls = []
        app.models._post(lambda: calls.append(1))
        assert calls == []

    def test_downloading_model_reflects_the_service(self, app, monkeypatch):
        assert app.models.downloading is None
        release = threading.Event()
        monkeypatch.setattr(models, "free_space_bytes", lambda: 500 * 1024**3)
        fake_hub(monkeypatch, lambda _b: release.wait(timeout=5))
        app.models.start_download("tiny")
        assert app.models.downloading == "tiny"
        release.set()
        app.models.downloader._thread.join(timeout=5)
        assert app.models.downloading is None

    def test_a_closed_settings_window_is_not_touched(self, app, monkeypatch):
        """Geschlossen heisst nur noch versteckt: das Fenster bleibt da, bekommt aber keine Meldungen mehr."""
        monkeypatch.setattr(settings_models, "threading", SimpleNamespace(Thread=InlineThread))
        app._open_settings()
        app.root.update()
        win = app.settings_win
        win.close()

        def touched(*_args):
            raise AssertionError("Zugriff auf ein geschlossenes Fenster")

        monkeypatch.setattr(win, "on_download_finished", touched)
        monkeypatch.setattr(win, "on_download_progress", touched)
        app.download_progress()
        app.download_finished("tiny", "")
        assert app.settings_win is win and not win.alive()


# --------------------------------------------------------- Einstellungsfenster


class FakeDownloader:
    """`ModelDownloader` ohne Netz: merkt sich Start und Abbruch, den Rest spielt der Test."""

    def __init__(self, problem=""):
        self.problem = problem
        self.model_id, self.active = None, False
        self.started, self.cancelled = [], []

    def start(self, info, on_progress, on_finished, on_wait=None):
        if self.problem:
            return self.problem
        self.started.append(info.model_id)
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.cancelled.append(self.model_id)
        self.active = False


@pytest.fixture
def settings(live_app, monkeypatch):
    """Die Gruppe "Transkription" des echten Einstellungsfensters.

    Turbo ist installiert, gewaehlt und geladen; der Download laeuft ueber den echten `ModelManager`, nur ohne Netz.
    `settings.open()` oeffnet das Fenster wie ein Klick aufs Zahnrad und liefert die Gruppe.
    """
    disk = SimpleNamespace(installed={"turbo"})
    monkeypatch.setattr(models, "installed_whisper_models", lambda: set(disk.installed))
    monkeypatch.setattr(models, "cached_whisper_models", lambda: ())
    monkeypatch.setattr(settings_models, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(CFG, "model_size", "turbo")
    live_app.models.active_model_size = "turbo"
    live_app.models.downloader = FakeDownloader()

    def _open():
        live_app._open_settings()
        live_app.root.update()
        return live_app.settings_win.transcription

    return SimpleNamespace(app=live_app, disk=disk, open=_open)


def description(section) -> str:
    return section.row_model.sub_text


class TestSettingsDownload:
    def test_choosing_an_uninstalled_model_starts_the_download(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        assert settings.app.models.downloader.started == ["tiny"]
        assert tr.downloading == "tiny"

    def test_the_selection_stays_on_the_working_model(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        assert tr.model_select.value == "turbo"

    def test_the_row_offers_a_cancel(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        assert tr.row_model._control is tr.cancel_button

    def test_a_refused_start_is_explained(self, settings):
        settings.app.models.downloader = FakeDownloader(problem="Es läuft bereits ein Download")
        tr = settings.open()
        tr.begin_download("tiny")
        assert description(tr) == "Es läuft bereits ein Download"
        assert tr.row_model._control is tr.model_select
        assert tr.downloading == ""

    def test_progress_names_share_and_amount(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.on_download_progress(39_000_000, 78_000_000, 49)
        text = description(tr)
        assert "Tiny" in text and "49 %" in text and "39/78 MB" in text

    def test_a_new_attempt_counts_down_instead_of_the_progress(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        settings.app.models.retry_in = 5
        tr.on_download_progress(39_000_000, 78_000_000, 49)
        assert description(tr) == "Neuer Versuch in 5 s …"
        settings.app.models.retry_in = 0
        tr.on_download_progress(39_000_000, 78_000_000, 49)
        assert "49 %" in description(tr)

    def test_progress_without_a_known_total(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.on_download_progress(1024**2, 0, 0)
        assert "geladen" in description(tr)

    def test_progress_is_ignored_without_a_download(self, settings):
        tr = settings.open()
        before = description(tr)
        tr.on_download_progress(1, 2, 50)
        assert description(tr) == before

    def test_the_hint_does_not_overwrite_the_progress(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.on_download_progress(10, 100, 10)
        before = description(tr)
        tr.update_model_hint()
        assert description(tr) == before

    def test_cancel_asks_the_app_and_says_so(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.cancel_download()
        assert settings.app.models.downloader.cancelled == ["tiny"]
        assert "abgebrochen" in description(tr).lower()
        assert tr.cancel_button.enabled is False

    def test_success_keeps_the_select_locked_while_loading(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        settings.disk.installed.add("tiny")
        tr.on_download_finished("tiny", "", True)
        assert tr.row_model._control is tr.model_select
        assert tr.model_select.enabled is False
        assert "wird geladen" in description(tr)

    def test_cancel_restores_the_select(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.on_download_finished("tiny", models.CANCELLED, False)
        assert tr.model_select.enabled is True
        assert tr.cancel_button.enabled is True
        assert "abgebrochen" in description(tr).lower()
        assert tr.downloading == ""

    def test_an_error_offers_a_retry(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        tr.on_download_finished("tiny", "Keine Verbindung zum Modell-Server", False)
        assert "erneut" in description(tr)
        assert tr.model_select.enabled is True

    def test_a_finished_download_appears_as_installed(self, settings):
        tr = settings.open()
        tr.begin_download("tiny")
        settings.disk.installed.add("tiny")
        tr.on_download_finished("tiny", "", True)
        tiny = next(o for o in tr.model_select.options if o.value == "tiny")
        assert tiny.group == "Installiert" and tiny.trailing == ""

    def test_a_reopened_window_shows_the_running_download(self, settings):
        """Der Download gehoert der Anwendung — das Fenster haengt sich nur an."""
        settings.open()
        settings.app.settings_win.close()
        manager = settings.app.models
        assert manager.start_download("tiny") == ""
        manager.download_done, manager.download_total, manager.download_percent = 4_000_000, 10_000_000, 40
        tr = settings.open()
        assert tr.downloading == "tiny"
        assert tr.row_model._control is tr.cancel_button
        assert "40 %" in description(tr)

    def test_a_fresh_window_attaches_to_nothing(self, settings):
        tr = settings.open()
        assert tr.downloading == ""
        assert tr.row_model._control is tr.model_select


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


class _Downloader:
    def __init__(self):
        self.model_id, self.active, self.started = None, False, []

    def start(self, info, on_progress, on_finished, on_wait=None):
        self.started.append(info.model_id)
        self.model_id, self.active = info.model_id, True
        return ""

    def cancel(self):
        self.active = False


class TestALateDownloadNeverOverridesANewerChoice:
    @pytest.fixture
    def app(self, live_app, model_cache_check, monkeypatch, hams):

        live_app.models.downloader = _Downloader()
        live_app.loaded = []

        def run_now(model_id, generation, persist=True):
            live_app.loaded.append(model_id)
            live_app.models._switch_worker(model_id, generation, persist)

        monkeypatch.setattr(live_app.models, "_start_switch_thread", run_now)
        monkeypatch.setattr(live_app.models, "_build_model", lambda model_id: (object(), "cpu", "int8"))
        monkeypatch.setattr(live_app, "_load_model_async", lambda: hams.load_model(live_app))
        CFG.model_size = "small"
        return live_app

    def settle(self, app):
        app._ui_dispatcher.drain()
        app.root.update()

    def test_download_a_switch_to_b_a_finishes_b_stays(self, app, hams):
        app._load_model_async()  # "small" isn't cached: it is downloaded first
        assert app.models.load_after_download == "small"
        assert app.switch_model("base")
        self.settle(app)
        assert app.models.load_after_download is None
        assert (app.models.active_model_size, CFG.model_size) == ("base", "base")
        app.models.downloader.active = False
        app.models._on_download_finished("small", "")
        self.settle(app)
        assert app.loaded == ["base"], "the finished download of small loads nothing"
        assert (app.models.active_model_size, CFG.model_size) == ("base", "base")
        assert ("model", "size", "small") not in hams.saved

    def test_a_download_from_the_settings_still_switches_when_nothing_else_was_chosen(self, app):
        app.models.start_download("base")
        app.models.downloader.active = False
        app.models._on_download_finished("base", "")
        self.settle(app)
        assert app.loaded == ["base"]
        assert app.models.active_model_size == "base"

    def test_the_awaited_download_does_not_draw_over_a_recording(self, app):
        app._load_model_async()
        app.toggle_recording()
        app.models.downloader.active = False
        app.models._on_download_finished("small", "")
        self.settle(app)
        assert app.loaded == ["small"], "the recording waits for this model: load it"
        assert app._state == "recording"


# ------------------------------------------------------------- load from disk


class TestLoadFromDisk:
    """An installed model loads without asking the model server: behind a broken proxy it loads all the same."""

    @pytest.fixture
    def tried(self, monkeypatch):
        from hams import whisper_model

        calls = []

        class FakeModel:
            fail_local = False

            def __init__(self, model_id, device, compute_type, **options):
                calls.append(options.get("local_files_only", False))
                if options.get("local_files_only") and FakeModel.fail_local:
                    raise OSError("snapshot folder missing")

        monkeypatch.setattr(whisper_model, "WhisperModel", FakeModel)
        monkeypatch.setattr(whisper_model, "cuda_libraries_found", lambda: False)
        monkeypatch.setattr(CFG, "model_device_priority", ["cpu"])
        return SimpleNamespace(calls=calls, model=FakeModel)

    def test_an_installed_model_loads_from_the_disk_alone(self, tried):
        from hams import whisper_model

        manager = SimpleNamespace(installed_models=lambda: {"tiny"})
        model, device, _info = whisper_model.ModelManager._build_model(manager, "tiny")
        assert model is not None and device == "cpu"
        assert tried.calls == [True]

    def test_a_model_not_yet_installed_asks_the_server(self, tried):
        from hams import whisper_model

        manager = SimpleNamespace(installed_models=lambda: set())
        whisper_model.ModelManager._build_model(manager, "tiny")
        assert tried.calls == [False]

    def test_if_the_disk_alone_fails_the_normal_way_follows_once(self, tried):
        from hams import whisper_model

        tried.model.fail_local = True
        manager = SimpleNamespace(installed_models=lambda: {"tiny"})
        model, _device, _info = whisper_model.ModelManager._build_model(manager, "tiny")
        assert model is not None
        assert tried.calls == [True, True, False]  # int8 and float32 from the disk, then the normal way

    def test_not_knowing_what_is_installed_means_the_normal_way(self, tried):
        from hams import whisper_model

        def broken():
            raise OSError("cache unreadable")

        whisper_model.ModelManager._build_model(SimpleNamespace(installed_models=broken), "tiny")
        assert tried.calls == [False]

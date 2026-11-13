"""Der Einrichtungsassistent ohne Fenster: Seitenfolge, Hardware, Empfehlung, Downloads, Markierung.

Alles, was den Rechner beruehrt, kommt aus `fake_services`; kein Test fragt ein Netz, eine GPU
oder Ollama.
"""

import errno
import json
import urllib.error

import pytest
from setup_fakes import InlineThread, fake_services, online

from hams import ollama as ollama_api
from hams import setup_wizard as sw
from hams.ollama import OllamaState


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    monkeypatch.setattr(sw, "TaskThread", InlineThread)


def controller(**overrides) -> sw.SetupController:
    choices = sw.Choices(ui_language="auto", dictation_language="de", model="turbo", cleanup_model="qwen3.5:9b")
    return sw.SetupController(fake_services(**overrides), choices)


def immediate(callback) -> None:
    """`post` ohne Tk: der Rueckruf laeuft sofort."""
    callback()


# ---------------------------------------------------------------- hardware and recommendation


class TestHardware:
    @pytest.mark.parametrize(
        ("present", "found", "supported", "expected"),
        [
            (False, True, True, sw.CPU_ONLY),
            (True, True, True, sw.GPU_READY),
            (True, False, True, sw.GPU_DOWNLOAD),
            (True, False, False, sw.GPU_MANUAL),
        ],
    )
    def test_verdict(self, present, found, supported, expected):
        assert sw.detect_hardware(present, found, supported) == expected

    @pytest.mark.parametrize(
        ("hardware", "model"),
        [(sw.GPU_READY, "turbo"), (sw.GPU_DOWNLOAD, "small"), (sw.GPU_MANUAL, "small"), (sw.CPU_ONLY, "small")],
    )
    def test_without_a_usable_gpu_the_smaller_model_is_suggested(self, hardware, model):
        assert sw.recommended_model(hardware) == model

    def test_both_recommendations_are_in_the_catalog(self):
        from hams import models

        assert models.find(sw.GPU_MODEL) and models.find(sw.CPU_MODEL)

    def test_controller_asks_the_services(self):
        wizard = controller(gpu_present=lambda: True, gpu_libraries_found=lambda: True)
        assert wizard.detect_hardware() == sw.GPU_READY


# ---------------------------------------------------------------- marker


class TestMarker:
    def test_round_trip(self, tmp_path):
        path = sw.marker_path(tmp_path / "config.toml")
        assert path == tmp_path / sw.MARKER_NAME
        assert sw.read_marker(path) is None
        assert sw.write_marker(path, False)
        assert sw.read_marker(path) is False
        sw.write_marker(path, True)
        assert sw.read_marker(path) is True

    @pytest.mark.parametrize("content", ["kaputt", "[]", '{"completed": "ja"}'])
    def test_unreadable_marker_counts_as_missing(self, tmp_path, content):
        path = tmp_path / sw.MARKER_NAME
        path.write_text(content, encoding="utf-8")
        assert sw.read_marker(path) is None

    def test_write_failure_is_reported_not_raised(self, tmp_path):
        blocker = tmp_path / "datei"
        blocker.write_text("x", encoding="utf-8")
        assert not sw.write_marker(blocker / "unter" / sw.MARKER_NAME, True)

    def test_fresh_configuration_gets_the_wizard(self, tmp_path):
        assert sw.needs_setup("created", tmp_path / sw.MARKER_NAME)

    @pytest.mark.parametrize("state", ["existing", "migrated"])
    def test_existing_installation_is_marked_as_set_up(self, tmp_path, state):
        path = tmp_path / sw.MARKER_NAME
        assert not sw.needs_setup(state, path)
        assert sw.read_marker(path) is True

    def test_unwritable_configuration_gets_no_wizard(self, tmp_path):
        assert not sw.needs_setup("failed", tmp_path / sw.MARKER_NAME)

    def test_unfinished_setup_comes_back(self, tmp_path):
        path = tmp_path / sw.MARKER_NAME
        sw.write_marker(path, False)
        assert sw.needs_setup("existing", path)
        sw.write_marker(path, True)
        assert not sw.needs_setup("created", path)


# ---------------------------------------------------------------- tasks


class TestTask:
    def test_lifecycle(self):
        task = sw.Task()
        assert task.state == sw.IDLE and task.percent == 0
        task.begin(200)
        assert task.running
        task.advance(50, 0)
        assert (task.done, task.total, task.percent) == (50, 200, 25)
        task.advance(200, 200)
        assert task.percent == 99, "100 % erst, wenn es wirklich fertig ist"
        task.succeed()
        assert task.percent == 100
        task.advance(1, 1)
        assert task.done == 200, "nach dem Ende aendert Fortschritt nichts mehr"

    def test_failure_and_cancel(self):
        task = sw.Task()
        task.begin()
        task.cancel()
        assert task.cancel_event.is_set()
        task.cancelled()
        assert task.state == sw.CANCELLED
        task.begin()
        assert not task.cancel_event.is_set(), "ein neuer Versuch beginnt ohne Abbruchwunsch"
        task.fail("Netz weg")
        assert (task.state, task.error) == (sw.FAILED, "Netz weg")


class TestTaskView:
    def view(self, task):
        return sw.task_view(task, idle="bereit", done="fertig")

    def test_idle_offers_the_download(self):
        assert self.view(sw.Task()) == sw.TaskView("idle", "bereit", "start", None)

    def test_running_shows_progress_and_cancel(self, english):
        task = sw.Task()
        task.begin()
        assert self.view(task).text == "Starting …"
        task.advance(500_000_000, 1_000_000_000)
        view = self.view(task)
        assert (view.status, view.action, view.fraction) == ("processing", "cancel", 0.5)
        assert view.text == "50% · 0.5/1.0 GB"
        task.advance(10, 40, "extract")
        assert self.view(task).text == "Unpacking · 25%"

    def test_failure_is_a_sentence_with_retry(self):
        task = sw.Task()
        task.fail("Der Server ist nicht erreichbar")
        assert self.view(task) == sw.TaskView("error", "Der Server ist nicht erreichbar", "retry", None)

    def test_done_and_cancelled(self, english):
        task = sw.Task()
        task.succeed()
        assert self.view(task) == sw.TaskView("ready", "fertig", None, None)
        task.cancelled()
        view = self.view(task)
        assert (view.status, view.action) == ("warning", "start")
        assert "continues" in view.text


class TestRunTask:
    def test_success_reports_progress_once_per_percent(self):
        task = sw.Task()
        posted = []

        def work(progress, cancelled):
            for done in (0, 1, 2, 50, 51, 100):
                progress(done, 100)
            assert cancelled() is False

        def post(callback):
            posted.append(callback)
            callback()

        sw.run_task(task, work, post, str)
        assert task.state == sw.DONE
        assert len(posted) == 7  # sechs verschiedene Prozente, dann der Erfolg

    def test_failure_becomes_a_sentence(self):
        task = sw.Task()

        def work(progress, cancelled):
            raise OSError(errno.ENOSPC, "No space left on device")

        sw.run_task(task, work, immediate, lambda exc: "Platte voll")
        assert (task.state, task.error) == (sw.FAILED, "Platte voll")

    def test_cancel_wins_over_the_error(self):
        task = sw.Task()

        def work(progress, cancelled):
            task.cancel()
            raise RuntimeError("abgebrochen")

        sw.run_task(task, work, immediate, str)
        assert task.state == sw.CANCELLED


class TestDescribeError:
    def test_network(self, english):
        error = sw.describe_error(urllib.error.URLError("getaddrinfo failed"))
        assert error == "Can't reach the model server"

    def test_disk_full(self, english):
        assert sw.describe_error(OSError(errno.ENOSPC, "voll")) == "The disk is full"

    def test_ollama_message_is_kept(self):
        assert sw.describe_error(ollama_api.PullError("Ollama sagt nein")) == "Ollama sagt nein"

    def test_unknown_error_has_no_traceback(self, english):
        assert sw.describe_error(ValueError("x" * 500)) == "Download failed"

    def test_a_wrapped_gpu_error_keeps_its_cause(self, english):
        from hams import gpu_support

        try:
            try:
                raise PermissionError(13, "Access is denied")
            except OSError as cause:
                raise gpu_support.GpuSupportError("PermissionError: Access is denied") from cause
        except gpu_support.GpuSupportError as wrapped:
            assert sw.describe_error(wrapped) == "No write access to the model folder"

    def test_a_running_gpu_install_elsewhere(self, english):
        from hams import gpu_support

        assert sw.describe_error(gpu_support.InstallBusy("busy")) == "Another Hams is already downloading this"


# ---------------------------------------------------------------- navigation


class TestNavigation:
    def test_walks_all_pages_in_order(self):
        wizard = controller()
        seen = [wizard.page]
        while not wizard.is_last:
            seen.append(wizard.next())
        assert tuple(seen) == sw.PAGES
        assert wizard.next() == "finish", "hinter der letzten Seite kommt nichts"
        assert wizard.count == len(sw.PAGES)

    def test_back_stops_at_the_first_page(self):
        wizard = controller()
        assert not wizard.can_back()
        wizard.next()
        assert wizard.can_back()
        assert wizard.back() == "welcome"
        assert wizard.back() == "welcome"

    def test_only_optional_pages_can_be_skipped(self):
        wizard = controller()
        skippable = []
        for page in sw.PAGES:
            wizard.index = sw.PAGES.index(page)
            if wizard.can_skip():
                skippable.append(page)
        assert skippable == ["cleanup"], "nur dort tut Überspringen etwas anderes als Weiter"

    def test_model_page_needs_a_model(self):
        wizard = controller()
        wizard.index = sw.PAGES.index("model")
        assert not wizard.can_next(model_ready=False)
        assert wizard.can_next(model_ready=True)

    def test_running_gpu_download_holds_the_page(self):
        wizard = controller()
        wizard.index = sw.PAGES.index("hardware")
        wizard.gpu_task.begin()
        assert wizard.busy()
        assert not (wizard.can_next(True) or wizard.can_back() or wizard.can_skip())

    def test_running_pull_holds_the_cleanup_page(self):
        wizard = controller()
        wizard.index = sw.PAGES.index("cleanup")
        wizard.pull_task.begin()
        assert not wizard.can_next(True)

    def test_skipping_cleanup_switches_it_off(self):
        wizard = controller()
        wizard.choices.cleanup = True
        wizard.index = sw.PAGES.index("cleanup")
        assert wizard.skip() == "finish"
        assert wizard.choices.cleanup is False


# ---------------------------------------------------------------- model


class TestModel:
    def enter_model_page(self, wizard):
        wizard.index = sw.PAGES.index("hardware")
        return wizard.next()

    def test_cpu_gets_small_preselected(self):
        wizard = controller()
        self.enter_model_page(wizard)
        assert wizard.choices.model == "small"
        assert not wizard.model_installed()

    def test_gpu_keeps_turbo(self):
        wizard = controller(gpu_present=lambda: True, gpu_libraries_found=lambda: True)
        wizard.detect_hardware()
        self.enter_model_page(wizard)
        assert wizard.choices.model == "turbo"

    def test_an_installed_choice_is_not_replaced(self):
        wizard = controller()
        wizard.services.state.installed = {"turbo"}
        self.enter_model_page(wizard)
        assert wizard.choices.model == "turbo"
        assert wizard.model_installed()

    def test_a_second_visit_keeps_the_users_choice_but_rereads_the_disk(self):
        wizard = controller()
        self.enter_model_page(wizard)
        wizard.choices.model = "base"
        wizard.services.state.installed = {"base"}
        wizard.back()
        wizard.next()
        assert wizard.choices.model == "base"
        assert wizard.model_installed()


# ---------------------------------------------------------------- GPU download


class TestGpuDownload:
    def test_success_makes_the_gpu_ready(self):
        found = {"libs": False}

        def install(progress, cancelled):
            progress(10, 100, "download")
            found["libs"] = True

        wizard = controller(gpu_present=lambda: True, gpu_libraries_found=lambda: found["libs"], install_gpu=install)
        wizard.detect_hardware()
        assert wizard.hardware == sw.GPU_DOWNLOAD
        assert wizard.start_gpu_download(immediate) == ""
        assert wizard.gpu_task.state == sw.DONE
        wizard.after_gpu_download()
        assert wizard.hardware == sw.GPU_READY
        wizard.index = sw.PAGES.index("hardware")
        wizard.next()
        assert wizard.choices.model == "turbo", "nach dem GPU-Download wird neu empfohlen"

    def test_not_enough_space_fails_before_downloading(self, english):
        started = []
        wizard = controller(free_space=lambda: 1_000_000_000, install_gpu=lambda *a: started.append(a))
        problem = wizard.start_gpu_download(immediate)
        assert problem.startswith("Not enough disk space")
        assert wizard.gpu_task.state == sw.FAILED and wizard.gpu_task.error == problem
        assert started == []

    def test_unknown_free_space_does_not_block(self):
        wizard = controller(free_space=lambda: None)
        assert wizard.gpu_space_problem() == ""

    def test_network_failure_is_human_readable(self, english):
        def install(progress, cancelled):
            raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")

        wizard = controller(install_gpu=install)
        wizard.start_gpu_download(immediate)
        assert wizard.gpu_task.error == "Can't reach the model server"

    def test_a_second_start_while_running_does_nothing(self):
        wizard = controller()
        wizard.gpu_task.begin()
        assert wizard.start_gpu_download(immediate) == ""
        assert wizard.gpu_task.running

    def test_install_registers_the_new_libraries(self, monkeypatch):
        from hams import backend_preload, gpu_support

        calls = []

        def install(progress, cancel):
            progress("download", 5, 10)
            calls.append(cancel())

        monkeypatch.setattr(gpu_support, "install", install)
        monkeypatch.setattr(backend_preload, "add_cuda_libraries", lambda: calls.append("registered"))
        reported = []
        sw._install_gpu(lambda done, total, stage: reported.append((stage, done, total)), lambda: False)
        assert reported == [("download", 5, 10)]
        assert calls == [False, "registered"]


# ---------------------------------------------------------------- microphone


class TestMicrophone:
    def test_speech_level_counts_once(self):
        wizard = controller()
        assert not wizard.note_level(0.01)
        assert wizard.note_level(0.2)
        assert wizard.note_level(0.0), "einmal gehoert bleibt gehoert"


# ---------------------------------------------------------------- Ollama


class TestCleanup:
    def test_offline_means_cleanup_off(self):
        wizard = controller()
        wizard.choices.cleanup = True
        wizard.set_ollama_state(OllamaState(False, error="aus"))
        assert not wizard.ollama_online
        assert wizard.choices.cleanup is False
        wizard.set_cleanup(True)
        assert wizard.choices.cleanup is False, "ohne Dienst laesst sie sich nicht einschalten"

    def test_online_with_the_model_switches_it_on(self):
        wizard = controller()
        wizard.set_ollama_state(online("qwen3.5:9b"))
        assert wizard.cleanup_model_installed() and wizard.choices.cleanup
        wizard.set_cleanup(False)
        assert wizard.choices.cleanup is False

    def test_online_without_the_model_offers_the_pull(self):
        wizard = controller(probe_ollama=lambda: online("qwen3.5:9b"))
        wizard.set_ollama_state(online("llama3.2:3b"))
        assert not wizard.cleanup_available()
        wizard.start_pull(immediate)
        assert wizard.services.state.pulls == ["qwen3.5:9b"]
        assert wizard.pull_task.state == sw.DONE
        wizard.after_pull()
        assert wizard.choices.cleanup is True

    def test_no_pull_without_ollama(self):
        wizard = controller()
        wizard.start_pull(immediate)
        assert wizard.pull_task.state == sw.IDLE

    def test_failed_pull_keeps_the_message(self):
        def pull(name, progress, cancelled):
            raise ollama_api.PullError("Ollama konnte das Modell nicht laden: manifest unknown")

        wizard = controller(pull_ollama=pull)
        wizard.set_ollama_state(online())
        wizard.start_pull(immediate)
        assert wizard.pull_task.error.endswith("manifest unknown")


# ---------------------------------------------------------------- finish


class TestFinish:
    def test_writes_cleanup_and_marker(self, tmp_path):
        wizard = controller()
        wizard.set_ollama_state(online("qwen3.5:9b"))
        marker = tmp_path / sw.MARKER_NAME
        assert wizard.finish(marker) == [("ollama", "cleanup_enabled", True)]
        assert wizard.services.state.persisted == [("ollama", "cleanup_enabled", True)]
        assert json.loads(marker.read_text(encoding="utf-8")) == {"completed": True}

    def test_never_switches_cleanup_on_without_ollama(self, tmp_path):
        wizard = controller()
        wizard.choices.cleanup = True  # aus der Konfiguration, Ollama nie gefragt
        assert wizard.finish(tmp_path / sw.MARKER_NAME) == [("ollama", "cleanup_enabled", False)]


# ---------------------------------------------------------------- helpers


class TestHelpers:
    def test_free_space_walks_up_to_an_existing_folder(self, tmp_path):
        assert sw._free_space_at(tmp_path / "gibt" / "es" / "nicht") > 0

    def test_free_space_error_is_unknown(self, monkeypatch, tmp_path):
        def broken(_path):
            raise OSError("nein")

        monkeypatch.setattr(sw.shutil, "disk_usage", broken)
        assert sw._free_space_at(tmp_path) is None

    def test_linux_driver_check_reads_proc(self, monkeypatch):
        monkeypatch.setattr(sw.Path, "exists", lambda self: str(self).replace("\\", "/").endswith("nvidia/version"))
        assert sw._linux_nvidia_present()

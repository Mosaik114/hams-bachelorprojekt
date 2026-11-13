"""First-run setup: everything the wizard decides, without a single Tk widget.

The window (`setup_window.py`) only draws what `SetupController` says and forwards
clicks. Navigation, the hardware verdict, the model recommendation, the state of the
three downloads (GPU libraries, Whisper model, Ollama model) and what gets written at
the end live here, so they can be tested without a display, a network or a GPU.

Everything that touches the machine comes in through `SetupServices`. The defaults
(`default_services`) use the real modules; tests pass fakes.

The completed setup is remembered in a small marker file next to the configuration
(`setup.json`). Installations that existed before the wizard have a configuration but
no marker; they are treated as set up, so an update never greets a long-time user with
a first-run assistant.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hams.i18n import t

logger = logging.getLogger("hams")

# ----------------------------------------------------------------- pages

PAGES = ("welcome", "hardware", "model", "microphone", "cleanup", "finish")
#: Pages with a "Skip" button. The model page has none: without a model there is no dictation.
SKIPPABLE = frozenset({"hardware", "microphone", "cleanup"})

# ----------------------------------------------------------------- hardware

GPU_READY = "gpu_ready"  #: NVIDIA card and the CUDA libraries are there
GPU_DOWNLOAD = "gpu_download"  #: NVIDIA card, libraries missing, the app can fetch them
GPU_MANUAL = "gpu_manual"  #: NVIDIA card, libraries missing, only pip can install them (source install)
CPU_ONLY = "cpu"  #: no NVIDIA card

#: The model for a GPU: fast and very accurate.
GPU_MODEL = "turbo"
#: The model for the CPU. Measured with int8 on a current desktop CPU: Small transcribes 12-15 s of
#: speech in about 1 s, Turbo needs about 3 s, Base about 0.3 s (less accurate).
CPU_MODEL = "small"


def detect_hardware(gpu_present: bool, libraries_found: bool, download_supported: bool) -> str:
    """One of the four verdicts above."""
    if not gpu_present:
        return CPU_ONLY
    if libraries_found:
        return GPU_READY
    return GPU_DOWNLOAD if download_supported else GPU_MANUAL


def recommended_model(hardware: str) -> str:
    return GPU_MODEL if hardware == GPU_READY else CPU_MODEL


# ----------------------------------------------------------------- marker

MARKER_NAME = "setup.json"
#: `config.ensure_user_config()` results that mean "this configuration existed before".
EXISTING_CONFIG = ("vorhanden", "migriert")


def marker_path(config_path: Path) -> Path:
    return Path(config_path).parent / MARKER_NAME


def read_marker(path: Path) -> bool | None:
    """True/False from the marker; None if there is none (or it cannot be read)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("completed"), bool):
        return None
    return data["completed"]


def write_marker(path: Path, completed: bool) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"completed": completed}), encoding="utf-8")
    except OSError:
        logger.warning("Setup marker %s could not be written", path, exc_info=True)
        return False
    return True


def needs_setup(config_state: str, path: Path) -> bool:
    """Whether the wizard opens at this start.

    Without a marker, a configuration that already existed means an installation from
    before the wizard: it is marked as set up instead. A configuration that could not be
    created ("fehlgeschlagen") gets no wizard either, nothing it chose could be saved.
    """
    completed = read_marker(path)
    if completed is not None:
        return not completed
    if config_state in EXISTING_CONFIG:
        write_marker(path, True)
        return False
    return config_state == "angelegt"


# ----------------------------------------------------------------- tasks

IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


class Task:
    """One download as the window shows it: state, bytes, and a sentence when it failed."""

    def __init__(self) -> None:
        self.state = IDLE
        self.done = 0
        self.total = 0
        self.stage = ""
        self.error = ""
        self.cancel_event = threading.Event()

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    @property
    def percent(self) -> int:
        """0..100; stays at 99 until the task really succeeded."""
        if self.state == DONE:
            return 100
        if self.total <= 0:
            return 0
        return max(0, min(99, self.done * 100 // self.total))

    def begin(self, total: int = 0) -> None:
        self.state = RUNNING
        self.done, self.total, self.stage, self.error = 0, total, "", ""
        self.cancel_event = threading.Event()

    def advance(self, done: int, total: int, stage: str = "") -> None:
        if self.state != RUNNING:
            return
        self.done, self.stage = done, stage
        self.total = total or self.total

    def succeed(self) -> None:
        self.state = DONE
        self.error = ""

    def fail(self, message: str) -> None:
        self.state = FAILED
        self.error = message

    def cancelled(self) -> None:
        self.state = CANCELLED
        self.error = ""

    def cancel(self) -> None:
        self.cancel_event.set()


@dataclass(frozen=True)
class TaskView:
    """How a task row looks: status dot, text, the button next to it, and the bar (None = hidden)."""

    status: str
    text: str
    action: str | None  #: "start", "retry", "cancel" or None
    fraction: float | None


def running_text(task: Task) -> str:
    from hams.models import format_progress

    if task.total <= 0:
        return t("setup.task.starting")
    if task.stage == "extract":
        return t("setup.task.unpacking", percent=task.percent)
    return t("setup.task.progress", percent=task.percent, progress=format_progress(task.done, task.total))


def task_view(task: Task, *, idle: str, done: str) -> TaskView:
    """The row for a task: errors are sentences with a retry button, never a traceback."""
    if task.state == RUNNING:
        return TaskView("processing", running_text(task), "cancel", task.percent / 100)
    if task.state == DONE:
        return TaskView("ready", done, None, None)
    if task.state == FAILED:
        return TaskView("error", task.error, "retry", None)
    if task.state == CANCELLED:
        return TaskView("warning", t("setup.task.cancelled_resume"), "start", None)
    return TaskView("idle", idle, "start", None)


Post = Callable[[Callable[[], None]], None]


#: Thread class for the background work; the tests run it inline.
TaskThread = threading.Thread


def run_task(task: Task, work, post: Post, describe: Callable[[BaseException], str]) -> None:
    """Runs ``work(progress, cancelled)`` in a background thread.

    Every change reaches `task` through `post`, i.e. on the Tk thread. Progress is only
    posted when the percentage or the stage changes: the GPU download reports every
    megabyte, a few thousand times.
    """
    task.begin()
    last = {"key": None}

    def progress(done: int, total: int, stage: str = "") -> None:
        key = (stage, done * 100 // total if total else -1)
        if key != last["key"]:
            last["key"] = key
            post(lambda: task.advance(done, total, stage))

    def body() -> None:
        try:
            work(progress, task.cancel_event.is_set)
        except Exception as exc:  # noqa: BLE001 - becomes a sentence for the window
            if task.cancel_event.is_set():
                post(task.cancelled)
            else:
                logger.warning("Setup task failed: %s", type(exc).__name__, exc_info=True)
                message = describe(exc)
                post(lambda: task.fail(message))
            return
        post(task.succeed)

    TaskThread(target=body, daemon=True, name="hams-setup-task").start()


# ----------------------------------------------------------------- services


@dataclass
class SetupServices:
    """What the wizard needs from the machine. Tests replace any of these."""

    gpu_present: Callable[[], bool]
    gpu_libraries_found: Callable[[], bool]
    gpu_download_supported: Callable[[], bool]
    install_gpu: Callable  #: (progress(stage, done, total), cancelled()) -> None
    gpu_download_bytes: int
    gpu_space_needed: int
    free_space: Callable[[], int | None]
    installed_models: Callable[[], set]
    probe_ollama: Callable  #: () -> ollama.OllamaState
    pull_ollama: Callable  #: (name, progress(done, total), cancelled()) -> None
    persist: Callable  #: (section, key, value) -> bool
    open_url: Callable[[str], object]


def _linux_nvidia_present() -> bool:
    return Path("/proc/driver/nvidia/version").exists()


def _free_space_at(folder: Path) -> int | None:
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _install_gpu(progress, cancelled) -> None:
    from hams import backend_preload, gpu_support

    gpu_support.install(lambda stage, done, total: progress(done, total, stage), cancelled)
    # The backend was preloaded at start without them; register the new folder now so the
    # next model load can use the GPU without a restart.
    backend_preload.add_cuda_libraries()


def default_services() -> SetupServices:  # pragma: no cover - wiring of the real modules, used by the app
    import webbrowser

    from hams import app_paths, backend_preload, gpu_support
    from hams import models as whisper_models
    from hams import ollama as ollama_api
    from hams.config import CFG, _persist_config_value

    download = sum(wheel.size for wheel in gpu_support.CUDA_WHEELS)

    def base() -> str:
        return ollama_api.base_url(CFG.ollama_url)

    return SetupServices(
        gpu_present=lambda: gpu_support.nvidia_gpu_present() or (sys.platform != "win32" and _linux_nvidia_present()),
        gpu_libraries_found=backend_preload.cuda_libraries_found,
        gpu_download_supported=gpu_support.is_supported,
        install_gpu=_install_gpu,
        gpu_download_bytes=download,
        # wheels and unpacked DLLs exist side by side until the wheels are deleted
        gpu_space_needed=int(download * 2.5),
        free_space=lambda: _free_space_at(app_paths.local_data_dir()),
        installed_models=whisper_models.installed_whisper_models,
        probe_ollama=lambda: ollama_api.probe(base()),
        pull_ollama=lambda name, progress, cancelled: ollama_api.pull(base(), name, progress, cancelled),
        persist=_persist_config_value,
        open_url=webbrowser.open,
    )


def describe_error(exc: BaseException) -> str:
    """A sentence for a failed GPU or Ollama download; the traceback stays in the log."""
    from hams import models as whisper_models
    from hams import ollama as ollama_api

    if isinstance(exc, ollama_api.PullError):
        return str(exc)
    return whisper_models.describe_download_error(exc)


# ----------------------------------------------------------------- controller


@dataclass
class Choices:
    """What the user picked. Starts from the current configuration."""

    ui_language: str
    dictation_language: str
    model: str
    cleanup_model: str
    cleanup: bool = False


class SetupController:
    """Page order, the hardware verdict and the state of every download."""

    def __init__(self, services: SetupServices, choices: Choices) -> None:
        self.services = services
        self.choices = choices
        self.index = 0
        self.hardware = CPU_ONLY
        self.gpu_task = Task()
        self.pull_task = Task()
        self.ollama = None  #: last `OllamaState`, None until asked
        self.mic_heard = False
        self.model_suggested = False
        #: Installed Whisper models, read once per visit of the model page (scanning the cache costs time).
        self.installed: set = set()

    # ------------------------------------------------------- navigation

    @property
    def page(self) -> str:
        return PAGES[self.index]

    @property
    def count(self) -> int:
        return len(PAGES)

    @property
    def is_last(self) -> bool:
        return self.index == len(PAGES) - 1

    def can_back(self) -> bool:
        return self.index > 0 and not self.busy()

    def can_skip(self) -> bool:
        return self.page in SKIPPABLE and not self.busy()

    def busy(self) -> bool:
        """A download the current page owns is running; leaving would hide its progress."""
        return (self.page == "hardware" and self.gpu_task.running) or (
            self.page == "cleanup" and self.pull_task.running
        )

    def can_next(self, model_ready: bool) -> bool:
        """`model_ready`: the chosen model is installed or its download is running in the app."""
        if self.busy():
            return False
        return model_ready if self.page == "model" else True

    def next(self) -> str:
        if self.index < len(PAGES) - 1:
            self.index += 1
        self._entered()
        return self.page

    def back(self) -> str:
        if self.index > 0:
            self.index -= 1
        self._entered()
        return self.page

    def skip(self) -> str:
        if self.page == "cleanup":
            self.choices.cleanup = False
        return self.next()

    def _entered(self) -> None:
        if self.page != "model":
            return
        if self.model_suggested:
            self.refresh_installed()
        else:
            self.suggest_model()

    # ------------------------------------------------------- hardware and model

    def detect_hardware(self) -> str:
        services = self.services
        self.hardware = detect_hardware(
            services.gpu_present(), services.gpu_libraries_found(), services.gpu_download_supported()
        )
        logger.info("Setup: hardware %s", self.hardware)
        return self.hardware

    def gpu_space_problem(self) -> str:
        """Empty, or why the GPU download would not fit on the disk."""
        from hams.models import format_bytes

        free = self.services.free_space()
        needed = self.services.gpu_space_needed
        if free is None or free >= needed:
            return ""
        return t("download.no_space", needed=format_bytes(needed), free=format_bytes(free))

    def start_gpu_download(self, post: Post) -> str:
        """Starts the GPU download; returns why not, or ""."""
        problem = self.gpu_space_problem()
        if problem:
            self.gpu_task.fail(problem)
            return problem
        if self.gpu_task.running:
            return ""
        run_task(self.gpu_task, self.services.install_gpu, post, describe_error)
        return ""

    def after_gpu_download(self) -> None:
        """Called when the GPU task succeeded: the GPU is usable now."""
        self.detect_hardware()
        self.model_suggested = False

    def refresh_installed(self) -> set:
        self.installed = set(self.services.installed_models())
        return self.installed

    def suggest_model(self) -> str:
        """Preselects the model for this hardware, unless the chosen one is already installed."""
        if self.choices.model not in self.refresh_installed():
            self.choices.model = recommended_model(self.hardware)
        self.model_suggested = True
        return self.choices.model

    def model_installed(self) -> bool:
        return self.choices.model in self.installed

    # ------------------------------------------------------- microphone

    #: The settings window calls this level "good signal".
    HEARD_LEVEL = 0.08

    def note_level(self, level: float) -> bool:
        """True once speech-loud input arrived on the microphone page."""
        if level >= self.HEARD_LEVEL:
            self.mic_heard = True
        return self.mic_heard

    # ------------------------------------------------------- Ollama

    def set_ollama_state(self, state) -> None:
        self.ollama = state
        self.choices.cleanup = self.cleanup_available()

    @property
    def ollama_online(self) -> bool:
        return bool(self.ollama is not None and self.ollama.online)

    def cleanup_model_installed(self) -> bool:
        return self.ollama_online and self.choices.cleanup_model in self.ollama.names

    def cleanup_available(self) -> bool:
        return self.cleanup_model_installed()

    def set_cleanup(self, value: bool) -> None:
        self.choices.cleanup = bool(value) and self.cleanup_available()

    def start_pull(self, post: Post) -> None:
        if self.pull_task.running or not self.ollama_online:
            return
        name = self.choices.cleanup_model

        def work(progress, cancelled) -> None:
            self.services.pull_ollama(name, progress, cancelled)

        run_task(self.pull_task, work, post, describe_error)

    def after_pull(self) -> None:
        """Ask again: the model should now be on the list."""
        self.set_ollama_state(self.services.probe_ollama())

    # ------------------------------------------------------- finish

    def finish(self, marker: Path) -> list:
        """Writes the cleanup choice and the marker. Returns what was persisted.

        Cleanup is only switched on when Ollama answered and has the model; otherwise every
        dictation would first try a service that is not there.
        """
        cleanup = self.choices.cleanup and self.cleanup_available()
        written = [("ollama", "cleanup_enabled", cleanup)]
        for section, key, value in written:
            self.services.persist(section, key, value)
        write_marker(marker, True)
        logger.info("Setup completed (cleanup %s, model %s)", "on" if cleanup else "off", self.choices.model)
        return written

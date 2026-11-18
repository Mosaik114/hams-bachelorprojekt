"""What the start and the idle release measure, and the start budget.

The log now says how long the start took from the moment the process began (`process_clock`), and how much graphics
memory Hams holds itself (`gpu_memory`, DXGI). Both are only figures for the log and must never break anything.

The start budget runs a real Hams as a test instance, off unless asked for (`set HAMS_PERF=1`) like the other time
budgets, and only with a model cache to load from (`set HAMS_BUDGET_HF_CACHE=<folder with the turbo model>`): it never
downloads. It fails when the start grows clearly beyond what was measured for 1.3.7 on the development PC
(RTX 5070, warm disk cache): hotkeys ready after about 0.6 s, the model ready after about 4-5 s.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hams import gpu_memory, process_clock

PROJECT_ROOT = Path(__file__).parent.parent

#: Seconds after the process began; about twice what was measured, so only a clear growth fails.
HOTKEYS_BUDGET = 1.5
MODEL_READY_BUDGET = 10.0


class TestProcessClock:
    def test_counts_from_the_process_start(self):
        first = process_clock.seconds_since_start()
        time.sleep(0.05)
        second = process_clock.seconds_since_start()
        assert 0 < first < second
        assert second - first >= 0.04

    def test_falls_back_when_the_system_does_not_say(self, monkeypatch):
        monkeypatch.setattr(process_clock, "_windows", lambda: None)
        monkeypatch.setattr(process_clock, "_linux", lambda: None)
        assert process_clock.seconds_since_start() >= 0

    def test_never_raises(self, monkeypatch):
        def broken():
            raise OSError("no")

        monkeypatch.setattr(process_clock, "_windows", broken)
        monkeypatch.setattr(process_clock, "_linux", broken)
        assert process_clock.seconds_since_start() >= 0


class TestGraphicsMemory:
    def test_a_figure_or_unknown(self):
        text = gpu_memory.describe()
        assert text == "unknown" or re.fullmatch(r"\d+ MiB", text)

    @pytest.mark.skipif(sys.platform != "win32", reason="DXGI is Windows")
    def test_the_adapters_are_listed(self):
        for name, dedicated, usage in gpu_memory.adapters():
            assert isinstance(name, str) and dedicated >= 0 and usage >= 0

    def test_the_adapter_with_the_most_memory_counts(self, monkeypatch):
        monkeypatch.setattr(
            gpu_memory, "adapters", lambda: [("iGPU", 512 * 2**20, 10 * 2**20), ("dGPU", 12 * 2**30, 2048 * 2**20)]
        )
        assert gpu_memory.process_usage_mib() == 2048
        assert gpu_memory.describe() == "2048 MiB"

    def test_nothing_known_is_unknown(self, monkeypatch):
        monkeypatch.setattr(gpu_memory, "adapters", lambda: [])
        assert gpu_memory.describe() == "unknown"

    def test_a_failure_is_unknown(self, monkeypatch):
        def broken():
            raise OSError("dxgi")

        monkeypatch.setattr(gpu_memory, "adapters", broken)
        assert gpu_memory.process_usage_mib() is None

    @pytest.mark.skipif(sys.platform == "win32", reason="the Linux side")
    def test_outside_windows_there_is_nothing(self):
        assert gpu_memory.adapters() == []


class TestTheFirstLogLine:
    def test_says_when_the_process_began(self, tmp_path):
        log_dir = tmp_path / "logs"
        result = subprocess.run(
            [sys.executable, "-c", "from hams import bootstrap; bootstrap.start()"],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "HAMS_CONFIG": str(tmp_path / "config.toml"),
                "HAMS_LOG_DIR": str(log_dir),
                "HAMS_DATA_DIR": str(tmp_path / "data"),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        first = (log_dir / "hams.log").read_text(encoding="utf-8").splitlines()[0]
        assert re.search(r"Hams starting, \d+\.\d\d s after the process began", first)


# ---------------------------------------------------------------------- the budget, on a real start


def _seconds(log: str, pattern: str) -> float | None:
    match = re.search(pattern + r".*?(\d+\.\d\d) s after the process began", log)
    return float(match.group(1)) if match else None


@pytest.mark.skipif(not os.environ.get("HAMS_PERF"), reason="time budgets: set HAMS_PERF=1")
def test_the_start_budget(tmp_path):
    cache = os.environ.get("HAMS_BUDGET_HF_CACHE")
    if not cache or not Path(cache).is_dir():
        pytest.skip("set HAMS_BUDGET_HF_CACHE to a model cache with the turbo model")
    config = tmp_path / "roaming" / "config.toml"
    config.parent.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "hams" / "config.default.toml", config)  # an existing file: no assistant
    log_dir = tmp_path / "logs"
    env = {
        **os.environ,
        "HAMS_TEST_INSTANCE": "budget",
        "HAMS_CONFIG": str(config),
        "HAMS_LOG_DIR": str(log_dir),
        "HAMS_DATA_DIR": str(tmp_path / "data"),
        "HF_HUB_CACHE": cache,
        "HF_HUB_OFFLINE": "1",  # never a download, whatever happens
    }
    process = subprocess.Popen([sys.executable, "-m", "hams", "--background"], cwd=PROJECT_ROOT, env=env)
    try:
        deadline = time.monotonic() + 90
        log = ""
        while time.monotonic() < deadline and "Model ready for UI" not in log:
            time.sleep(0.2)
            log_file = log_dir / "hams.log"
            log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
            assert process.poll() is None, log
    finally:
        process.kill()
        process.wait(30)
    hotkeys = _seconds(log, "Hotkeys ready")
    ready = _seconds(log, "Model ready for UI")
    assert hotkeys is not None and ready is not None, log
    print(f"start budget: hotkeys {hotkeys:.2f} s, model ready {ready:.2f} s")  # noqa: T201 - shown with -s
    assert hotkeys <= HOTKEYS_BUDGET
    assert ready <= MODEL_READY_BUDGET

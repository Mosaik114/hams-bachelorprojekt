"""Ersatz fuer alles, was der Einrichtungsassistent vom Rechner braucht (siehe `SetupServices`).

Kein Ersatz fragt ein Netz, eine GPU oder Ollama.
"""

from types import SimpleNamespace

from hams import setup_wizard as sw
from hams.ollama import OllamaModel, OllamaState


class InlineThread:
    """Fuehrt die Arbeit sofort auf dem Testthread aus."""

    def __init__(self, target, **_options):
        self.target = target

    def start(self) -> None:
        self.target()


def fake_services(**overrides) -> sw.SetupServices:
    state = SimpleNamespace(persisted=[], opened=[], installed=set(), pulls=[])
    values = dict(
        gpu_present=lambda: False,
        gpu_libraries_found=lambda: False,
        gpu_download_supported=lambda: True,
        install_gpu=lambda progress, cancelled: None,
        gpu_download_bytes=1_400_000_000,
        gpu_space_needed=3_500_000_000,
        free_space=lambda: 100_000_000_000,
        installed_models=lambda: set(state.installed),
        probe_ollama=lambda: OllamaState(False, error="aus"),
        pull_ollama=lambda name, progress, cancelled: state.pulls.append(name),
        persist=lambda *args: state.persisted.append(args) or True,
        open_url=lambda url: state.opened.append(url),
    )
    values.update(overrides)
    services = sw.SetupServices(**values)
    services.state = state
    return services


def online(*names) -> OllamaState:
    """Ein erreichbares Ollama mit diesen Modellen."""
    return OllamaState(True, [OllamaModel(name) for name in names])

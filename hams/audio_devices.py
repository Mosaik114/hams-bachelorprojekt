"""The microphones: which input devices there are and which one a setting means.

Kept apart from `config` so that reading the settings never queries PortAudio; the recording and the settings window
ask here when they need a device.
"""

import re
from typing import Any

import sounddevice as sd


def resolve_mic_device(raw: Any) -> str | int | None:
    """Resolve microphone device from config value.

    Args:
        raw: Config value - can be None, int (device index), or string (device name).

    Returns:
        None for default device, int for device index, or string for device name.

    A name substring is resolved to a concrete input-device *index* here, because
    the same physical mic appears under several host APIs (MME/DirectSound/WASAPI)
    and passing the bare string to sounddevice raises "Multiple input devices found".
    Among matches we prefer the default host API, then the lowest device index.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        needle = raw.strip().lower()
        if needle in ("", "default"):
            return None
        try:
            devices = sd.query_devices()
            default_hostapi = sd.default.hostapi
        except Exception:
            return raw  # fall back to letting sounddevice resolve the name
        matches = [
            idx
            for idx, dev in enumerate(devices)
            if dev.get("max_input_channels", 0) > 0 and needle in str(dev.get("name", "")).lower()
        ]
        if not matches:
            return raw
        matches.sort(key=lambda idx: (devices[idx].get("hostapi") != default_hostapi, idx))
        return matches[0]
    return None


def list_input_devices() -> list[tuple[str, str]]:
    """Eingabegeräte als (Anzeigename, Such-String) — pro physischem Mikro nur
    einmal (bevorzugt die Standard-Host-API).

    Gibt es WASAPI, zählen nur dessen Geräte: das ist genau die Liste, die auch
    Windows unter Eingabe zeigt. MME kürzt Namen auf 31 Zeichen, und WDM-KS
    führt sogar Geräte auf, die in Windows deaktiviert sind (Kamera-Mikrofon,
    Stereomix) — beides ergab doppelte und unerwünschte Einträge.
    """
    try:
        devices = sd.query_devices()
        default_hostapi = sd.default.hostapi
        wasapi = next((i for i, api in enumerate(sd.query_hostapis()) if "WASAPI" in str(api.get("name", ""))), None)
    except Exception:
        return []
    seen: dict[str, tuple[str, str, int]] = {}
    for dev in devices:
        if dev.get("max_input_channels", 0) <= 0:
            continue
        if wasapi is not None and dev.get("hostapi") != wasapi:
            continue
        name = str(dev.get("name", "")).strip()
        if not name:
            continue
        # Anzeigenamen normalisieren (führendes "3- " etc. entfernen).
        pretty = re.sub(r"^\d+-\s*", "", name)
        prefer = 0 if dev.get("hostapi") == default_hostapi else 1
        prev = seen.get(pretty)
        if prev is None or prefer < prev[2]:
            seen[pretty] = (pretty, name, prefer)
    return [(p, full) for (p, full, _pref) in seen.values()]

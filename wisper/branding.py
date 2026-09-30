"""Produktmarke: Name, Bildmarke und die daraus abgeleiteten Symbole.

Das interne Python-Package heisst weiterhin `wisper` — sichtbar ist das
Produkt aber `Hams`. Diese Datei ist die einzige Stelle, an der der sichtbare
Name und die Bildmarke stehen; alles andere fragt hier nach.

Die Bildmarke liegt in mehreren, eigens für kleine Grössen gezeichneten
Fassungen vor. Für das Tray-Symbol wird davon nur die **Form** benutzt: die
Farbe kommt aus dem Zustand (bereit, Aufnahme, KI, Fehler), die Geometrie ist
immer dieselbe. Das grosse 1024-px-App-Icon auf 16 px zu quetschen ergäbe an
dieser Stelle nur Matsch.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger("wisper")

#: Sichtbarer Produktname. Steht in Fenstertiteln, im Tray und in Meldungen.
PRODUCT_NAME = "Hams"

#: Arabische Markenform. Bestandteil der Wortmarke, nicht der deutschen
#: Oberfläche — die bleibt von links nach rechts gesetzt.
PRODUCT_NAME_AR = "همس"

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "branding"

#: Für kleine Grössen gezeichnete Fassungen der Bildmarke. Sie tragen mehr
#: Fläche als die heruntergerechnete grosse Marke und bleiben dadurch im Tray
#: erkennbar. Benutzt wird nur ihr Alphakanal.
_TRAY_MASKS = {
    16: "hams_tray_dark_16.png",
    20: "hams_tray_dark_20.png",
    24: "hams_tray_dark_24.png",
    32: "hams_tray_dark_32.png",
    64: "hams_tray_dark.png",
}

#: Fenster- und Anwendungssymbol.
ICON_FILE = "hams.ico"
APP_ICON_FILE = "hams_app_icon.png"

_mask_cache: dict[int, Image.Image] = {}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _load_mask(size: int) -> Image.Image | None:
    """Alphakanal der Bildmarke in der gewünschten Kantenlänge.

    Genommen wird die kleinste vorgezeichnete Fassung, die mindestens so gross
    ist wie die gewünschte — herunterrechnen bleibt sauber, hochrechnen nicht.
    """
    cached = _mask_cache.get(size)
    if cached is not None:
        return cached
    candidates = sorted(_TRAY_MASKS)
    source = next((s for s in candidates if s >= size), candidates[-1])
    path = ASSET_DIR / _TRAY_MASKS[source]
    try:
        with Image.open(path) as image:
            mask = image.convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        logger.warning("Bildmarke %s nicht lesbar", path, exc_info=True)
        return None
    if mask.size != (size, size):
        mask = mask.resize((size, size), Image.LANCZOS)
    _mask_cache[size] = mask
    return mask


def tray_image(colour: str, size: int = 64) -> Image.Image:
    """Die Bildmarke in der Farbe des aktuellen Zustands.

    Fehlt die Vorlage, bleibt eine schlichte gefüllte Scheibe — nie wieder ein
    Buchstabe: das alte weisse „W" auf blauem Kreis gehörte zu einem anderen
    Produkt und soll in keinem Zustand mehr auftauchen.
    """
    rgb = _hex_to_rgb(colour)
    mask = _load_mask(size)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if mask is None:      # pragma: no cover - nur ohne Assets
        from PIL import ImageDraw

        ImageDraw.Draw(image).ellipse([2, 2, size - 2, size - 2], fill=rgb + (255,))
        return image
    image.paste(rgb + (255,), (0, 0, size, size), mask)
    return image


def icon_path() -> Path | None:
    """Das Windows-Symbol mit allen Grössen, oder None wenn es fehlt."""
    path = ASSET_DIR / ICON_FILE
    return path if path.exists() else None


def app_icon_image(size: int = 64) -> Image.Image | None:
    """Das App-Symbol als Bild — für `iconphoto`, das kein ICO annimmt."""
    path = ASSET_DIR / APP_ICON_FILE
    try:
        with Image.open(path) as image:
            return image.convert("RGBA").resize((size, size), Image.LANCZOS)
    except (OSError, ValueError):
        logger.warning("App-Symbol %s nicht lesbar", path, exc_info=True)
        return None

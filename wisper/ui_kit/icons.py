"""Linien-Icons, gezeichnet statt aus Schriftzeichen."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageTk

from wisper.ui_kit.caches import _icon_cache
from wisper.ui_kit.scale import Scale, px
from wisper.ui_kit.surfaces import SUPERSAMPLE
from wisper.ui_kit.theme import Theme

# ---------------------------------------------------------------- Icons

ICON_BOX = 16
"""Logische Kantenlänge des Rasters, in dem alle Icons gezeichnet werden."""

ICON_STROKE = 1.5
"""Strichstärke in logischen Pixeln — für alle Motive identisch."""
ICON_CACHE_LIMIT = 128


def _stroke(draw: ImageDraw.ImageDraw, points: list, width: float, color: str) -> None:
    """Polylinie mit runden Ecken und runden Enden.

    PIL kennt keine Linienenden; die Kreise an jedem Stützpunkt liefern sie —
    und zugleich saubere Innenecken.
    """
    draw.line(points, fill=color, width=max(1, int(round(width))), joint="curve")
    radius = width / 2.0
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)


def _icon_settings(draw, unit: float, stroke: float, color: str) -> None:
    """Regler statt Zahnrad — drei Linien mit versetzten Reglerpunkten."""
    # Knopfradius und Linienlaenge bewusst knapp: mit drei vollen Linien wirkt
    # das Motiv sonst deutlich schwerer als die uebrigen fuenf Icons.
    knob = stroke * 1.15
    for y, knob_x in ((4.3, 5.6), (8.0, 9.8), (11.7, 6.6)):
        _stroke(draw, [(3.0 * unit, y * unit), (13.0 * unit, y * unit)], stroke, color)
        cx, cy = knob_x * unit, y * unit
        draw.ellipse([cx - knob, cy - knob, cx + knob, cy + knob], fill=color)


def _icon_close(draw, unit: float, stroke: float, color: str) -> None:
    """Symmetrisches Kreuz — durch die Symmetrie zugleich optisch zentriert."""
    _stroke(draw, [(4.6 * unit, 4.6 * unit), (11.4 * unit, 11.4 * unit)], stroke, color)
    _stroke(draw, [(11.4 * unit, 4.6 * unit), (4.6 * unit, 11.4 * unit)], stroke, color)


def _icon_chevron(draw, unit: float, stroke: float, color: str) -> None:
    """Nach unten zeigender Winkel; um die eigene Höhe optisch ausgemittelt."""
    _stroke(
        draw,
        [(3.8 * unit, 6.4 * unit), (8.0 * unit, 10.2 * unit), (12.2 * unit, 6.4 * unit)],
        stroke,
        color,
    )


def _icon_check(draw, unit: float, stroke: float, color: str) -> None:
    _stroke(
        draw,
        [(3.6 * unit, 8.4 * unit), (6.6 * unit, 11.4 * unit), (12.4 * unit, 4.9 * unit)],
        stroke,
        color,
    )


def _icon_download(draw, unit: float, stroke: float, color: str) -> None:
    """Abwärtspfeil ohne Unterkante — bleibt auch klein eindeutig."""
    _stroke(draw, [(8.0 * unit, 3.4 * unit), (8.0 * unit, 11.6 * unit)], stroke, color)
    _stroke(
        draw,
        [(4.6 * unit, 8.2 * unit), (8.0 * unit, 11.6 * unit), (11.4 * unit, 8.2 * unit)],
        stroke,
        color,
    )


def _icon_stop(draw, unit: float, stroke: float, color: str) -> None:
    """Gefülltes Quadrat mit leicht gerundeten Ecken."""
    draw.rounded_rectangle(
        [4.6 * unit, 4.6 * unit, 11.4 * unit, 11.4 * unit],
        radius=stroke * 0.9,
        fill=color,
    )


def _icon_trash(draw, unit: float, stroke: float, color: str) -> None:
    """Papierkorb: Deckel, Griff, Korpus — ohne Innenstriche.

    Die ueblichen zwei senkrechten Striche im Korpus fallen weg. Bei 16 px
    liegen sie knapp zwei Bildpunkte auseinander und laufen beim Verkleinern
    zu einem grauen Fleck zusammen; die Silhouette allein bleibt eindeutig.
    """
    _stroke(draw, [(3.4 * unit, 5.0 * unit), (12.6 * unit, 5.0 * unit)], stroke, color)
    _stroke(
        draw,
        [(6.3 * unit, 5.0 * unit), (6.3 * unit, 3.2 * unit), (9.7 * unit, 3.2 * unit), (9.7 * unit, 5.0 * unit)],
        stroke,
        color,
    )
    _stroke(
        draw,
        [(4.9 * unit, 5.0 * unit), (5.5 * unit, 12.8 * unit), (10.5 * unit, 12.8 * unit), (11.1 * unit, 5.0 * unit)],
        stroke,
        color,
    )


_ICON_PAINTERS = {
    "settings": _icon_settings,
    "close": _icon_close,
    "chevron": _icon_chevron,
    "check": _icon_check,
    "download": _icon_download,
    "stop": _icon_stop,
    "trash": _icon_trash,
}


def icon_names() -> list[str]:
    return list(_ICON_PAINTERS)


def render_icon(name: str, size: int, color: str) -> Image.Image:
    """Zeichnet ein Icon als RGBA-Bild. Reines PIL, ohne Tk und ohne Cache.

    Args:
        name: Schlüssel aus `icon_names()`.
        size: Kantenlänge in **physischen** Pixeln (der Aufrufer hat bereits
            `px()` angewandt).
        color: Strichfarbe. Die Geometrie ist für alle Zustände dieselbe —
            Hover, Disabled und Auswahl unterscheiden sich nur hierin.
    """
    painter = _ICON_PAINTERS.get(name)
    if painter is None:
        raise KeyError(f"Unbekanntes Icon: {name!r}")

    size = max(1, int(size))
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    unit = big / ICON_BOX  # ein Rasterschritt in Zielpixeln
    stroke = ICON_STROKE * (size / ICON_BOX) * SUPERSAMPLE
    painter(draw, unit, stroke, color)

    return image.resize((size, size), Image.LANCZOS)


def icon(name: str, size: int | None = None, color: str | None = None):
    """Wie `render_icon()`, aber als Tk-Bild und zwischengespeichert.

    `size` ist eine **logische** Größe und wird hier skaliert; der Cache-Schlüssel
    trägt Motiv, Größe, Farbe und DPI-Faktor.

    Die Standardfarbe wird erst hier aufgeloest, nicht im Vorgabewert des
    Parameters: der waere beim Import eingefroren und bliebe nach einem
    Moduswechsel auf der alten Palette stehen.
    """
    if color is None:
        color = Theme.TEXT_SECONDARY
    logical = ICON_BOX if size is None else size
    physical = px(logical)
    key = (name, physical, color, Scale.factor)
    cached = _icon_cache.get(key)
    if cached is not None:
        _icon_cache.move_to_end(key)
        return cached

    photo = ImageTk.PhotoImage(render_icon(name, physical, color))
    _icon_cache[key] = photo
    if len(_icon_cache) > ICON_CACHE_LIMIT:
        _icon_cache.popitem(last=False)
    return photo

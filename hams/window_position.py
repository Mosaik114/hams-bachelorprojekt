"""Wo das Hauptfenster steht: gespeicherte Lage pruefen und auf einen Bildschirm holen."""

import logging

from hams import ui_kit as uk
from hams.config_validation import clean_coordinate

logger = logging.getLogger("hams")  # derselbe wie config.logger; kein Import, sonst Kreis mit config


#: Rueckfallabstand zur Ecke der Arbeitsflaeche, in logischen Pixeln.
POSITION_FALLBACK = 48
#: So viel vom Fenster muss auf einem Bildschirm liegen, damit es greifbar ist.
MIN_VISIBLE_W = 120
MIN_VISIBLE_H = 60
#: Nach dem Ziehen wird einmal geschrieben, nicht bei jedem Ereignis.
POSITION_SAVE_DELAY_MS = 700


def _visible_enough(x: int, y: int, width: int, height: int, areas) -> bool:
    """Liegt ein brauchbarer Teil des Fensters auf einem Bildschirm?

    Nicht nur "irgendwo im virtuellen Desktop": ein Fenster, von dem nur zehn
    Pixel am Rand herausschauen, ist nicht bedienbar. Zusaetzlich muss die
    Oberkante unterhalb des Arbeitsflaechenrandes liegen — dort sitzen
    Kopfzeile und Ziehflaeche, und was darueber liegt, bekommt niemand zu
    fassen.
    """
    for left, top, right, bottom in areas:
        if y < top:
            continue
        overlap_w = min(x + width, right) - max(x, left)
        overlap_h = min(y + height, bottom) - max(y, top)
        if overlap_w >= uk.px(MIN_VISIBLE_W) and overlap_h >= uk.px(MIN_VISIBLE_H):
            return True
    return False


def window_position(saved_x, saved_y, width: int, height: int, areas) -> tuple[int, int]:
    """Startlage des Hauptfensters: gemerkte Position oder Rueckfall.

    Gerechnet wird in **physischen** Bildschirmpixeln — derselben Einheit, die
    `geometry()` erwartet, die `winfo_x()` liefert und in der die
    Arbeitsflaechen gemeldet werden. Logisch zu speichern hiesse, bei jedem
    Start durch den Skalierungsfaktor zu teilen und wieder zu multiplizieren:
    das wanderte ueber mehrere Laeufe um die Rundungsfehler und landete nach
    einem DPI-Wechsel an einer anderen Stelle des Schirms.
    """
    left, top = (areas[0][0], areas[0][1]) if areas else (0, 0)
    fallback = (left + uk.px(POSITION_FALLBACK), top + uk.px(POSITION_FALLBACK))
    x, y = clean_coordinate(saved_x), clean_coordinate(saved_y)
    if x is None or y is None:
        return fallback
    if not _visible_enough(x, y, width, height, areas):
        logger.info("Saved window position %s,%s is no longer visible", x, y)
        return fallback
    return x, y

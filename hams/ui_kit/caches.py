"""Zwischenspeicher fuer gerenderte Bilder; beim Umfaerben und Umskalieren geleert.

Ein Speicher fuer alles Gemalte (Flaechen, Banner, Bildmarke, zusammengesetzte Auswahlfelder): er haelt die
Grenze an *einer* Stelle durch, nach Anzahl und nach Bytes. Frueher legte der Banner an der Grenze vorbei ab,
und ein einziges Ziehen am Fensterrand verdraengte alle Hover-Bilder.
"""

from __future__ import annotations

from collections import OrderedDict

from PIL import ImageTk


class ImageCache(OrderedDict):
    """LRU-Speicher fuer Tk-Bilder mit Grenze nach Anzahl *und* Bytes, samt Treffer-Statistik.

    Bleibt ein `OrderedDict`, damit Lesen, Leeren und Zaehlen wie bisher gehen; abgelegt wird ueber `store`,
    gelesen ueber `fetch`.
    """

    def __init__(self, limit: int, budget_bytes: int) -> None:
        super().__init__()
        self.limit = limit
        self.budget_bytes = budget_bytes
        self.bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._sizes: dict = {}

    @staticmethod
    def size_of(photo) -> int:
        try:
            return int(photo.width()) * int(photo.height()) * 4
        except Exception:  # noqa: BLE001 - something without a size counts as small
            return 0

    def fetch(self, key):
        photo = self.get(key)
        if photo is None:
            self.misses += 1
            return None
        self.hits += 1
        self.move_to_end(key)
        return photo

    def store(self, key, photo):
        if key in self:
            self.bytes -= self._sizes.pop(key, 0)
        super().__setitem__(key, photo)
        self.move_to_end(key)
        size = self.size_of(photo)
        self._sizes[key] = size
        self.bytes += size
        while len(self) > 1 and (len(self) > self.limit or self.bytes > self.budget_bytes):
            self.popitem(last=False)
            self.evictions += 1
        return photo

    def popitem(self, last: bool = True):
        key, value = super().popitem(last=last)
        self.bytes -= self._sizes.pop(key, 0)
        return key, value

    def clear(self) -> None:
        super().clear()
        self._sizes.clear()
        self.bytes = 0


#: Flaechen, Banner, Bildmarke, zusammengesetzte Controls: 256 Eintraege, 48 MB.
_img_cache = ImageCache(limit=256, budget_bytes=48 * 1024 * 1024)

_icon_cache: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()

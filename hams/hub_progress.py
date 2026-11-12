"""Fortschritt und Abbruch beim Laden eines Modells vom Hugging-Face-Hub (tqdm-Ersatz).

Ausgelagert aus models.py (Q7); `ModelDownloader` reicht die Klasse als `tqdm_class` an `snapshot_download`.
"""

from __future__ import annotations

import threading


class DownloadCancelled(Exception):
    """Der Nutzer hat den Download abgebrochen."""


class _ProgressBar:
    """Ersetzt tqdm in `snapshot_download` — fuer Fortschritt und Abbruch.

    Der Hub legt genau zwei Balken dieser Klasse an: einen fuer die Bytes (ohne
    Iterable, `unit="B"`), dessen `total` die einzelnen Dateidownloads von aussen
    hochzaehlen, und einen fuer die Dateizahl, der ueber `thread_map` das
    Ergebnis-Iterable umschliesst. Der zweite *muss* durchlaufen werden: sonst
    werden die Futures der Arbeitsthreads nie abgeholt, und ein Fehler beim
    Laden verschwindet stillschweigend.

    Der Byte-Balken ist zugleich die einzige Stelle, an der ein Abbruch greifen
    kann. Python-Threads lassen sich nicht von aussen beenden; hier wird
    waehrend des Ladens regelmaessig zurueckgefragt.
    """

    #: Werden vor jedem Lauf gesetzt. Es laeuft immer nur ein Download.
    cancel_flag = None
    report = None

    def __init__(self, iterable=None, *_args, **kwargs) -> None:
        self._guard = threading.Lock()
        self._total = 0
        self.iterable = iterable
        self.total = kwargs.get("total") or 0
        self.n = kwargs.get("initial") or 0
        self.desc = kwargs.get("desc", "")
        self.disable = False
        #: Nur der Byte-Balken kommt ohne Iterable — der andere zaehlt Dateien.
        self._counts_bytes = iterable is None

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value) -> None:
        """Waechst nur. `bytes_progress.total += n` laeuft aus acht Threads.

        Der Hub liest, addiert und schreibt ohne Sperre; ein veralteter
        Schreibvorgang machte die Gesamtgroesse sonst wieder kleiner und die
        Anzeige sprang von "0/78 MB" auf "13/76 MB" zurueck.
        """
        with self._guard:
            self._total = max(self._total, int(value or 0))

    # ------------------------------------------------ tqdm-Schnittstelle

    def __iter__(self):
        for item in self.iterable if self.iterable is not None else ():
            yield item
            self.update(1)

    def update(self, n=1) -> None:
        with self._guard:
            self.n += n or 0
            done, total = self.n, self.total
        if not self._counts_bytes:
            return
        if _ProgressBar.cancel_flag is not None and _ProgressBar.cancel_flag.is_set():
            raise DownloadCancelled()
        if _ProgressBar.report is not None:
            _ProgressBar.report(int(done), int(total))

    def close(self) -> None:
        pass

    def refresh(self) -> None:
        pass

    def set_description(self, *_args, **_kwargs) -> None:
        pass

    def set_description_str(self, *_args, **_kwargs) -> None:
        pass

    def set_postfix(self, *_args, **_kwargs) -> None:
        pass

    def write(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # `thread_map` teilt seine Sperre ueber die Balkenklasse. Ohne diese beiden
    # Methoden bricht tqdm ab, bevor ueberhaupt eine Datei geladen wird.
    @classmethod
    def get_lock(cls):
        lock = getattr(cls, "_lock", None)
        if lock is None:
            lock = threading.RLock()
            cls._lock = lock
        return lock

    @classmethod
    def set_lock(cls, lock) -> None:
        cls._lock = lock

"""The main window of Hams: its five zones, light and dark, and its position.

FloatingTranscriberApp inherits it as a mixin. The states (what the zones show) stay in app.py; `apply_state` is
their one place.
"""

import contextlib
import tkinter as tk

from hams import branding
from hams import ui_kit as uk
from hams.config import CFG, _persist_config_value, logger
from hams.i18n import t
from hams.ui_kit import Theme
from hams.window_mark import mark_label
from hams.window_position import POSITION_SAVE_DELAY_MS


class MainWindowMixin:
    """Zonen, Darstellung und Lage des Hauptfensters (braucht self.root)."""

    # ---------------------------------------------------------- Darstellung

    def set_appearance(self, mode: str) -> None:
        """Schaltet zwischen hell und dunkel um — sofort, ohne Neustart.

        Reihenfolge mit Bedacht: erst ein offenes Popover schliessen, dann die
        Palette wechseln, dann die Fenster einfaerben. Umgekehrt bliebe ein
        schwebendes Toplevel einen Wimpernschlag in der alten Palette stehen.

        Bewusst ohne Ueberblendung: eine Fensterueberblendung muesste jedes
        Bild neu rendern, und die Flaechen entstehen ueber PIL. Der Wechsel ist
        eine Entscheidung, keine Bewegung.
        """
        mode = Theme.normalise(mode)
        uk.Popover.dismiss_current()
        if not Theme.apply(mode):
            return
        CFG.appearance = mode
        painted = self._retint_windows()
        logger.info("Appearance switched to %s (%s widgets)", mode, painted)

    def _retint_windows(self) -> int:
        """Faerbt alle offenen Hams-Fenster neu ein. Gibt die Widgetzahl zurueck."""
        painted = uk.apply_theme_tree(self.root)
        for win in (self.settings_win, self.setup_win):
            if win is not None and win.alive():
                painted += win.retint()
        return painted

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        """Fünf feste Zonen: Kopf, Status, Pegel, Aktion, Kurzbefehle.

        Jede Zone behält ihren Platz in jedem Zustand — deshalb bleibt die
        Fensterhöhe konstant und nichts springt.
        """
        card = tk.Frame(self.root, bg=Theme.WINDOW_BG)
        card.pack(fill="both", expand=True, padx=uk.px(uk.Space.WINDOW), pady=uk.px(uk.Space.WINDOW))
        self._build_head(card)
        self._build_status_row(card)

        # --- 3. Pegel: immer an derselben Stelle, im Ruhezustand dunkel ---
        self.level_meter = uk.SignalMeter(card, bg_under=Theme.WINDOW_BG)
        self.level_meter.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))

        # --- 4. Hauptaktion ---
        self.main_button = uk.SecondaryButton(
            card,
            t("button.start_recording"),
            self._on_main_action,
            bg_under=Theme.WINDOW_BG,
            role="primary",
            height=uk.Height.PRIMARY,
        )
        self.main_button.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))
        self._build_footer(card)

    def _build_head(self, card: tk.Frame) -> None:
        """1. Kopfzeile: Wortmarke (ziehbar) + zwei Icon-Schaltflächen."""
        head = tk.Frame(card, bg=Theme.WINDOW_BG)
        head.pack(fill="x")

        # Right to left (Arabic) the whole head is mirrored: mark and name on the right, the buttons on the left.
        mark_label(head, bg=Theme.WINDOW_BG).pack(
            side=uk.mirror_side("left"), padx=uk.mirror_pad((0, uk.px(uk.Space.MD)))
        )
        title = tk.Label(
            head,
            text=branding.PRODUCT_NAME,
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("window_title"),
            anchor=uk.mirror_anchor("w"),
        )
        title.pack(side=uk.mirror_side("left"))
        # Reihenfolge der Erzeugung ist die Reihenfolge der Tabulatorkette; sie
        # soll der Leserichtung folgen (Zahnrad, dann Kreuz). Gepackt wird
        # umgekehrt, weil `side="right"` das zuerst gepackte ganz aussen setzt.
        self._gear_btn = uk.IconButton(
            head, "settings", self._open_settings, bg_under=Theme.WINDOW_BG, tooltip=t("main.settings_tooltip")
        )
        self._close_btn = uk.IconButton(
            head, "close", self._hide_window, bg_under=Theme.WINDOW_BG, tooltip=t("main.hide_tooltip")
        )
        self._close_btn.set_hover_colour(Theme.ERROR)
        if not uk.system_frame():  # unter Linux hat die KDE-Titelleiste das Kreuz
            self._close_btn.pack(side=uk.mirror_side("right"))
        self._gear_btn.pack(side=uk.mirror_side("right"), padx=uk.mirror_pad((0, uk.px(uk.Space.XS))))

    def _build_status_row(self, card: tk.Frame) -> None:
        """2. Statuszeile: die einzige Zustandsanzeige des Produkts."""
        status_row = tk.Frame(card, bg=Theme.WINDOW_BG)
        status_row.pack(fill="x", pady=(uk.px(uk.Space.XL), 0))

        self.status_dot = uk.StatusDot(status_row, bg_under=Theme.WINDOW_BG, size=uk.StatusDot.SIZE_MAIN)
        self.status_dot.pack(side=uk.mirror_side("left"), pady=uk.px(uk.Space.SM))
        self.status_label = tk.Label(
            status_row,
            text="",
            anchor=uk.mirror_anchor("w"),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_PRIMARY,
            font=uk.font("status"),
        )
        self.status_label.pack(side=uk.mirror_side("left"), padx=uk.mirror_pad((uk.px(uk.Space.MD), 0)))
        self.elapsed_label = tk.Label(
            status_row,
            text="",
            anchor=uk.mirror_anchor("e"),
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_SECONDARY,
            font=uk.font("status"),
        )
        self.elapsed_label.pack(side=uk.mirror_side("right"))

    def _build_footer(self, card: tk.Frame) -> None:
        """5. Fußzeile: beide Kurzbefehle als Tasten, im Fehlerfall der Hinweis.

        Feste Höhe: der Hinweis tritt an die Stelle der Tasten, und das Fenster darf dabei nicht springen."""
        self.footer = tk.Frame(card, bg=Theme.WINDOW_BG, height=uk.px(self.FOOTER_HEIGHT))
        self.footer.pack(fill="x", pady=(uk.px(uk.Space.LG), 0))
        self.footer.pack_propagate(False)

        self.shortcut_box = tk.Frame(self.footer, bg=Theme.WINDOW_BG)
        # The name on the reading side, the keys on the other (right to left: name right, keys left).
        name_column, keys_column = (1, 0) if uk.Writing.rtl else (0, 1)
        self.shortcut_box.grid_columnconfigure(name_column, weight=1)
        self.shortcut_keys = {}
        #: (name, keys) per shortcut row; their width decides how wide the window must be (`_fit_window`).
        self._shortcut_rows = []
        for row, (key, text, combo) in enumerate(self.shortcuts()):
            gap = (uk.px(uk.Space.SM) if row else 0, 0)
            name = tk.Label(
                self.shortcut_box,
                text=text,
                anchor=uk.mirror_anchor("w"),
                bd=0,
                padx=0,
                pady=0,
                bg=Theme.WINDOW_BG,
                fg=Theme.TEXT_TERTIARY,
                font=uk.font("description"),
            )
            name.grid(row=row, column=name_column, sticky=uk.mirror_anchor("w"), pady=gap)
            keys = uk.KeyCombo(self.shortcut_box, bg_under=Theme.WINDOW_BG, combo=combo)
            keys.grid(row=row, column=keys_column, sticky=uk.mirror_anchor("e"), pady=gap)
            self._shortcut_rows.append((name, keys))
            self.shortcut_keys[key] = keys
        self.shortcut_box.pack(fill="x")

        self.footer_label = tk.Label(
            self.footer,
            text="",
            anchor=uk.mirror_anchor("nw"),
            justify=uk.text_justify(),
            bd=0,
            padx=0,
            pady=0,
            bg=Theme.WINDOW_BG,
            fg=Theme.ERROR,
            font=uk.font("description"),
        )
        # Der Hinweis bricht um statt abzuschneiden („… läuft der Dien“).
        self.footer.bind("<Configure>", lambda e: self.footer_label.configure(wraplength=max(1, e.width)), add="+")

    #: Zwei Tastenreihen plus Abstand dazwischen, in logischen Pixeln.
    FOOTER_HEIGHT = 2 * uk.Height.KEYCAP + uk.Space.SM

    @staticmethod
    def shortcuts() -> tuple:
        """(Schlüssel, Bezeichnung, Tastenkürzel) für Fußzeile und Einstellungen.

        Kurze Bezeichnungen mit Absicht: vier Tasten brauchen bei 100 % schon
        249 px der 320 px Innenbreite; „Prompt an KI“ passte daneben nicht mehr.
        """
        return (
            ("transcript", t("shortcut.transcript"), CFG.hotkey_transcript),
            ("prompt", t("shortcut.prompt"), CFG.hotkey_prompt),
        )

    def _show_footer_hint(self, hint: str | None, *, calm: bool = False) -> None:
        """Tasten oder Hinweis — nie beides, und nie mit anderer Höhe. `calm`: Auskunft statt Fehler."""
        if hint:
            self.footer_label.configure(text=hint, fg=Theme.TEXT_SECONDARY if calm else Theme.ERROR)
            self.shortcut_box.pack_forget()
            self.footer_label.pack(fill="both", expand=True)
        else:
            self.footer_label.configure(text="")
            self.footer_label.pack_forget()
            self.shortcut_box.pack(fill="x")

    def _footer_width(self) -> int:
        """Pixels the shortcut rows need: name, a gap, the keys, and the window margins.

        At 360 logical pixels a long name ("Спросить ИИ", the Arabic "Ask AI") ran into its keys; such a language
        makes the window a little wider instead.
        """
        widest = 0
        for name, keys in self._shortcut_rows:
            with contextlib.suppress(tk.TclError):
                widest = max(widest, name.winfo_reqwidth() + uk.px(uk.Space.SM) + keys.winfo_reqwidth())
        return widest + 2 * uk.px(uk.Space.WINDOW)

    def _fit_window(self) -> None:
        """Fensterhöhe aus den fünf Zonen berechnen.

        Gerechnet statt gemessen: `winfo_reqheight()` meldet vor dem ersten
        Configure-Ereignis nur die Texthöhe der Buttons, weil deren Fläche erst
        dann gerendert wird. Die Rechnung ist dagegen sofort richtig — und weil
        jede Zone ihren Platz behält, ändert sich die Höhe danach nie wieder.
        """
        line = self._line_height("status")
        footer = self.FOOTER_HEIGHT

        logical = (
            uk.Space.WINDOW  # Rand oben
            + uk.IconButton.SIZE
            + uk.Depth.CONTROL  # Kopfzeile, die Steinknöpfe mit ihrer Seite
            + uk.Space.XL
            + line
            + 2 * uk.Space.SM  # Statuszeile
            + uk.Space.LG
            + uk.SignalMeter.HEIGHT  # Pegel
            + uk.Space.LG
            + uk.Height.PRIMARY
            + uk.Depth.CONTROL  # Hauptaktion mit ihrer Seite
            + uk.Space.LG
            + footer  # Kurzbefehle als Tasten
            + uk.Space.WINDOW  # Rand unten
        )
        width = max(uk.px(self.WINDOW_WIDTH), self._footer_width())
        height = uk.px(max(logical, self.WINDOW_MIN_HEIGHT))
        self._window_size = (width, height)
        self.root.geometry(f"{width}x{height}")
        self.footer.configure(height=uk.px(self.FOOTER_HEIGHT))

    def _line_height(self, role: str) -> int:
        """Zeilenhöhe einer Schriftrolle in *logischen* Pixeln."""
        try:
            pixels = uk.font(role).metrics("linespace")
        except AttributeError:  # vor init_fonts()
            pixels = uk.px(14)
        return max(1, int(round(pixels / max(uk.Scale.factor, 0.1))))

    # ------------------------------------------------------------------ Fensterlage

    def _remember_position(self, x: int, y: int) -> None:
        """Merkt die Lage sofort und schreibt sie erst, wenn die Hand still steht.

        Beim Ziehen fallen hunderte Ereignisse an. Jedes davon in die
        Konfiguration zu schreiben hiesse, fuer ein paar Pixel hundertmal eine
        Datei neu anzulegen.
        """
        self._window_pos = (int(x), int(y))
        self._cancel_position_job()
        try:
            self._position_job = self.root.after(POSITION_SAVE_DELAY_MS, self._save_position)
        except tk.TclError:  # pragma: no cover - Fenster verschwindet gerade
            self._position_job = None

    def _cancel_position_job(self) -> None:
        if self._position_job is None:
            return
        with contextlib.suppress(tk.TclError, ValueError):
            self.root.after_cancel(self._position_job)
        self._position_job = None

    def _save_position(self) -> None:
        """Schreibt die gemerkte Lage — nur, wenn sie sich geaendert hat."""
        self._position_job = None
        x, y = self._window_pos
        if (x, y) == (CFG.window_x, CFG.window_y):
            return
        CFG.window_x, CFG.window_y = x, y
        _persist_config_value("ui", "window_x", x)
        _persist_config_value("ui", "window_y", y)
        logger.info("Window position saved: %d,%d", x, y)

    def _flush_position(self) -> None:
        """Beim Verstecken und beim Beenden auf jeden Fall sichern.

        Nachgemessen wird nur an einem sichtbaren Fenster. Ein verstecktes
        meldet keine verlaessliche Lage; beim Beenden nach dem Verstecken
        wuerde sonst ein Nullwert eine gute gemerkte Position ueberschreiben.
        """
        self._cancel_position_job()
        try:
            if self.root.winfo_exists() and self.root.winfo_ismapped():
                self._window_pos = (self.root.winfo_x(), self.root.winfo_y())
        except tk.TclError:  # pragma: no cover
            pass
        try:
            self._save_position()
        except Exception:  # noqa: BLE001 - Verstecken, Beenden und Neustart duerfen nie daran scheitern
            logger.exception("Window position not saved")

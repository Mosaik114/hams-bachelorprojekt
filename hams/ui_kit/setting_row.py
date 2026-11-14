"""Eine Zeile der Einstellungen (Titel, Beschreibung oder Status, Control rechts oder darunter)."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from hams.ui_kit.controls import StatusDot
from hams.ui_kit.press_area import PressArea
from hams.ui_kit.scale import Scale, font, px
from hams.ui_kit.text import _fit_text, _line_height
from hams.ui_kit.theme import Height, Radius, Space, Theme, set_surface_under


class SettingRow(tk.Frame):
    """Eine Einstellungszeile in vier Ausprägungen.

    * `simple`      Titel — Control
    * `description` Titel / Beschreibung — Control
    * `status`      Titel / Statuspunkt + Text — Control
    * `display`     Titel — Wert, kein Control

    Bei `stacked=True` steht das Control nicht rechts, sondern in voller Breite
    unter dem Text. Das ist die Form für Auswahlfelder mit langen Werten
    (Geräte, Modelle): nebeneinander blieben für Titel, Beschreibung und Wert
    je nur rund 140 px, und alle drei wurden abgeschnitten.

    Die Höhe ergibt sich aus dem Inhalt: oben und unten `Space.ROW_Y`, dazwischen
    Text oder Control, mindestens `Height.ROW_MIN`. Eine Beschreibung bricht um,
    statt gekürzt zu werden.

    Bei `interactive=True` ist die ganze Zeile Auslöser (für Schalter). Bei
    Auswahlzeilen bleibt das ausdrücklich aus: ein Klick auf die Beschreibung
    soll kein Popover öffnen.
    """

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        *,
        variant: str = "simple",
        description: str = "",
        value: str = "",
        interactive: bool = False,
        command: Callable[[], None] | None = None,
        hairline: bool = True,
        stacked: bool = False,
    ) -> None:
        super().__init__(master, bg=Theme.GROUP_SURFACE)
        self._variant = variant
        self._interactive = interactive
        self._command = command
        self._stacked = stacked
        self._enabled = True
        self._hovered = False
        self._control: tk.Misc | None = None
        self._area: PressArea | None = None
        #: Die vollständigen Texte. Angezeigt wird, was in die Zeile passt.
        self._title_text = title
        self._sub_text = description

        self._build_text(title, description, variant)
        self._build_value(value, variant)
        self._build_hairline(hairline)
        self._apply_metrics()

        # Der Textblock bekommt erst nach dem Einhängen des Controls seine
        # endgültige Breite; vorher lässt sich weder kürzen noch umbrechen.
        self._text_box.bind("<Configure>", self._refit, add="+")
        if interactive:
            self._bind_interaction()
        self.bind("<Destroy>", self._on_destroy, add="+")
        Scale.register(self._on_scale_change)

    def _build_text(self, title: str, description: str, variant: str) -> None:
        """Titel; bei `description`/`status` darunter die Beschreibung, beim Status mit Punkt davor."""
        self._line = tk.Frame(self, bg=Theme.GROUP_SURFACE)
        self._line.pack(fill="x")
        self._line.grid_columnconfigure(0, weight=1)

        self._text_box = tk.Frame(self._line, bg=Theme.GROUP_SURFACE)
        self._text_box.grid(row=0, column=0, sticky="ew")

        self.title_label = tk.Label(
            self._text_box,
            text=title,
            bg=Theme.GROUP_SURFACE,
            fg=Theme.TEXT_PRIMARY,
            font=font("row_title"),
            anchor="w",
            bd=0,
            padx=0,
            pady=0,
        )
        self.title_label.pack(anchor="w")

        self.sub_box: tk.Frame | None = None
        self.status_dot: StatusDot | None = None
        self.sub_label: tk.Label | None = None
        if variant in ("description", "status"):
            self.sub_box = tk.Frame(self._text_box, bg=Theme.GROUP_SURFACE)
            self.sub_box.pack(anchor="w", fill="x", pady=(px(Space.TEXT_GAP), 0))
            if variant == "status":
                self.status_dot = StatusDot(self.sub_box, bg_under=Theme.GROUP_SURFACE, size=StatusDot.SIZE_ROW)
                self.status_dot.pack(side="left", anchor="n", padx=(0, px(Space.ICON_GAP)))
            self.sub_label = tk.Label(
                self.sub_box,
                text=description,
                bg=Theme.GROUP_SURFACE,
                fg=Theme.TEXT_TERTIARY,
                font=font("description"),
                anchor="w",
                justify="left",
                bd=0,
                padx=0,
                pady=0,
            )
            self.sub_label.pack(side="left", fill="x")

    def _build_value(self, value: str, variant: str) -> None:
        """`display`: der Wert rechts statt eines Controls."""
        self.value_label: tk.Label | None = None
        if variant == "display":
            self.value_label = tk.Label(
                self._line,
                text=value,
                bg=Theme.GROUP_SURFACE,
                fg=Theme.TEXT_SECONDARY,
                font=font("row_title"),
                anchor="e",
                justify="right",
                bd=0,
                padx=0,
                pady=0,
            )
            self.value_label.grid(row=0, column=1, sticky="e")

    def _build_hairline(self, hairline: bool) -> None:
        self.hairline: tk.Frame | None = None
        if hairline:
            holder = tk.Frame(self, bg=Theme.GROUP_SURFACE)
            holder.pack(fill="x")
            self.hairline = tk.Frame(holder, bg=Theme.HAIRLINE, height=max(1, px(1)))
            self.hairline.pack(fill="x", padx=px(Space.ROW_X))

    def _bind_interaction(self) -> None:
        """Die ganze Zeile ist Auslöser (Schalter): jeder Teil, auch Rand und Trennlinie."""
        self._area = PressArea(
            self,
            self._on_click,
            on_hover=self._on_hover,
            on_press=self._on_press_look,
            enabled=lambda: self._enabled,
        )
        self._area.set_cursor("hand2")

    # ------------------------------------------------------------ Aufbau

    def set_control(self, control: tk.Misc) -> None:
        """Hängt das Control rechts in die Zeile — und löst ein früheres ab.

        Der Austausch ist gewollt: eine Zeile zeigt während eines Downloads
        eine Abbruch-Schaltfläche statt des Auswahlfeldes.
        """
        if self._control is not None and self._control is not control:
            with contextlib.suppress(tk.TclError):  # bereits zerstoert
                self._control.grid_forget()
        self._control = control
        if control is None:
            return
        if self._area is not None:
            self._area.watch(control)  # der Schalter klickt selbst, haelt aber die Hervorhebung der Zeile
        # Der Fokus liegt auf der Fläche, kein Control hält Rand für einen Ring frei: alle Kanten stehen
        # ohne Ausgleich auf einer Linie.
        if self._stacked:
            # Auswahlfelder und Pegel nehmen die volle Breite; eine Schaltfläche,
            # die vorübergehend an ihre Stelle tritt, behält ihre eigene.
            sticky = "ew" if getattr(control, "stretch_stacked", True) else "w"
            control.grid(
                in_=self._line,
                row=1,
                column=0,
                columnspan=2,
                sticky=sticky,
                pady=(px(Space.STACK_GAP), 0),
            )
        else:
            control.grid(
                in_=self._line,
                row=0,
                column=1,
                sticky="e",
                padx=(px(Space.CONTROL_GAP), 0),
            )
        # Tk stapelt nach Erzeugungsreihenfolge; ein spaeter erzeugtes Control
        # laege sonst hinter dem Zeilengrund.
        # `tk.Misc.lift`, nicht `control.lift`: an einer Canvas hebt das Tags.
        tk.Misc.lift(control, self._line)

    def _apply_metrics(self) -> None:
        """Innenabstände und Mindesthöhe — beim Aufbau und bei jedem DPI-Wechsel.

        Alle sichtbaren Kanten (Text, Wert, Control) stehen bei `Space.ROW_X`.
        """
        self._line.configure(padx=px(Space.ROW_X), pady=px(Space.ROW_Y))
        # Gestapelt bestimmt der Text selbst die Höhe der ersten Reihe; eine
        # Mindesthöhe schöbe nur Luft zwischen Text und Feld.
        self._line.grid_rowconfigure(0, minsize=0 if self._stacked else px(Height.ROW_MIN - 2 * Space.ROW_Y))
        self._text_box.grid_configure(padx=0)
        if self._control is not None:
            self.set_control(self._control)
        if self.value_label is not None:
            self.value_label.grid_configure(padx=(px(Space.CONTROL_GAP), 0))
        if self.status_dot is not None:
            # Mittig zur ersten Textzeile, nicht an deren Oberkante.
            line = _line_height(font("description"), self)
            self.status_dot.pack_configure(pady=(max(0, (line - px(StatusDot.SIZE_ROW)) // 2), 0))

    # ------------------------------------------------------------ Zustand

    def _refit(self, _event=None) -> None:
        """Passt Titel und Unterzeile an den Platz an, der wirklich übrig ist.

        Die Unterzeile bricht um statt gekürzt zu werden: gerade Hinweise
        („Ollama wird gepr…“) wären sonst nicht lesbar. Nur der Titel wird im
        Notfall gekürzt; er ist kurz und passt praktisch immer.
        """
        try:
            width = self._text_box.winfo_width()
        except tk.TclError:  # pragma: no cover - Zeile wird gerade zerstört
            return
        if width <= 1:
            return
        self.title_label.configure(text=_fit_text(self, self._title_text, font("row_title"), width))
        if self.sub_label is None:
            return
        used = 0
        if self.status_dot is not None:
            used = px(StatusDot.SIZE_ROW) + px(Space.ICON_GAP)
        wrap = max(1, width - used)
        if int(str(self.sub_label.cget("wraplength")) or 0) != wrap:
            self.sub_label.configure(wraplength=wrap)
        if self.sub_label.cget("text") != self._sub_text:
            self.sub_label.configure(text=self._sub_text)

    @property
    def sub_text(self) -> str:
        """Die vollständige Unterzeile, unabhängig vom Umbruch."""
        return self._sub_text

    def set_status(self, status: str, text: str) -> None:
        if self.status_dot is not None:
            self.status_dot.set_status(status)
        self._sub_text = text
        self._refit()

    def set_description(self, text: str) -> None:
        self._sub_text = text
        self._refit()

    def set_value(self, text: str) -> None:
        if self.value_label is not None:
            self.value_label.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        """Deaktiviert die Zeile — die Höhe bleibt, damit nichts springt."""
        self._enabled = enabled
        self.title_label.configure(fg=Theme.TEXT_PRIMARY if enabled else Theme.TEXT_DISABLED)
        if self.sub_label is not None:
            # Ein Status („Ollama erreichbar“) ist Auskunft, keine Bedienung: er bleibt lesbar wie er ist.
            informs = self.status_dot is not None
            self.sub_label.configure(fg=Theme.TEXT_TERTIARY if enabled or informs else Theme.TEXT_DISABLED)
        if self.value_label is not None:
            self.value_label.configure(fg=Theme.TEXT_SECONDARY if enabled else Theme.TEXT_DISABLED)
        if self._control is not None and hasattr(self._control, "set_enabled"):
            self._control.set_enabled(enabled)
        if not enabled:
            self._paint(Theme.GROUP_SURFACE)
        if self._area is not None:
            self._area.set_cursor("hand2" if enabled else "arrow")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------ Interaktion

    def _paint(self, colour: str) -> None:
        self._round_corners(colour)
        for widget in (self._line, self._text_box, self.title_label, self.sub_box, self.sub_label, self.value_label):
            if widget is not None:
                # Tk-Kinder erben keinen Hintergrund; jedes muss einzeln gesetzt
                # werden, sonst bleiben Textkästchen in der alten Farbe stehen.
                widget.configure(bg=colour)
        if self.status_dot is not None:
            self.status_dot.configure(bg=colour)
        if self._control is not None:
            # Das Control gehoert zur Zeile. Ohne diesen Schritt blieb es beim
            # Hover als Rechteck der alten Farbe stehen — am deutlichsten am
            # Schalter, dessen runde Spur den Untergrund durchscheinen laesst.
            set_surface_under(self._control, colour)

    def _on_enter(self, _event=None) -> None:
        if self._enabled and self._interactive:
            self._hovered = True
            self._paint(Theme.CONTROL_SURFACE)

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._paint(Theme.GROUP_SURFACE)

    #: Ecken der Hover-Fläche: (Ecke, place-Optionen). Die Fläche ist gerundet wie die Platte innen.
    _CORNERS = (
        ("nw", {"x": 0, "y": 0, "anchor": "nw"}),
        ("ne", {"relx": 1.0, "y": 0, "anchor": "ne"}),
        ("sw", {"x": 0, "rely": 1.0, "anchor": "sw"}),
        ("se", {"relx": 1.0, "rely": 1.0, "anchor": "se"}),
    )

    def _round_corners(self, colour: str) -> None:
        """Rundet die farbige Hover-Fläche: vier kleine Eckbilder in Plattenfarbe mit einem Viertelkreis.

        Die Zeile besteht aus rechteckigen Tk-Rahmen; ohne die Ecken stand der Hover als scharfes Rechteck in
        der runden Platte. Radius = Platte minus Einzug, damit beide Rundungen zueinander passen.
        """
        corners = getattr(self, "_corner_labels", None)
        if colour == Theme.GROUP_SURFACE:
            for label in corners or ():
                label.place_forget()
            return
        if corners is None:
            corners = self._corner_labels = []
            for _name, _options in self._CORNERS:
                label = tk.Label(self._line, bd=0, highlightthickness=0, padx=0, pady=0, bg=Theme.GROUP_SURFACE)
                if self._area is not None:
                    self._area.join(label)
                corners.append(label)
        radius = px(Radius.GROUP - Space.GROUP_INSET)
        for label, (name, options) in zip(corners, self._CORNERS, strict=True):
            image = corner_image(name, radius, colour, Theme.GROUP_SURFACE)
            label.configure(image=image, bg=Theme.GROUP_SURFACE)
            label.image = image
            label.place(**options)
            label.lift()

    def _on_press_look(self, pressed: bool) -> None:
        """Gedrückt eine Stufe dunkler als im Hover, wie ein Knopf."""
        if self._enabled and self._interactive:
            self._paint(
                Theme.CONTROL_HOVER if pressed else Theme.CONTROL_SURFACE if self._hovered else Theme.GROUP_SURFACE
            )

    def _on_hover(self, inside: bool) -> None:
        if inside:
            self._on_enter()
        else:
            self._on_leave()

    def _on_click(self) -> None:
        if self._enabled and self._command is not None:
            self._command()

    # ------------------------------------------------------------ Darstellung

    def _on_theme_change(self) -> None:
        """Zeilengrund und Textfarben neu setzen.

        `set_enabled()` ist hier der ehrlichste Weg: dort steht bereits, welche
        Textfarbe zu welchem Zustand gehört, und die Zeile weiss selbst, ob sie
        gerade abgeblendet ist. Danach entscheidet der Hover über die Fläche —
        sonst verlöre eine Zeile unter dem Zeiger beim Wechsel ihre Hervorhebung.
        """
        self.set_enabled(self._enabled)
        hovered = self._hovered and self._enabled and self._interactive
        self._paint(Theme.CONTROL_SURFACE if hovered else Theme.GROUP_SURFACE)

    # ------------------------------------------------------------ DPI

    def _on_scale_change(self, _factor: float) -> None:
        try:
            self._apply_metrics()
            self.title_label.configure(font=font("row_title"))
            if self.sub_label is not None:
                self.sub_label.configure(font=font("description"))
            if self.value_label is not None:
                self.value_label.configure(font=font("row_title"))
            self._refit()
            if self.hairline is not None:
                self.hairline.configure(height=max(1, px(1)))
                self.hairline.pack_configure(padx=px(Space.ROW_X))
        except tk.TclError:  # pragma: no cover
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            Scale.unregister(self._on_scale_change)


def corner_image(corner: str, radius: int, fill: str, outside: str):
    """Ein Eckstück: aussen die Plattenfarbe, innen ein Viertelkreis in `fill`, kantengeglättet (gecacht)."""
    from PIL import Image, ImageDraw, ImageTk

    from hams.ui_kit.caches import _img_cache

    key = ("corner", corner, radius, fill, outside)
    cached = _img_cache.fetch(key)
    if cached is not None:
        return cached
    scale = 4
    big = radius * scale
    image = Image.new("RGB", (big, big), outside)
    centre = {"nw": (big, big), "ne": (0, big), "sw": (big, 0), "se": (0, 0)}[corner]
    ImageDraw.Draw(image).ellipse([centre[0] - big, centre[1] - big, centre[0] + big, centre[1] + big], fill=fill)
    return _img_cache.store(key, ImageTk.PhotoImage(image.resize((radius, radius), Image.LANCZOS)))

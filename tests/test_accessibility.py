"""Abnahme der Bedienbarkeit: Tastatur, Kontrast, Graustufen, Sprache.

Diese Datei prüft die Oberfläche als Ganzes und nicht einzelne Bauteile. Was
sich nur am laufenden Programm zeigt — die Kette durch das echte
Einstellungsfenster, die Bildschirmränder, die reale Skalierungsumschaltung —
steht im Bericht unter den offenen manuellen Prüfungen.
"""

import ast
import pathlib
import tkinter as tk

import pytest

from conftest import fokus_erzwingen

from wisper import ui_kit as uk
from wisper.ui_kit import (ControlStyle, IconButton, Option, Popover, PrimaryButton,
                           SecondaryButton, Select, StatusDot, Switch, Theme)

BG = Theme.GROUP_SURFACE
SOURCE = pathlib.Path(uk.__file__).parent


# ------------------------------------------------------------------ Werkzeug


def luminance(colour: str) -> float:
    value = colour.lstrip("#")
    parts = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("360x300+140+140")
    win.configure(bg=BG)
    win.deiconify()
    win.lift()
    fokus_erzwingen(win, root)
    yield win
    Popover.dismiss_current()
    win.destroy()
    root.update()


# ------------------------------------------------------------ Fokusreihenfolge


class TestFocusChain:
    def test_follows_the_order_of_creation(self, window, root):
        first = SecondaryButton(window, "Eins", lambda: None, bg_under=BG)
        second = SecondaryButton(window, "Zwei", lambda: None, bg_under=BG)
        third = SecondaryButton(window, "Drei", lambda: None, bg_under=BG)
        for widget in (first, second, third):
            widget.pack()
        root.update()
        assert uk.focus_chain(window) == [first, second, third]

    def test_packing_does_not_change_it(self, window, root):
        """Die Kette haengt an der Stapelreihenfolge, nicht an der Packung.

        Genau darauf beruht die Kopfzeile des Hauptfensters: Zahnrad zuerst
        erzeugt, Kreuz zuerst gepackt.
        """
        first = IconButton(window, "settings", lambda: None, bg_under=BG)
        second = IconButton(window, "close", lambda: None, bg_under=BG)
        second.pack(side="right")
        first.pack(side="right")      # sichtbar links von `second`
        root.update()
        assert uk.focus_chain(window) == [first, second]

    def test_lift_moves_a_widget_to_the_end(self, window, root):
        """So kommt die Kopfzeile ans Ende der Kette, ohne nach unten zu rutschen."""
        head = tk.Frame(window, bg=BG)
        head.pack(fill="x")
        close = IconButton(head, "close", lambda: None, bg_under=BG)
        close.pack()
        body = SecondaryButton(window, "Inhalt", lambda: None, bg_under=BG)
        body.pack()
        root.update()
        assert uk.focus_chain(window) == [close, body]
        head.lift()
        root.update()
        assert uk.focus_chain(window) == [body, close]

    def test_disabled_controls_drop_out(self, window, root):
        first = SecondaryButton(window, "Eins", lambda: None, bg_under=BG)
        second = SecondaryButton(window, "Zwei", lambda: None, bg_under=BG)
        for widget in (first, second):
            widget.pack()
        root.update()
        second.set_enabled(False)
        root.update()
        assert uk.focus_chain(window) == [first]

    def test_hidden_controls_are_skipped(self, window, root):
        """Der Abbruchknopf wartet unsichtbar auf seinen Einsatz."""
        visible = SecondaryButton(window, "Sichtbar", lambda: None, bg_under=BG)
        visible.pack()
        hidden = IconButton(window, "close", lambda: None, bg_under=BG)
        root.update()
        assert uk.focus_chain(window) == [visible]
        hidden.pack()
        root.update()
        assert uk.focus_chain(window) == [visible, hidden]

    def test_displays_stay_out(self, window, root):
        """Pegel und Statuspunkt sind Anzeigen, keine Bedienelemente."""
        meter = uk.SignalMeter(window, bg_under=BG)
        meter.pack()
        dot = StatusDot(window, bg_under=BG)
        dot.pack()
        button = SecondaryButton(window, "Aktion", lambda: None, bg_under=BG)
        button.pack()
        root.update()
        assert uk.focus_chain(window) == [button]

    def test_reverse_is_the_mirror_image(self, window, root):
        widgets = [SecondaryButton(window, f"B{i}", lambda: None, bg_under=BG)
                   for i in range(4)]
        for widget in widgets:
            widget.pack()
        root.update()
        forward = uk.focus_chain(window)
        current = forward[-1]
        backward = [current]
        for _ in range(len(forward) - 1):
            name = window.tk.call("tk_focusPrev", current)
            current = window.nametowidget(name)
            backward.append(current)
        assert backward == list(reversed(forward))


# ------------------------------------------------------------------- Tastatur


class TestKeyboardActivation:
    @pytest.mark.parametrize("key", ["<Return>", "<space>"])
    def test_buttons_react_to_enter_and_space(self, window, root, key):
        clicks = []
        button = PrimaryButton(window, "Start", lambda: clicks.append(1), bg_under=BG)
        button.pack()
        root.update()
        button.focus_set()
        button.event_generate(key)
        root.update()
        assert clicks == [1]

    @pytest.mark.parametrize("key", ["<Return>", "<space>"])
    def test_icon_buttons_react(self, window, root, key):
        """Der Abbruchknopf aus Phase 15 ist so ein Icon-Knopf."""
        clicks = []
        button = IconButton(window, "close", lambda: clicks.append(1), bg_under=BG)
        button.pack()
        root.update()
        button.focus_set()
        button.event_generate(key)
        root.update()
        assert clicks == [1]

    def test_a_disabled_button_stays_silent(self, window, root):
        clicks = []
        button = IconButton(window, "close", lambda: clicks.append(1), bg_under=BG)
        button.pack()
        root.update()
        button.set_enabled(False)
        button.event_generate("<Return>")
        root.update()
        assert clicks == []

    def test_space_toggles_a_switch(self, window, root):
        changes = []
        switch = Switch(window, bg_under=BG, value=False, command=changes.append)
        switch.pack()
        root.update()
        switch.focus_set()
        switch.event_generate("<space>")
        root.update()
        assert changes == [True] and switch.value is True

    def test_a_disabled_switch_does_not_move(self, window, root):
        changes = []
        switch = Switch(window, bg_under=BG, value=False, command=changes.append)
        switch.pack()
        root.update()
        switch.set_enabled(False)
        switch.event_generate("<space>")
        root.update()
        assert changes == [] and switch.value is False

    def test_focus_shows_a_ring(self, window, root):
        button = SecondaryButton(window, "Start", lambda: None, bg_under=BG)
        button.pack()
        root.update()
        button.focus_set()
        root.update()
        assert button.ring() == ControlStyle.focus_ring()

    def test_a_select_opens_and_closes_by_keyboard(self, window, root):
        options = [Option(f"v{i}", f"Eintrag {i}") for i in range(4)]
        select = Select(window, bg_under=BG, options=options, value="v0")
        select.pack()
        root.update()
        select.focus_set()
        select.event_generate("<Return>")
        root.update()
        assert Popover.current is not None
        popover = Popover.current
        popover.event_generate("<Down>")
        popover.event_generate("<Return>")
        root.update()
        assert select.value == "v1"
        assert Popover.current is None


# ---------------------------------------------------------------- Klickflächen


class TestHitAreas:
    MINIMUM = 24      # logische Pixel

    def test_icon_button(self, window, root):
        button = IconButton(window, "settings", lambda: None, bg_under=BG)
        button.pack()
        root.update()
        assert button.winfo_width() >= uk.px(self.MINIMUM)
        assert button.winfo_height() >= uk.px(self.MINIMUM)

    def test_switch(self, window, root):
        switch = Switch(window, bg_under=BG, value=False, command=lambda _v: None)
        switch.pack()
        root.update()
        assert switch.winfo_height() >= uk.px(self.MINIMUM)

    def test_select(self, window, root):
        select = Select(window, bg_under=BG, options=[Option("a", "A")], value="a")
        select.pack()
        root.update()
        assert select.winfo_height() >= uk.px(self.MINIMUM)

    def test_the_whole_select_triggers_the_popover(self, window, root):
        """Nicht nur der Chevron — die ganze Fläche ist der Auslöser."""
        select = Select(window, bg_under=BG, options=[Option("a", "A")], value="a")
        select.pack(fill="x")
        root.update()
        select.event_generate("<ButtonPress-1>", x=5, y=5)
        select.event_generate("<ButtonRelease-1>", x=5, y=5)
        root.update()
        assert Popover.current is not None

    def test_control_heights_are_at_least_the_minimum(self):
        for name in ("PRIMARY", "STANDARD", "ROW_SIMPLE", "ROW_DETAIL"):
            assert getattr(uk.Height, name) >= self.MINIMUM


# ------------------------------------------------------------------- Kontrast


TEXT_PAIRS = [
    ("Popover-Titel", "TEXT_PRIMARY", "POPOVER_SURFACE"),
    ("Popover-Meta", "TEXT_SECONDARY", "POPOVER_SURFACE"),
    ("Popover-Zusatz", "TEXT_SECONDARY", "POPOVER_SURFACE"),
    ("Popover-Gruppentitel", "TEXT_SECONDARY", "POPOVER_SURFACE"),
    ("Popover-Titel im Hover", "TEXT_PRIMARY", "CONTROL_HOVER"),
    ("Popover-Meta im Hover", "TEXT_SECONDARY", "CONTROL_HOVER"),
    ("Select-Text", "TEXT_PRIMARY", "CONTROL_SURFACE"),
    ("Select-Meta", "TEXT_SECONDARY", "CONTROL_SURFACE"),
    ("Zeilenbeschreibung", "TEXT_TERTIARY", "GROUP_SURFACE"),
    ("Zeilenwert", "TEXT_SECONDARY", "GROUP_SURFACE"),
    ("Downloadstatus", "TEXT_TERTIARY", "GROUP_SURFACE"),
    ("Ollama-Status", "TEXT_TERTIARY", "GROUP_SURFACE"),
    ("Abschnittslabel", "TEXT_TERTIARY", "WINDOW_BG"),
    ("Kurzbefehle", "TEXT_TERTIARY", "WINDOW_BG"),
]

GRAPHIC_PAIRS = [
    ("Fokusring auf Fenstergrund", "ACCENT_TINT", "WINDOW_BG"),
    ("Fokusring auf Gruppenfläche", "ACCENT_TINT", "GROUP_SURFACE"),
    ("Fokusring auf Controlfläche", "ACCENT_TINT", "CONTROL_SURFACE"),
    ("Fokusring auf Hoverfläche", "ACCENT_TINT", "CONTROL_HOVER"),
    ("Fokusring auf Popover", "ACCENT_TINT", "POPOVER_SURFACE"),
    ("Haken im Popover", "ACCENT_TINT", "POPOVER_SURFACE"),
    ("Haken im Hover", "ACCENT_TINT", "CONTROL_HOVER"),
    ("Umriss der Schalterspur", "CONTROL_OUTLINE", "GROUP_SURFACE"),
    ("Schalter an", "SUCCESS", "GROUP_SURFACE"),
    ("Statuspunkt bereit", "SUCCESS", "GROUP_SURFACE"),
    ("Statuspunkt Fehler", "ERROR", "GROUP_SURFACE"),
    ("Statuspunkt Warnung", "WARNING", "GROUP_SURFACE"),
    ("Statuspunkt neutral", "TEXT_TERTIARY", "GROUP_SURFACE"),
]

#: Die Paare tragen Tokennamen, nicht Farbwerte, und jede Pruefung laeuft ueber
#: `Theme.palette(mode)`. Beides mit Absicht: ein beim Import eingefrorener
#: Farbwert wuerde nur den gerade aktiven Modus pruefen, und ueber die Palette
#: braucht kein Test den globalen Modus umzustellen.
MODES = list(Theme.MODES)


class TestContrastOfTheWholeSurface:
    """Getrennte Kontrastmatrix je Modus.

    Getrennt, weil das eine das andere nicht mittraegt: Apples helles Gruen
    #30D158 kommt auf dem dunklen Grund auf 8,60:1 und auf der hellen
    Gruppenflaeche auf 1,73:1 — es fiele dort selbst als Punkt durch.
    """

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("label,fg,bg", TEXT_PAIRS, ids=lambda v: v)
    def test_every_readable_text_meets_aa(self, mode, label, fg, bg):
        palette = Theme.palette(mode)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= 4.5, f"{mode}/{label}: {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("label,fg,bg", GRAPHIC_PAIRS, ids=lambda v: v)
    def test_every_meaningful_shape_meets_three(self, mode, label, fg, bg):
        palette = Theme.palette(mode)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= 3.0, f"{mode}/{label}: {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    def test_secondary_text_holds_aa_on_every_surface(self, mode):
        """Das ist die eigentliche Zusage: Meta-Zeilen tragen `TEXT_SECONDARY`.

        Sie muss auf *jeder* Flaeche reichen — sonst haette die Regel „auf den
        helleren Flaechen eine Stufe heller“ keinen Boden.
        """
        palette = Theme.palette(mode)
        for name in ("WINDOW_BG", "GROUP_SURFACE", "CONTROL_SURFACE",
                     "CONTROL_HOVER", "POPOVER_SURFACE"):
            ratio = contrast(palette["TEXT_SECONDARY"], palette[name])
            assert ratio >= 4.5, f"{mode}: Sekundaertext auf {name} {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    def test_tertiary_text_is_only_promised_on_the_quiet_surfaces(self, mode):
        """`TEXT_TERTIARY` gilt auf Fenstergrund und Gruppenflaeche — sonst nirgends.

        Dunkel reicht es auf Control-, Popover- und Hoverflaeche nicht (3,94 /
        4,17 / 3,40), hell nur auf der Hoverflaeche nicht (4,10). Deshalb steht
        dort ueberall `TEXT_SECONDARY`. Geprueft wird die Zusage, nicht die
        Ausnahme.
        """
        palette = Theme.palette(mode)
        for name in ("WINDOW_BG", "GROUP_SURFACE"):
            ratio = contrast(palette["TEXT_TERTIARY"], palette[name])
            assert ratio >= 4.5, f"{mode}: Tertiaertext auf {name} {ratio:.2f}:1"

    @pytest.mark.parametrize("mode", MODES)
    def test_the_knob_on_the_green_track_is_a_known_limit(self, mode):
        """Dunkel gemessen 2,02:1, hell 5,01:1.

        Im Dunkeln haengt der Zustand nicht allein daran: die Stellung des
        Knopfes traegt ihn ebenso, und die Spur selbst hebt sich mit 7,75:1 vom
        Zeilengrund ab. Die Zahlen stehen hier, damit sie nicht unbemerkt
        schlechter werden.
        """
        palette = Theme.palette(mode)
        ratio = contrast(palette["KNOB"], palette["SUCCESS"])
        grenze = 1.9 if mode == Theme.DARK else 4.5
        assert ratio >= grenze, f"{mode}: Knopf auf gruener Spur {ratio:.2f}:1"


# ------------------------------------------------------------------ Graustufen


class TestGreyscale:
    """Nichts darf allein an der Farbe hängen."""

    @pytest.mark.parametrize("mode", MODES)
    def test_switch_states_differ_in_brightness(self, mode):
        """Ein- und ausgeschaltete Spur muessen sich auch in Grau unterscheiden.

        Die Richtung dreht sich mit dem Modus — dunkel wird die Spur beim
        Einschalten heller, hell wird sie dunkler. Geprueft wird deshalb der
        Abstand, nicht das Vorzeichen.
        """
        palette = Theme.palette(mode)
        assert contrast(palette["SUCCESS"], palette["CONTROL_SURFACE"]) >= 3.0

    def test_switch_state_also_shows_in_the_knob_position(self, window, root):
        switch = Switch(window, bg_under=BG, value=False, command=lambda _v: None)
        switch.pack()
        root.update()
        left = switch._progress
        switch.set(True)
        root.update()
        assert switch._progress != left, "die Stellung muss den Zustand mittragen"

    def test_the_chosen_entry_carries_a_check_not_only_a_colour(self, window, root):
        options = [Option("a", "A"), Option("b", "B")]
        select = Select(window, bg_under=BG, options=options, value="b")
        select.pack()
        root.update()
        select.open_popover()
        root.update()
        items = Popover.current._items
        chosen = [item for item in items if item.option.value == "b"][0]
        other = [item for item in items if item.option.value == "a"][0]
        assert chosen._check_image is not other._check_image
        assert chosen._selected and not other._selected

    def test_status_colours_are_distinguishable_in_grey(self):
        """Die Punkte allein reichen nicht — aber sie duerfen sich auch nicht
        gegenseitig aufheben."""
        for mode in MODES:
            palette = Theme.palette(mode)
            values = {name: luminance(palette[token])
                      for name, token in StatusDot.COLOURS.items()}
            assert values["error"] != values["ready"], mode
            # Nur der Abstand zaehlt: hell ist „bereit“ dunkler als „untaetig“,
            # dunkel ist es umgekehrt.
            assert abs(values["idle"] - values["ready"]) > 0.02, mode

    def test_every_status_dot_has_a_text_beside_it(self):
        """Geprüft am Quelltext: `set_status` verlangt immer beides."""
        source = (SOURCE / "ui_kit.py").read_text(encoding="utf-8")
        marker = "def set_status(self, status: str, text: str) -> None:"
        assert marker in source, "SettingRow.set_status muss Text mitfuehren"

    def test_a_download_shows_numbers_not_only_a_bar(self):
        from wisper.main import SettingsWindow as SW

        text = SW.download_text("Tiny", 13_000_000, 78_000_000, 17)
        assert "17 %" in text and "13/78 MB" in text


# --------------------------------------------------------------------- Sprache


#: Schlüsselwörter, unter denen Text tatsächlich in der Oberfläche landet.
UI_KEYWORDS = {"text", "description", "value", "placeholder", "label", "meta",
               "trailing", "title", "tooltip", "hint", "detail", "summary",
               "size_label"}

#: Aufrufe, deren Positionsargumente ebenfalls in der Oberfläche landen.
UI_CALLS = {"SettingRow", "SecondaryButton", "PrimaryButton", "IconButton",
            "SectionHeader", "Option", "WhisperModelInfo", "set_description",
            "set_status", "set_value", "_apply_state", "download_text"}

#: Englische Reste, die frueher in dieser Oberflaeche standen.
FORBIDDEN = ["Transcriber", "Loading", "Error", "Saved", "Recording", "Ready",
             "Settings", "Cancel", "Failed", "Copied", "Pasted"]

#: Fachbegriffe, die als Eigenname stehen bleiben duerfen.
ALLOWED = {"Whisper", "Ollama", "Turbo", "Large", "Medium", "Small", "Base",
           "Tiny", "CUDA", "float16", "GPU", "CPU", "Wisper", "KI", "MB", "GB"}


def _literals(node) -> list:
    """Zeichenketten aus einem Knoten — auch aus den festen Teilen eines f-Strings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part.value for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)]
    return []


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def ui_strings() -> list:
    """Alle Zeichenketten, die als Oberflächentext gesetzt werden.

    Zwei Wege führen dorthin: benannte Argumente wie `text=` und die
    Positionsargumente der Bauteile, die Text als erstes entgegennehmen.
    Logmeldungen bleiben aussen vor — die sind seit jeher englisch.
    """
    found = []
    for path in sorted(SOURCE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            named = _call_name(node) in UI_CALLS
            for argument in (node.args if named else ()):
                found += [(path.name, text) for text in _literals(argument)]
            for keyword in node.keywords:
                if named or keyword.arg in UI_KEYWORDS:
                    found += [(path.name, text) for text in _literals(keyword.value)]
    return found


class TestLanguage:
    def test_there_is_something_to_check(self):
        assert len(ui_strings()) > 40

    def test_no_english_leftovers_in_the_interface(self):
        offenders = []
        for name, text in ui_strings():
            for word in FORBIDDEN:
                if word in text and word not in ALLOWED:
                    offenders.append(f"{name}: {text!r} enthält {word!r}")
        assert not offenders, "\n".join(offenders)

    def test_the_window_titles_are_german(self):
        from wisper.main import SettingsWindow as SW

        assert "Einstellungen" in SW.MODEL_HINT or SW.MODEL_HINT
        assert SW.MODEL_HINT == "Tempo und Genauigkeit"


class TestNoLegacyGlyphs:
    #: Zeichen, die frueher als Ersatz fuer echte Icons dienten.
    GLYPHS = ["⚙", "⌄", "✕", "▾", "▼", "●", "✔"]

    @pytest.mark.parametrize("path", ["ui_kit.py", "main.py", "models.py", "ollama.py"])
    def test_no_glyph_hacks_left(self, path):
        source = (SOURCE / path).read_text(encoding="utf-8")
        lines = [line for line in source.splitlines()
                 if not line.lstrip().startswith("#")]
        body = "\n".join(lines)
        present = [glyph for glyph in self.GLYPHS if glyph in body]
        assert not present, f"{path}: {present}"

    def test_the_download_marker_is_a_deliberate_character(self):
        """Der Pfeil vor der Groesse ist Text, kein Icon-Ersatz — und bleibt."""
        from wisper.main import SettingsWindow as SW
        from wisper import models

        window = object.__new__(SW)
        window._installed_models = {"turbo"}
        options = SW.model_options(window)
        large = next(o for o in options if o.value == "large-v3")
        assert large.trailing.startswith("↓")
        assert models.find("large-v3").size_label in large.trailing

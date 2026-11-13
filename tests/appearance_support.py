"""Gemeinsame Fixtures der Darstellungstests (test_appearance.py, test_appearance_app.py).

Nicht in conftest.py: `restore_mode` ist autouse und soll nur für diese beiden Dateien gelten.
"""

import tkinter as tk

import pytest
from conftest import force_focus

from hams import ui_kit as uk
from hams.ui_kit import (
    GroupSurface,
    IconButton,
    Option,
    Popover,
    ScrollArea,
    SecondaryButton,
    SectionHeader,
    Select,
    SettingRow,
    SignalMeter,
    Switch,
    Theme,
)

MODES = list(Theme.MODES)


@pytest.fixture(autouse=True)
def restore_mode():
    """Kein Test darf den Modus für den nächsten stehen lassen.

    Die anderen Testdateien lesen ihre Farben teils beim Import; ein hier
    hängengebliebener Modus verschöbe sie unbemerkt.
    """
    before = Theme.mode
    yield
    Theme.apply(before)


@pytest.fixture
def window(root):
    win = tk.Toplevel(root)
    win.geometry("420x420+130+130")
    win.configure(bg=Theme.WINDOW_BG)
    win.deiconify()
    win.lift()
    force_focus(win, root)
    yield win
    win.destroy()
    root.update()


@pytest.fixture
def surface(window, root):
    """Ein Ausschnitt, der jedes Bauteil einmal enthält — wie die Einstellungen.

    Bewusst aus denselben Bausteinen zusammengesetzt wie das echte Fenster:
    Rollbereich, Abschnitt, Gruppenfläche, Zeilen mit Schalter und Auswahlfeld,
    Schaltflächen, Statuspunkt und Pegel. Das vollständige `SettingsWindow`
    liesse sich ohne Audio und Tray nicht bauen.
    """
    teile = {}
    scroll = ScrollArea(window, bg_under=Theme.WINDOW_BG)
    scroll.pack(fill="both", expand=True)
    teile["scroll"] = scroll

    teile["section"] = SectionHeader(scroll.body, "Allgemein", bg_under=Theme.WINDOW_BG)
    teile["section"].pack(fill="x")

    group = GroupSurface(scroll.body, bg_under=Theme.WINDOW_BG)
    group.pack(fill="x")
    teile["group"] = group

    row = SettingRow(group.body, "Sound", variant="description", description="Kurzer Ton", interactive=True)
    row.pack(fill="x")
    teile["row"] = row
    switch = Switch(group.body, bg_under=Theme.GROUP_SURFACE, value=True, command=lambda _v: None)
    row.set_control(switch)
    teile["switch"] = switch

    status_row = SettingRow(group.body, "Mikrofon", variant="status")
    status_row.pack(fill="x")
    teile["status_row"] = status_row
    teile["dot"] = status_row.status_dot

    select_row = SettingRow(
        group.body, "Darstellung", variant="description", description="Hell oder dunkel", hairline=False
    )
    select_row.pack(fill="x")
    select = Select(
        group.body,
        bg_under=Theme.GROUP_SURFACE,
        options=[Option("light", "Hell"), Option("dark", "Dunkel")],
        value="light",
    )
    # Wie im echten Fenster: ohne feste logische Breite bliebe das Auswahlfeld
    # bei seiner Eigenbreite von 2 px stehen — es traegt nur ein Bild, keinen Text.
    uk.keep_logical_width(select, 150)
    select_row.set_control(select)
    teile["select"] = select

    teile["primary"] = SecondaryButton(scroll.body, "Aufnahme", lambda: None, bg_under=Theme.WINDOW_BG, role="primary")
    teile["primary"].pack(fill="x")
    teile["secondary"] = SecondaryButton(
        scroll.body, "Abbrechen", lambda: None, bg_under=Theme.WINDOW_BG, icon_name="stop"
    )
    teile["secondary"].pack(fill="x")
    teile["icon_button"] = IconButton(scroll.body, "settings", lambda: None, bg_under=Theme.WINDOW_BG)
    teile["icon_button"].pack()
    teile["icon_button"].set_hover_colour(Theme.ERROR)
    teile["meter"] = SignalMeter(scroll.body, bg_under=Theme.WINDOW_BG)
    teile["meter"].pack(fill="x")
    teile["meter"].set_level(0.7)

    for _ in range(3):
        root.update_idletasks()
        root.update()
    teile["window"] = window
    yield teile


def wechsel(window, mode, root):
    """Modus setzen und den Baum einfärben — genau wie die Anwendung es tut."""
    Popover.dismiss_current()
    Theme.apply(mode)
    besucht = uk.apply_theme_tree(window)
    root.update()
    return besucht

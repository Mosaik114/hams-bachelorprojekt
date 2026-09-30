"""Regressionstests des Tray-Menüs.

Der Fehler aus `4a34fed`: der Rückruf eines Verlaufseintrags wurde als

    lambda _icon=None, _item=None, i=idx: ...

geschrieben. pystray liest die Zahl der Positionsparameter aus `co_argcount`
und lässt nur 0, 1 oder 2 zu — Vorgabewerte zählen mit, also drei, also
`ValueError`. Sobald ein Transkript im Verlauf stand, liess sich das Menü nicht
mehr bauen.
"""

import threading

import pystray
import pytest

from wisper.main import FloatingTranscriberApp as App


class _FakeIcon:
    def __init__(self):
        self.menu = None
        self.title = ""
        self.updates = 0

    def update_menu(self):
        self.updates += 1


@pytest.fixture
def app(monkeypatch):
    """Anwendung ohne Fenster, Modell und echtes Tray."""
    instance = App.__new__(App)
    instance._history = []
    instance._history_lock = threading.Lock()
    instance.tray_icon = _FakeIcon()
    instance.is_recording = False
    instance._prompt_mode = False
    instance.model_ready = threading.Event()
    instance.model_ready.set()
    instance.copied = []
    monkeypatch.setattr(App, "_copy_history_entry",
                        lambda self, index: self.copied.append(index))
    monkeypatch.setattr(App, "_toggle_visibility", lambda self, icon=None, item=None: None)
    monkeypatch.setattr(App, "_open_settings", lambda self, icon=None, item=None: None)
    monkeypatch.setattr(App, "_quit_app", lambda self, icon=None, item=None: None)
    return instance


def entries(menu) -> list:
    """Die Beschriftungen eines pystray-Menüs, eine Ebene tief."""
    return [item.text for item in menu.items]


def history_submenu(menu):
    for item in menu.items:
        if item.text == "Letzte Transkripte":
            return item._action
    raise AssertionError("Verlaufsuntermenü fehlt")


class TestBuilding:
    def test_an_empty_history_builds(self, app):
        menu = app._build_tray_menu()
        assert "(leer)" in entries(history_submenu(menu))

    def test_one_entry_builds(self, app):
        app._history = [("12:00:00", "Ein Satz", "Ein Satz.")]
        menu = app._build_tray_menu()
        assert entries(history_submenu(menu)) == ["12:00:00  —  Ein Satz"]

    def test_several_entries_build(self, app):
        app._history = [(f"12:0{i}:00", f"Satz {i}", f"Satz {i}.") for i in range(5)]
        assert len(entries(history_submenu(app._build_tray_menu()))) == 5

    def test_the_regression_itself(self, app):
        """Genau der Fall, der frueher ValueError warf."""
        app._history = [("12:00:00", "Erster", "Erster.")]
        app._build_tray_menu()      # darf nicht mehr fliegen

    def test_special_characters_survive(self, app):
        app._history = [("12:00:00", "Grüße & „Anführung“ — 100 %", "…")]
        labels = entries(history_submenu(app._build_tray_menu()))
        assert "Grüße" in labels[0] and "100 %" in labels[0]

    def test_a_long_preview_is_accepted(self, app):
        app._history = [("12:00:00", "x" * 500, "x" * 5000)]
        labels = entries(history_submenu(app._build_tray_menu()))
        assert labels[0].endswith("x")

    def test_the_fixed_items_are_all_there(self, app):
        labels = entries(app._build_tray_menu())
        assert "Zeigen / Verstecken" in labels
        assert "Einstellungen …" in labels
        assert "Letzte Transkripte" in labels
        assert "Beenden" in labels


class TestCallbacks:
    def test_every_callback_satisfies_pystray(self, app):
        """pystray prueft die Signatur beim Anlegen — das ist der Kern."""
        app._history = [(f"12:0{i}:00", f"Satz {i}", "…") for i in range(3)]
        for item in history_submenu(app._build_tray_menu()).items:
            assert callable(item._action)

    def test_the_action_has_exactly_two_parameters(self, app):
        action = app._history_action(0)
        assert action.__code__.co_argcount == 2

    def test_pystray_accepts_the_action(self, app):
        pystray.MenuItem("Test", app._history_action(0))      # kein ValueError

    def test_pystray_would_still_refuse_the_old_lambda(self):
        """Damit der Grund dokumentiert bleibt, nicht nur die Loesung."""
        broken = lambda _icon=None, _item=None, i=0: None      # noqa: E731
        assert broken.__code__.co_argcount == 3
        with pytest.raises(ValueError):
            pystray.MenuItem("Test", broken)

    def test_clicking_copies_the_right_entry(self, app):
        app._history = [(f"12:0{i}:00", f"Satz {i}", f"Voll {i}") for i in range(3)]
        items = history_submenu(app._build_tray_menu()).items
        items[2](None)      # pystray ruft das MenuItem mit dem Icon auf
        assert app.copied == [2]

    def test_each_entry_keeps_its_own_index(self, app):
        """Die klassische Schleifenfalle: alle Rueckrufe zeigten auf den letzten."""
        app._history = [(f"12:0{i}:00", f"Satz {i}", f"Voll {i}") for i in range(4)]
        items = history_submenu(app._build_tray_menu()).items
        for item in items:
            item(None)
        assert app.copied == [0, 1, 2, 3]


class TestRobustness:
    def test_a_broken_entry_costs_only_its_line(self, app):
        app._history = [("12:00:00", "gut", "gut"), "kaputt",
                        ("12:02:00", "auch gut", "auch gut")]
        labels = entries(history_submenu(app._build_tray_menu()))
        assert labels == ["12:00:00  —  gut", "12:02:00  —  auch gut"]

    def test_only_broken_entries_still_give_a_menu(self, app):
        app._history = [None, 42]
        assert "(leer)" in entries(history_submenu(app._build_tray_menu()))

    def test_a_click_beyond_the_history_is_harmless(self, app, monkeypatch):
        monkeypatch.undo()
        app._history = []
        app._copy_history_entry(7)      # darf nichts tun und nicht werfen


class TestRefresh:
    def test_refreshing_replaces_the_menu(self, app):
        app._history = [("12:00:00", "Satz", "Satz.")]
        app._refresh_tray()
        assert app.tray_icon.menu is not None
        assert app.tray_icon.updates == 1

    def test_repeated_refreshes(self, app):
        for index in range(10):
            app._history.insert(0, (f"12:0{index}:00", f"Satz {index}", "…"))
            app._refresh_tray()
        assert app.tray_icon.updates == 10
        assert len(entries(history_submenu(app.tray_icon.menu))) == 10

    def test_history_grows_during_the_session(self, app, monkeypatch):
        monkeypatch.setattr(App, "_ui_call", lambda self, fn: fn())
        for index in range(7):
            app._add_to_history(f"Transkript Nummer {index}")
        assert len(app._history) == 5, "der Verlauf haelt fuenf Eintraege"
        labels = entries(history_submenu(app.tray_icon.menu))
        assert len(labels) == 5
        assert "Transkript Nummer 6" in labels[0]

    def test_a_failing_build_does_not_escape(self, app, monkeypatch):
        """Ein Fehler gehoert ins Log, nicht in den Tk-Rueckruf."""
        monkeypatch.setattr(App, "_build_tray_menu",
                            lambda self: (_ for _ in ()).throw(RuntimeError("kaputt")))
        app._refresh_tray()      # darf nicht fliegen

    def test_without_a_tray_nothing_happens(self, app):
        del app.tray_icon
        app._refresh_tray()

    def test_the_tooltip_names_the_last_entry(self, app):
        app._history = [("12:00:00", "Der letzte Satz", "…")]
        assert "Der letzte Satz" in app._tray_tooltip()

    def test_the_tooltip_without_history(self, app):
        assert app._tray_tooltip().endswith("bereit")

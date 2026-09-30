"""Warum ein verlorener Tastaturhook einen neuen Prozess braucht.

Diese Datei haelt den Befund fest, der den Neustart-Knopf begruendet. Sie
startet ausdruecklich *keinen* Lauschthread und setzt keinen Hook — sie liest
die Mechanik aus dem Quelltext der `keyboard`-Bibliothek. Ein Test, der einen
echten Low-Level-Hook installiert, haenge sich an die Tastatur des ganzen
Rechners, nur um eine Eigenschaft zu zeigen, die im Quelltext steht.

Der Befund, gemessen am 03.09.2026 mit keyboard 0.13.5:

* `add_hotkey()` ruft `start_if_necessary()`, und das tut nichts, solange
  `listening` True ist. Nach einem `unhook_all()` bleibt `listening` True.
  Ein erneutes Registrieren setzt den Hook also **nicht** neu.
* Der Lauschthread dreht eine `GetMessage`-Schleife. Entfernt Windows den
  Hook (etwa nach `LowLevelHooksTimeout`), laeuft die Schleife weiter und der
  Thread wirkt gesund — es kommen nur nie wieder Tastenereignisse an. Eine
  Pruefung auf „Thread lebt" gaebe also falsche Entwarnung.
* Ein Erzwingen ueber `listening = False` startet zwar neue Threads, laesst
  die alten aber fuer immer laufen. Nach mehreren Versuchen haengen mehrere
  Nachrichtenschleifen im Prozess.

Deshalb: neuer Prozess.
"""

import inspect

import keyboard
import keyboard._generic
import keyboard._winkeyboard


class TestWhyReRegisteringCannotHelp:
    def test_the_listener_only_initialises_while_it_is_not_listening(self):
        quelle = inspect.getsource(keyboard._generic.GenericListener.start_if_necessary)
        assert "if not self.listening:" in quelle
        assert "self.init()" in quelle

    def test_unhooking_everything_leaves_the_listener_listening(self):
        """Der Kern des Problems: `unhook_all` raeumt Handler, nicht den Hook."""
        quelle = inspect.getsource(keyboard.unhook_all)
        assert "listening" not in quelle, (
            "Die Bibliothek setzt `listening` jetzt zurueck — dann liesse sich "
            "der Hook doch im Prozess erneuern und der Neustart-Knopf braeuchte "
            "eine neue Begruendung.")

    def test_adding_a_hotkey_goes_through_start_if_necessary(self):
        quelle = inspect.getsource(keyboard.hook)
        assert "start_if_necessary" in quelle or "add_handler" in quelle

    def test_there_is_no_public_way_to_reinstall_the_hook(self):
        oeffentlich = {name for name in dir(keyboard) if not name.startswith("_")}
        for erfunden in ("restart", "rehook", "reinstall_hook", "restart_listener"):
            assert erfunden not in oeffentlich


class TestWhyALivenessCheckWouldMislead:
    def test_the_listening_thread_is_a_plain_message_loop(self):
        """`GetMessage` laeuft weiter, auch wenn der Hook laengst entfernt ist.

        Darum meldet die Anwendung nicht „Kurzbefehle gestoert" — sie koennte
        es nicht wissen, und eine falsche Entwarnung waere schlimmer als keine.
        """
        quelle = inspect.getsource(keyboard._winkeyboard.listen)
        assert "GetMessage" in quelle
        assert "while" in quelle
        # Nichts darin prueft, ob der Hook noch steht.
        assert "SetWindowsHookEx" not in quelle

    def test_the_hook_is_installed_once_per_listener_start(self):
        quelle = inspect.getsource(keyboard._winkeyboard.prepare_intercept)
        assert "SetWindowsHookEx" in quelle


class TestTheApplicationDrawsTheRightConclusion:
    def test_the_restart_is_documented_as_the_repair(self):
        from wisper.main import FloatingTranscriberApp

        doku = FloatingTranscriberApp.restart_app.__doc__ or ""
        assert "Hook" in doku
        assert "listening" in doku

    def test_the_application_never_pokes_the_private_listener_state(self):
        """`listening = False` liesse die alten Threads fuer immer laufen."""
        from wisper import main as wm

        quelle = open(wm.__file__, encoding="utf-8").read()
        assert "_listener" not in quelle
        assert "listening =" not in quelle

"""Nothing may pile up when windows and popovers come and go.

Counted are the Tcl commands of the interpreter: every Python callback Tk knows (bindings, `after`, `command=`)
is one. A popover opened and closed twenty times, or a settings window built anew five times, must leave the
count where it was (a small margin for timers that are pending at that moment).
"""

from hams.ui_kit.popover import Popover

MARGIN = 10


def tcl_commands(root) -> int:
    """Callbacks Tk knows. Images (`pyimageN`) are left out: they live in the bounded image cache on purpose."""
    return sum(1 for name in root.tk.call("info", "commands") if not str(name).startswith("pyimage"))


def test_popovers_leave_nothing_behind(live_app):
    live_app._open_settings()
    live_app.root.update()
    live_app.settings_win.select_page(1)
    live_app.root.update()
    select = live_app.settings_win.transcription.model_select

    def cycle():
        select.open_popover()
        live_app.root.update()
        Popover.dismiss_current()
        live_app.root.update()

    cycle()  # caches fill once
    before = tcl_commands(live_app.root)
    for _ in range(20):
        cycle()
    assert tcl_commands(live_app.root) - before <= MARGIN


def test_rebuilding_the_settings_leaves_nothing_behind(live_app):
    def rebuild():
        live_app._open_settings()
        live_app.root.update()
        win = live_app.settings_win
        win.close()
        win.win.destroy()
        live_app.settings_win = None
        live_app.root.update()

    rebuild()
    before = tcl_commands(live_app.root)
    for _ in range(5):
        rebuild()
    assert tcl_commands(live_app.root) - before <= MARGIN

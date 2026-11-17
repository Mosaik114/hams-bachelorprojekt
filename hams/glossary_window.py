"""The glossary window: the words Hams spells right and its fixed corrections, edited in the app.

Before, "Edit" opened `glossary.toml` in a text editor. Now the window shows both lists and changes them through
`glossary.GlossaryFile`, which saves at once and keeps the file's comments and order: the file stays the one place
the glossary lives, and the next dictation uses it like any edit by hand. "Open as file" is still there for anyone
who prefers the text.

Same chrome as the settings (painted stage with mark, title and round cross, frameless, resizable). Each list is a
group: on top a row to add an entry (a text field takes any letters, "Hüseyin" as much as "عبد الله"), below it one
row per entry; a click on an entry loads it for editing, the bin deletes it. From `SEARCH_FROM` entries on, a search
field above both groups narrows them.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from typing import TYPE_CHECKING

from hams import branding, glossary
from hams import ui_kit as uk
from hams.config import logger
from hams.i18n import t
from hams.settings_sections import group
from hams.ui_kit import Theme
from hams.window_stage import Stage

if TYPE_CHECKING:
    from hams.app import FloatingTranscriberApp


class TextField(tk.Frame):
    """A one-line text field on a stone surface, with a quiet placeholder while it is empty.

    Plain Tk `Entry` inside: it takes any Unicode text and the system's input methods. The text uses the
    `content` font, which has the letters of every script the interface needs, so a name in Arabic reads as a
    whole even in a German interface.
    """

    def __init__(
        self,
        master: tk.Misc,
        placeholder: str,
        *,
        on_change: Callable[[], object] | None = None,
        on_submit: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(
            master,
            bg=Theme.CONTROL_SURFACE,
            highlightthickness=max(1, uk.px(1)),
            highlightbackground=Theme.CONTROL_OUTLINE,
            highlightcolor=Theme.FOCUS,
            takefocus=0,
        )
        self._placeholder = placeholder
        self._showing_placeholder = False
        self._on_change = on_change
        self.entry = tk.Entry(
            self,
            font=uk.font("content"),
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=Theme.CONTROL_SURFACE,
            fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY,
            justify=uk.text_justify(),
        )
        self.entry.pack(fill="x", padx=uk.px(uk.Space.MD), pady=uk.px(uk.Space.SM))
        self.entry.bind("<FocusIn>", self._focus_in, add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._show_placeholder(), add="+")
        self.entry.bind("<KeyRelease>", lambda _e: self._changed(), add="+")
        if on_submit is not None:
            self.entry.bind("<Return>", lambda _e: on_submit(), add="+")
        self._show_placeholder()

    @property
    def value(self) -> str:
        return "" if self._showing_placeholder else self.entry.get()

    def set(self, text: str) -> None:
        self._clear_placeholder()
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        if not text and self.focus_get() is not self.entry:
            self._show_placeholder()

    def focus_field(self) -> None:
        self.entry.focus_set()
        self.entry.icursor("end")

    def set_enabled(self, enabled: bool) -> None:
        self.entry.configure(state="normal" if enabled else "disabled")

    def _changed(self) -> None:
        if self._on_change is not None and not self._showing_placeholder:
            self._on_change()

    def _focus_in(self, _event=None) -> None:
        self._clear_placeholder()

    def _clear_placeholder(self) -> None:
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=Theme.TEXT_PRIMARY)

    def _show_placeholder(self) -> None:
        if self.entry.get() or self._showing_placeholder:
            return
        self._showing_placeholder = True
        self.entry.insert(0, self._placeholder)
        self.entry.configure(fg=Theme.TEXT_TERTIARY)

    def _on_theme_change(self) -> None:
        self.configure(bg=Theme.CONTROL_SURFACE, highlightbackground=Theme.CONTROL_OUTLINE, highlightcolor=Theme.FOCUS)
        self.entry.configure(
            bg=Theme.CONTROL_SURFACE,
            insertbackground=Theme.TEXT_PRIMARY,
            fg=Theme.TEXT_TERTIARY if self._showing_placeholder else Theme.TEXT_PRIMARY,
        )


class GlossaryWindow:
    """The glossary, editable: words and fixed corrections. One at a time (`app.open_glossary`)."""

    SIZE = (440, 620)  # logical pixels, like the settings
    MIN_SIZE = (400, 420)
    #: From this many entries on, a search field narrows both lists.
    SEARCH_FROM = 12

    def __init__(self, app: FloatingTranscriberApp, *, open_file: Callable[[], object] | None = None) -> None:
        self.app = app
        self._open_file = open_file or glossary.open_in_editor
        self.file: glossary.GlossaryFile | None = None
        #: The entry being edited: a word, or the "wrong" side of a correction; None while adding.
        self.editing_word: str | None = None
        self.editing_replacement: str | None = None
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.title(t("glossary.window_title", product=branding.PRODUCT_NAME))
        self.chrome = uk.frame_window(self.win, min_size=self.MIN_SIZE)
        app.watch_dpi(self.win)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.configure(bg=Theme.WINDOW_BG)
        self.win.geometry(f"{uk.px(self.SIZE[0])}x{uk.px(self.SIZE[1])}")
        self.win.bind("<Escape>", lambda _e: self.close())
        self._build()
        self.reload()
        self._show()

    # ---------------------------------------------------------- lifecycle

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _show(self) -> None:
        try:
            x, y = self.app.root.winfo_rootx(), self.app.root.winfo_rooty()
        except tk.TclError:  # pragma: no cover - the main window is going away
            x, y = 80, 80
        self.win.geometry(f"+{x + uk.px(48)}+{y + uk.px(48)}")
        self.win.deiconify()
        self.win.update_idletasks()
        uk.round_window_corners(self.win)
        self.focus()

    def focus(self) -> None:
        uk.bring_to_front(self.win)
        self.word_field.focus_field()

    def close(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.win.destroy()

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        Stage(self.win, t("glossary.title"), self.close, close_tooltip=t("glossary.close_tooltip")).pack(fill="x")
        footer = tk.Frame(self.win, bg=Theme.WINDOW_BG)
        footer.pack(side="bottom", fill="x", padx=uk.px(uk.Space.WINDOW), pady=uk.px(uk.Space.WINDOW))
        self.open_file_button = uk.SecondaryButton(
            footer, t("glossary.open_file"), self.open_as_file, bg_under=Theme.WINDOW_BG, height=uk.Height.STANDARD
        )
        self.open_file_button.pack(side=uk.mirror_side("right"))
        self.status = tk.Label(
            footer,
            text="",
            bg=Theme.WINDOW_BG,
            fg=Theme.TEXT_TERTIARY,
            font=uk.font("description"),
            anchor=uk.mirror_anchor("w"),
            justify=uk.text_justify(),
        )
        self.status.pack(
            side=uk.mirror_side("left"), fill="x", expand=True, padx=uk.mirror_pad((0, uk.px(uk.Space.MD)))
        )
        footer.bind("<Configure>", self._wrap_status, add="+")

        self.scroll = uk.ScrollArea(self.win, bg_under=Theme.WINDOW_BG, edges=True)
        self.scroll.pack(fill="both", expand=True, padx=(uk.px(uk.Space.WINDOW), uk.px(uk.ScrollArea.EDGE_GAP)))
        self.scroll.bind_keys(self.win)
        body = self.scroll.body

        self.search_box = tk.Frame(body, bg=Theme.WINDOW_BG)
        self.search_field = TextField(self.search_box, t("glossary.search"), on_change=self._render)
        self.search_field.pack(fill="x", padx=(0, uk.px(uk.Space.WINDOW - uk.ScrollArea.EDGE_GAP)))

        words = group(body, t("glossary.words.title"), first=True).body
        self.word_field = TextField(words, t("glossary.words.new"), on_submit=self.submit_word)
        self.word_button = self._button(words, t("glossary.add"), self.submit_word)
        self.word_cancel = self._cancel(words, self.cancel_word)
        self.row_word_input = self._input_row(
            words, t("glossary.words.hint"), [self.word_field], self.word_button, self.word_cancel
        )
        self.word_list = tk.Frame(words, bg=Theme.GROUP_SURFACE)
        self.word_list.pack(fill="x")

        replacements = group(body, t("glossary.replace.title")).body
        self.wrong_field = TextField(replacements, t("glossary.replace.wrong"), on_submit=self.submit_replacement)
        self.right_field = TextField(replacements, t("glossary.replace.right"), on_submit=self.submit_replacement)
        self.replace_button = self._button(replacements, t("glossary.add"), self.submit_replacement)
        self.replace_cancel = self._cancel(replacements, self.cancel_replacement)
        self.row_replace_input = self._input_row(
            replacements,
            t("glossary.replace.hint"),
            [self.wrong_field, self.right_field],
            self.replace_button,
            self.replace_cancel,
        )
        self.replace_list = tk.Frame(replacements, bg=Theme.GROUP_SURFACE)
        self.replace_list.pack(fill="x")
        self.win.bind("<FocusIn>", self._on_focus, add="+")

    @staticmethod
    def _button(master: tk.Misc, text: str, command) -> uk.SecondaryButton:
        button = uk.SecondaryButton(master, text, command, bg_under=Theme.GROUP_SURFACE, height=uk.Height.STANDARD)
        button.set_role("primary")
        return button

    @staticmethod
    def _cancel(master: tk.Misc, command) -> uk.IconButton:
        return uk.IconButton(master, "close", command, bg_under=Theme.GROUP_SURFACE, tooltip=t("glossary.cancel"))

    def _input_row(self, master: tk.Misc, hint: str, fields: list, button, cancel) -> uk.SettingRow:
        """ "Add" with the list's hint, below it the field(s) and the button (stacked like the settings' rows)."""
        row = uk.SettingRow(master, t("glossary.add"), variant="description", description=hint, stacked=True)
        row.pack(fill="x")
        box = tk.Frame(master, bg=Theme.GROUP_SURFACE, highlightthickness=0, takefocus=0)
        for field in fields:
            field.pack(in_=box, fill="x", pady=(0, uk.px(uk.Space.SM)))
            tk.Misc.lift(field, box)
        buttons = tk.Frame(box, bg=Theme.GROUP_SURFACE)
        buttons.pack(fill="x")
        button.pack(in_=buttons, side=uk.mirror_side("right"))
        tk.Misc.lift(button, buttons)
        row.cancel_slot = (cancel, buttons)
        row.set_control(box)
        return row

    def _wrap_status(self, event: tk.Event) -> None:
        width = event.width - self.open_file_button.winfo_reqwidth() - uk.px(uk.Space.MD)
        with contextlib.suppress(tk.TclError):
            self.status.configure(wraplength=max(1, width))

    # ---------------------------------------------------------- content

    def _on_focus(self, event: tk.Event) -> None:
        """Back in the window, perhaps from the file in an editor: changes made there show up."""
        if event.widget is self.win and self._file_stamp() != self._stamp:
            self.reload()

    @staticmethod
    def _file_stamp():
        try:
            return glossary.glossary_path().stat().st_mtime_ns
        except OSError:
            return None

    def reload(self) -> None:
        """Reads the file anew. A broken file is shown as such and never written."""
        self._stamp = self._file_stamp()
        try:
            self.file = glossary.GlossaryFile()
        except glossary.GlossaryUnreadable as exc:
            logger.warning("Glossary unreadable, editing disabled: %s", exc)
            self.file = None
            self.say(t("glossary.unreadable", path=glossary.glossary_path()))
        for field in (self.word_field, self.wrong_field, self.right_field):
            field.set_enabled(self.file is not None)
        for button in (self.word_button, self.replace_button):
            button.set_enabled(self.file is not None)
        self._render()

    @property
    def words(self) -> list[str]:
        return self.file.words if self.file is not None else []

    @property
    def replacements(self) -> list[tuple[str, str]]:
        return self.file.replacements if self.file is not None else []

    def _matches(self, *texts: str) -> bool:
        query = self.search_field.value.strip().casefold()
        return not query or any(query in text.casefold() for text in texts)

    def _render(self) -> None:
        """Both lists anew, narrowed by the search; the search itself only from `SEARCH_FROM` entries on."""
        many = len(self.words) + len(self.replacements) >= self.SEARCH_FROM
        if many and not self.search_box.winfo_manager():
            self.search_box.pack(fill="x", pady=(uk.px(uk.Space.MD), 0), before=self.scroll.body.pack_slaves()[0])
        elif not many and self.search_box.winfo_manager():
            self.search_field.set("")
            self.search_box.pack_forget()
        self._fill(
            self.word_list,
            [(word, self._edit_word, self._delete_word) for word in self.words if self._matches(word)],
            empty="glossary.words.empty" if not self.words else "glossary.no_match",
        )
        self._fill(
            self.replace_list,
            [
                (t("glossary.replace.row", wrong=wrong, right=right), self._edit_replacement, self._delete_replacement)
                for wrong, right in self.replacements
                if self._matches(wrong, right)
            ],
            empty="glossary.replace.empty" if not self.replacements else "glossary.no_match",
            keys=[wrong for wrong, right in self.replacements if self._matches(wrong, right)],
        )

    def _fill(self, frame: tk.Frame, entries: list, *, empty: str, keys: list | None = None) -> None:
        for child in frame.winfo_children():
            child.destroy()
        if not entries:
            row = uk.SettingRow(frame, t(empty), hairline=False)
            row.set_enabled(False)
            row.pack(fill="x")
            return
        for index, (title, edit, delete) in enumerate(entries):
            key = keys[index] if keys is not None else title
            row = uk.SettingRow(
                frame, title, interactive=True, command=lambda k=key, e=edit: e(k), hairline=index < len(entries) - 1
            )
            row.title_label.configure(font=uk.font("content"))
            row.pack(fill="x")
            row.set_control(
                uk.IconButton(
                    frame,
                    "trash",
                    lambda k=key, d=delete: d(k),
                    bg_under=Theme.GROUP_SURFACE,
                    tooltip=t("glossary.delete_tooltip"),
                )
            )
            row.entry_key = key

    def say(self, text: str) -> None:
        self.status.configure(text=text)

    def _save(self) -> bool:
        try:
            self.file.save()
        except OSError as exc:
            logger.warning("Glossary could not be saved: %s", type(exc).__name__)
            self.say(t("glossary.save_failed", path=self.file.path))
            return False
        self._stamp = self._file_stamp()
        self.say(t("glossary.saved"))
        return True

    # ---------------------------------------------------------- words

    def submit_word(self) -> None:
        word = self.word_field.value.strip()
        if self.file is None or not word:
            return
        if self.file.has_word(word, besides=self.editing_word):
            self.say(t("glossary.duplicate", word=word))
            return
        if self.editing_word is not None:
            self.file.change_word(self.editing_word, word)
        else:
            self.file.add_word(word)
        if self._save():
            self.cancel_word()
            self._render()

    def _edit_word(self, word: str) -> None:
        self.editing_word = word
        self.word_field.set(word)
        self.word_field.focus_field()
        self._editing(self.row_word_input, self.word_button, True)

    def cancel_word(self) -> None:
        self.editing_word = None
        self.word_field.set("")
        self._editing(self.row_word_input, self.word_button, False)

    def _delete_word(self, word: str) -> None:
        if self.file is None:
            return
        self.file.remove_word(word)
        if self.editing_word == word:
            self.cancel_word()
        self._save()
        self._render()

    # ---------------------------------------------------------- replacements

    def submit_replacement(self) -> None:
        wrong, right = self.wrong_field.value.strip(), self.right_field.value.strip()
        if self.file is None or not wrong or not right:
            return
        if self.file.has_replacement(wrong, besides=self.editing_replacement):
            self.say(t("glossary.duplicate", word=wrong))
            return
        self.file.set_replacement(wrong, right, old=self.editing_replacement)
        if self._save():
            self.cancel_replacement()
            self._render()

    def _edit_replacement(self, wrong: str) -> None:
        right = dict(self.replacements).get(wrong, "")
        self.editing_replacement = wrong
        self.wrong_field.set(wrong)
        self.right_field.set(right)
        self.right_field.focus_field()
        self._editing(self.row_replace_input, self.replace_button, True)

    def cancel_replacement(self) -> None:
        self.editing_replacement = None
        self.wrong_field.set("")
        self.right_field.set("")
        self._editing(self.row_replace_input, self.replace_button, False)

    def _delete_replacement(self, wrong: str) -> None:
        if self.file is None:
            return
        self.file.remove_replacement(wrong)
        if self.editing_replacement == wrong:
            self.cancel_replacement()
        self._save()
        self._render()

    # ---------------------------------------------------------- shared

    @staticmethod
    def _editing(row: uk.SettingRow, button: uk.SecondaryButton, editing: bool) -> None:
        """While an entry is edited, the button saves and a cross beside it cancels."""
        button.config(text=t("glossary.save" if editing else "glossary.add"))
        cancel, buttons = row.cancel_slot
        if editing:
            cancel.pack(in_=buttons, side=uk.mirror_side("right"), padx=uk.mirror_pad((0, uk.px(uk.Space.XS))))
            tk.Misc.lift(cancel, buttons)
        else:
            cancel.pack_forget()

    def open_as_file(self) -> None:
        """The fallback for whoever prefers the text: the file in the default editor."""
        try:
            self._open_file()
        except OSError as exc:
            logger.warning("Glossary could not be opened: %s", type(exc).__name__)
            self.say(t("settings.glossary.failed", path=glossary.glossary_path()))
            return
        self.say(t("settings.glossary.opened"))

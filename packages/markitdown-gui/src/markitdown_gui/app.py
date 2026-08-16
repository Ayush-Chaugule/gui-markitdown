# SPDX-FileCopyrightText: 2026-present Ayush Chaugule
#
# SPDX-License-Identifier: MIT
"""The actual GUI window: a small, fixed-size Mint-Y styled app (dark or
light, with a switchable accent color -- see theme.py) that picks a file,
converts it with the real MarkItDown class, and saves the result. Also owns
the (simulated -- MarkItDown has no real progress signal to drive it with)
progress bar animation, and opens the About/Settings dialogs.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog

from . import theme
from .__about__ import APP_TITLE
from .convert import convert_to_markdown
from .formats import format_supported_extensions_label

# Sized to fit the progress bar and the About button in the header row
# without cramping anything else. Tuned by measuring the packed layout's
# actual winfo_reqheight() and leaving a deliberate margin below it --
# comfortable breathing room without a large dead gap at the bottom.
WINDOW_WIDTH = 460
WINDOW_HEIGHT = 370

# Simulated-progress tuning (see _animate_progress -- there is no real
# progress signal to drive this from; verified nothing in packages/markitdown
# exposes one). PROGRESS_CAP is the ceiling the animation eases toward but
# never reaches on its own, so it never *looks* finished before the real
# conversion actually reports back; PROGRESS_EASE is how much of the
# remaining distance to the cap gets closed on each tick (higher = faster
# start, still slowing down the same way).
PROGRESS_TICK_MS = 90
PROGRESS_CAP = 0.92
PROGRESS_EASE = 0.06
PROGRESS_RESET_DELAY_MS = 500


class MarkItDownApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.selected_path: str | None = None
        self.last_result_markdown: str | None = None
        self._converting = False
        self._progress_fraction = 0.0
        self._progress_after_id: str | None = None
        self._progress_reset_after_id: str | None = None
        self._last_status_message = ""
        self._last_status_kind = "neutral"

        # theme.get_theme() reflects whatever was loaded from disk at import
        # time (see theme.py's module-level load_settings() call), so this
        # already picks up a saved theme/accent before anything is drawn --
        # no flash of the default theme on startup.
        self.theme = theme.get_theme()

        self._configure_window()
        self._build_widgets()

        # Rebuild live whenever the theme changes (Settings dialog, or About
        # if it's somehow open at the same time -- see theme.add_listener).
        theme.add_listener(self._on_theme_changed)

    # --- setup --------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=self.theme.BG_WINDOW)
        self.root.resizable(False, False)

        # Center the fixed-size window on screen.
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - WINDOW_WIDTH) // 2)
        y = max(0, (screen_h - WINDOW_HEIGHT) // 3)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.maxsize(WINDOW_WIDTH, WINDOW_HEIGHT)

        family = theme.resolve_font_family(self.root)
        self.font_title = (family, theme.FONT_SIZE_TITLE, "bold")
        self.font_body = (family, theme.FONT_SIZE_BODY)
        self.font_small = (family, theme.FONT_SIZE_SMALL)

    def _build_widgets(self) -> None:
        # Deferred import: widgets.py pulls in tkinter.font, no need to pay
        # for that unless we're actually building the UI.
        from .widgets import ProgressBar, RoundedButton

        th = self.theme
        outer = tk.Frame(self.root, bg=th.BG_WINDOW)
        outer.pack(fill="both", expand=True, padx=18, pady=12)
        self._outer = outer

        # --- Header: title (left) + Settings/About (top-right corner) ------
        header = tk.Frame(outer, bg=th.BG_WINDOW)
        header.pack(fill="x")

        # Packed in this order (both side="right") so the header reads,
        # left to right: title ... Settings, About -- About claims the
        # rightmost corner first, Settings takes the slot next to it.
        RoundedButton(
            header,
            text="About",
            command=self._on_about,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=self.font_small,
        ).pack(side="right", anchor="n")

        RoundedButton(
            header,
            text="⚙",  # gear glyph -- compact, icon-style per the request
            command=self._on_settings,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=self.font_small,
        ).pack(side="right", anchor="n", padx=(0, 6))

        tk.Label(
            header,
            text=APP_TITLE,
            font=self.font_title,
            bg=th.BG_WINDOW,
            fg=th.FG_PRIMARY,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # --- Supported formats subtitle ------------------------------------
        tk.Label(
            outer,
            text=format_supported_extensions_label(),
            font=self.font_small,
            bg=th.BG_WINDOW,
            fg=th.FG_SECONDARY,
            justify="left",
            anchor="w",
            wraplength=WINDOW_WIDTH - 36,
        ).pack(fill="x", pady=(2, 12))

        # --- Choose File + filename display ------------------------------
        self.choose_button = RoundedButton(
            outer,
            text="Choose File",
            command=self._on_choose_file,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=self.font_body,
        )
        self.choose_button.pack(anchor="w", pady=(0, 8))

        self.filename_var = tk.StringVar(value="No file selected")
        filename_card = tk.Frame(
            outer,
            bg=th.BG_SURFACE,
            highlightbackground=th.BORDER,
            highlightcolor=th.BORDER,
            highlightthickness=1,
            bd=0,
        )
        filename_card.pack(fill="x", pady=(0, 12))
        tk.Label(
            filename_card,
            textvariable=self.filename_var,
            font=self.font_body,
            bg=th.BG_SURFACE,
            fg=th.FG_SECONDARY,
            anchor="w",
            padx=10,
            pady=6,
        ).pack(fill="x")

        # --- Convert (primary/accent, full width) ------------------------
        self.convert_button = RoundedButton(
            outer,
            text="Convert",
            command=self._on_convert,
            bg=th.ACCENT,
            hover_bg=th.ACCENT_HOVER,
            active_bg=th.ACCENT_PRESSED,
            fg=th.FG_ON_ACCENT,
            font=self.font_body,
            stretch=True,
        )
        self.convert_button.pack(fill="x", pady=(0, 10))
        self.convert_button.set_enabled(False)

        # --- Progress bar (see _animate_progress -- there's no real progress
        # signal from MarkItDown to drive this with; it's a simulated, eased
        # animation while a conversion is in flight). Empty/no text at rest.
        self.progress_bar = ProgressBar(
            outer,
            width=WINDOW_WIDTH - 36,
            font=self.font_small,
        )
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # --- Status message -------------------------------------------
        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(
            outer,
            textvariable=self.status_var,
            font=self.font_small,
            bg=th.BG_WINDOW,
            fg=th.FG_SECONDARY,
            justify="left",
            anchor="w",
            wraplength=WINDOW_WIDTH - 36,
        )
        self.status_label.pack(fill="x", pady=(0, 10))

        # --- Save Output (bottom, secondary) ---------------------------
        self.save_button = RoundedButton(
            outer,
            text="Save Output",
            command=self._on_save,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=self.font_body,
            stretch=True,
        )
        # Packed in normal top-down flow (not side="bottom") so the whole
        # block of widgets stays contiguous -- any leftover slack from
        # WINDOW_HEIGHT collects as one margin below Save Output, instead of
        # as an odd gap sandwiched between the status line and this button.
        self.save_button.pack(fill="x")
        self.save_button.set_enabled(False)

    # --- theming --------------------------------------------------------

    def _on_theme_changed(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        """Tear down and rebuild every widget from the current theme.
        Called whenever the theme changes so the whole app restyles live,
        with no restart needed.

        Only destroys `self._outer` (the main content frame), not every
        child of `root` -- `Toplevel` dialogs (About, Settings) are also
        counted among `root.winfo_children()` in Tkinter's widget hierarchy
        even though they render as separate OS windows, so a blanket
        "destroy every child of root" would silently close whichever dialog
        the user is actually looking at (found by testing: switching themes
        from *inside* the Settings dialog made Settings vanish, because that
        rebuild was destroying it out from under itself). Each dialog
        rebuilds its own contents independently via its own theme listener.
        `root` itself is never destroyed, so pending `root.after()` timers --
        e.g. a conversion's progress animation -- keep running across a
        rebuild.
        """
        self.theme = theme.get_theme()
        self._outer.destroy()
        self.root.configure(bg=self.theme.BG_WINDOW)
        self._build_widgets()

        # The widgets above are brand new; re-apply the state that actually
        # lives on `self` so a theme switch mid-flow doesn't look like it
        # reset the app (chosen file, last result, in-progress conversion).
        if self.selected_path:
            self.filename_var.set(os.path.basename(self.selected_path))
            self.convert_button.set_enabled(not self._converting)
        if self.last_result_markdown:
            self.save_button.set_enabled(not self._converting)
        if self._converting:
            self.convert_button.set_enabled(False)
            self.save_button.set_enabled(False)
        if self._last_status_message:
            self._set_status(self._last_status_message, kind=self._last_status_kind)

    # --- handlers -------------------------------------------------------

    def _on_about(self) -> None:
        from .about import show_about_dialog

        show_about_dialog(
            self.root,
            font_title=self.font_title,
            font_body=self.font_body,
            font_small=self.font_small,
        )

    def _on_settings(self) -> None:
        from .settings import show_settings_dialog

        show_settings_dialog(
            self.root,
            font_title=self.font_title,
            font_body=self.font_body,
            font_small=self.font_small,
        )

    def _on_choose_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose a file to convert")
        if not path:
            return
        self.selected_path = path
        self.filename_var.set(os.path.basename(path))
        self.last_result_markdown = None
        self.save_button.set_enabled(False)
        self.convert_button.set_enabled(True)
        self._set_status("", kind="neutral")

    def _on_convert(self) -> None:
        if not self.selected_path or self._converting:
            return
        self._converting = True
        self.convert_button.set_enabled(False)
        self.save_button.set_enabled(False)
        self._set_status(f"Converting {os.path.basename(self.selected_path)}...", kind="neutral")
        self._start_progress_animation()

        path = self.selected_path
        threading.Thread(target=self._run_conversion, args=(path,), daemon=True).start()

    def _run_conversion(self, path: str) -> None:
        # Runs on a worker thread -- Tkinter widgets must only be touched from
        # the main thread, so every outcome is marshalled back via root.after().
        try:
            result = convert_to_markdown(path)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the status bar, not swallowed
            self.root.after(0, self._on_conversion_error, exc)
        else:
            self.root.after(0, self._on_conversion_success, result)

    def _on_conversion_success(self, result) -> None:
        self._converting = False
        self.last_result_markdown = result.markdown
        self.convert_button.set_enabled(True)
        self.save_button.set_enabled(True)
        self._set_status(
            f"Converted successfully ({len(result.markdown):,} characters).", kind="success"
        )
        self._finish_progress_animation(success=True)

    def _on_conversion_error(self, exc: Exception) -> None:
        self._converting = False
        self.convert_button.set_enabled(True)
        self.save_button.set_enabled(False)
        self._set_status(f"Conversion failed: {exc}", kind="error")
        self._finish_progress_animation(success=False)

    # --- progress bar (simulated -- see the module docstring above) ---

    def _start_progress_animation(self) -> None:
        # A new conversion always wins over whatever the *previous* one's
        # bar was doing -- in particular, cancel a pending "fade back to
        # empty" from an earlier success, or it would stomp on this run's
        # animation partway through.
        self._cancel_progress_timers()
        self._progress_fraction = 0.0
        self.progress_bar.set_progress(0.0, show_text=True)
        self._animate_progress()

    def _animate_progress(self) -> None:
        if not self._converting:
            return
        self._progress_fraction += (PROGRESS_CAP - self._progress_fraction) * PROGRESS_EASE
        self.progress_bar.set_progress(self._progress_fraction)
        self._progress_after_id = self.root.after(PROGRESS_TICK_MS, self._animate_progress)

    def _finish_progress_animation(self, *, success: bool) -> None:
        self._cancel_progress_timers()
        if success:
            # Snap to 100% so the bar actually reads as "done" for a moment,
            # then fade back to empty -- the status line is the lasting
            # record of the result, the bar is just per-conversion feedback.
            self.progress_bar.set_progress(1.0)
            self._progress_reset_after_id = self.root.after(
                PROGRESS_RESET_DELAY_MS, lambda: self.progress_bar.set_progress(0.0, show_text=False)
            )
        else:
            # No misleading "100%" on a failed conversion -- just clear it.
            self.progress_bar.set_progress(0.0, show_text=False)

    def _cancel_progress_timers(self) -> None:
        if self._progress_after_id is not None:
            self.root.after_cancel(self._progress_after_id)
            self._progress_after_id = None
        if self._progress_reset_after_id is not None:
            self.root.after_cancel(self._progress_reset_after_id)
            self._progress_reset_after_id = None

    def _on_save(self) -> None:
        if not self.last_result_markdown:
            return
        default_name = "output.md"
        if self.selected_path:
            base, _ext = os.path.splitext(os.path.basename(self.selected_path))
            default_name = f"{base}.md"

        dest = filedialog.asksaveasfilename(
            title="Save Markdown Output",
            defaultextension=".md",
            initialfile=default_name,
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
        )
        if not dest:
            return

        try:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(self.last_result_markdown)
        except OSError as exc:
            self._set_status(f"Could not save file: {exc}", kind="error")
            return

        self._set_status(f"Saved to {os.path.basename(dest)}.", kind="success")

    # --- helpers --------------------------------------------------------

    def _set_status(self, message: str, *, kind: str) -> None:
        self._last_status_message = message
        self._last_status_kind = kind
        self.status_var.set(message)
        color = {
            "success": self.theme.SUCCESS,
            "error": self.theme.ERROR,
            "neutral": self.theme.FG_SECONDARY,
        }[kind]
        self.status_label.configure(fg=color)


def run() -> None:
    root = tk.Tk()
    MarkItDownApp(root)
    root.mainloop()

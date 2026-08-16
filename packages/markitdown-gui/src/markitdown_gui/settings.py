# SPDX-FileCopyrightText: 2026-present Ayush Chaugule
#
# SPDX-License-Identifier: MIT
"""The Settings dialog: dark/light theme toggle and accent color picker.

Same modal-Toplevel pattern as about.py (see that module's docstring for why
a Toplevel rather than an in-window content swap), and the same
theme.add_listener live-restyle approach -- picking a theme or accent calls
straight into theme.set_mode()/set_accent(), which persist the choice and
notify every listener (this dialog, the main window, and About if it's open)
to rebuild with the new colors. There's no separate "Apply"/"Save" step:
every click here takes effect immediately, the same way the rest of this
app's controls work.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser

from . import theme

# Sized the same way as every other fixed dialog in this app: measured the
# packed layout's actual winfo_reqheight() (323px) and left a deliberate
# margin below it, rather than guessing a round number.
DIALOG_WIDTH = 360
DIALOG_HEIGHT = 360

SWATCH_SIZE = 28


def show_settings_dialog(root: tk.Tk, *, font_title, font_body, font_small) -> None:
    """Open the Settings dialog as a modal child of `root`."""
    from .widgets import ColorSwatch, RoundedButton

    dialog = tk.Toplevel(root)
    dialog.title("Settings")
    dialog.resizable(False, False)
    dialog.transient(root)

    def build_content() -> None:
        th = theme.get_theme()
        dialog.configure(bg=th.BG_WINDOW)
        for child in dialog.winfo_children():
            child.destroy()

        outer = tk.Frame(dialog, bg=th.BG_WINDOW)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(
            outer,
            text="Settings",
            font=font_title,
            bg=th.BG_WINDOW,
            fg=th.FG_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 14))

        # --- Theme mode -------------------------------------------------
        tk.Label(
            outer,
            text="Theme",
            font=font_body,
            bg=th.BG_WINDOW,
            fg=th.FG_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        mode_row = tk.Frame(outer, bg=th.BG_WINDOW)
        mode_row.pack(fill="x", pady=(0, 16))

        def _mode_button(parent, label: str, mode_value: str) -> RoundedButton:
            # The active mode gets the accent treatment (same "this is the
            # current/selected thing" role accent plays everywhere else in
            # this app); the inactive one stays plain.
            active = th.mode == mode_value
            return RoundedButton(
                parent,
                text=label,
                command=lambda: theme.set_mode(mode_value),
                bg=th.ACCENT if active else th.BG_BUTTON,
                hover_bg=th.ACCENT_HOVER if active else th.BUTTON_HOVER,
                active_bg=th.ACCENT_PRESSED if active else th.BUTTON_PRESSED,
                fg=th.FG_ON_ACCENT if active else th.FG_PRIMARY,
                font=font_body,
                stretch=True,
            )

        _mode_button(mode_row, "Dark", "dark").pack(side="left", fill="x", expand=True, padx=(0, 6))
        _mode_button(mode_row, "Light", "light").pack(side="left", fill="x", expand=True)

        # --- Accent color -------------------------------------------------
        tk.Label(
            outer,
            text="Accent Color",
            font=font_body,
            bg=th.BG_WINDOW,
            fg=th.FG_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        swatch_row = tk.Frame(outer, bg=th.BG_WINDOW)
        swatch_row.pack(fill="x", pady=(0, 10))

        for preset in theme.ACCENT_PRESETS:
            selected = preset.lower() == th.ACCENT.lower()
            ColorSwatch(
                swatch_row,
                preset,
                command=lambda c=preset: theme.set_accent(c),
                selected=selected,
                size=SWATCH_SIZE,
            ).pack(side="left", padx=(0, 8))

        RoundedButton(
            outer,
            text="Custom Color...",
            command=_pick_custom_color,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=font_body,
            stretch=True,
        ).pack(fill="x", pady=(0, 16))

        RoundedButton(
            outer,
            text="Close",
            command=_close,
            bg=th.BG_BUTTON,
            hover_bg=th.BUTTON_HOVER,
            active_bg=th.BUTTON_PRESSED,
            fg=th.FG_PRIMARY,
            font=font_body,
            stretch=True,
        ).pack(fill="x")

    def _pick_custom_color() -> None:
        th = theme.get_theme()
        # tkinter.colorchooser is stdlib and does its own hex validation --
        # simplest and lowest-risk way to offer "any color", no hand-rolled
        # hex text entry/validation needed. Returns ((r, g, b), '#rrggbb'),
        # or (None, None) if the user cancels.
        _rgb, hex_color = colorchooser.askcolor(
            color=th.ACCENT, title="Choose accent color", parent=dialog
        )
        if hex_color:
            theme.set_accent(hex_color)

    def _on_theme_changed() -> None:
        # Defensive: normally _close() below deregisters this listener
        # before the dialog is destroyed, but if the Toplevel ever goes away
        # through some other path, self-unsubscribe instead of crashing on
        # a destroyed widget the next time the theme changes.
        if not dialog.winfo_exists():
            theme.remove_listener(_on_theme_changed)
            return
        build_content()

    def _close() -> None:
        theme.remove_listener(_on_theme_changed)
        dialog.destroy()

    theme.add_listener(_on_theme_changed)
    build_content()

    root.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - DIALOG_WIDTH) // 2
    y = root.winfo_rooty() + (root.winfo_height() - DIALOG_HEIGHT) // 3
    dialog.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}+{max(0, x)}+{max(0, y)}")
    dialog.minsize(DIALOG_WIDTH, DIALOG_HEIGHT)
    dialog.maxsize(DIALOG_WIDTH, DIALOG_HEIGHT)

    dialog.protocol("WM_DELETE_WINDOW", _close)
    dialog.focus_set()
    dialog.grab_set()  # modal: block interaction with the main window until closed

# SPDX-FileCopyrightText: 2026-present Ayush Chaugule
#
# SPDX-License-Identifier: MIT
"""Switchable Mint-Y color palettes (dark/light) plus a live accent-color
override, persisted across restarts.

Tkinter has no access to the real GTK/Cinnamon theme engine (GTK themes don't
apply to Tk widgets on any platform), so "matching Mint Cinnamon" here means
hand-reproducing the palette, not hooking into a live theme. To make that
reproduction accurate, every hex value below was read directly out of the
dev machine's actual installed Mint-Y themes rather than guessed:

    Dark:  /usr/share/themes/Mint-Y-Dark-Purple/gtk-3.0/gtk-dark.css
    Light: /usr/share/themes/Mint-Y-Purple/gtk-3.0/gtk.css

(`@define-color` rules for theme_bg_color, accent_color, borders, button
background, semantic colors, etc.) Both are real, shipped Mint-Y variants,
confirmed the same way as before: `gsettings get org.cinnamon.theme name`.

Everything a widget needs to draw itself lives on a `Theme` instance
(`get_theme()`), never on bare module constants -- that's what makes a live
theme switch possible: `set_mode()`/`set_accent()` rebuild the current
`Theme` and notify subscribers (see `add_listener()`), and callers
(app.py/about.py/settings.py) rebuild their widgets reading the new one.
Layout constants that aren't colors (button radius/padding, font sizes) stay
as plain module constants below, since they don't vary by theme.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# --- Layout constants (not themed -- same in dark and light) ---------------

# Cinnamon/GTK buttons in this theme use a small radius and compact padding
# (button { border-radius: 3px; padding: 5px 8px; min-height: 22px; }).
# We go slightly larger than the literal GTK values because Tk fonts render
# a bit taller than GTK's, and this is a *small* fixed window where a couple
# of extra px of breathing room reads as "designed" rather than "cramped".
BUTTON_RADIUS = 8
BUTTON_PAD_X = 16
BUTTON_PAD_Y = 6

# Cinnamon's own default UI font on this system (gsettings
# org.cinnamon.desktop.interface font-name -> 'Ubuntu 10'). Falls back
# gracefully via FONT_FALLBACKS if "Ubuntu" isn't installed (e.g. when this
# app runs on a non-Mint machine).
FONT_FAMILY_PRIMARY = "Ubuntu"
FONT_FALLBACKS = ("Noto Sans", "DejaVu Sans", "Helvetica")

FONT_SIZE_TITLE = 15
FONT_SIZE_BODY = 10
FONT_SIZE_SMALL = 9

PROGRESS_BAR_HEIGHT = 20
PROGRESS_BAR_RADIUS = 6

# A small set of preset accent swatches offered in Settings, pulled from
# other real Mint-Y-Dark-* variants' own accent_color (same method as the
# rest of this file) rather than invented -- these are all colors Mint
# itself ships as accent options, just gathered into one picker here.
ACCENT_PRESETS = [
    "#8c5dd9",  # Purple -- this app's default (Mint-Y-Dark-Purple)
    "#0c75de",  # Blue   (Mint-Y-Dark-Blue)
    "#199ca8",  # Teal   (Mint-Y-Dark-Teal)
    "#ff7139",  # Orange (Mint-Y-Dark-Orange)
    "#e54980",  # Pink   (Mint-Y-Dark-Pink)
    "#e82127",  # Red    (Mint-Y-Dark-Red)
]


def resolve_font_family(tk_root) -> str:
    """Return FONT_FAMILY_PRIMARY if it's actually installed on this system,
    otherwise the first available fallback. Keeps the app looking right on
    Mint (where Ubuntu is present) without breaking on other distros/CI.
    """
    import tkinter.font as tkfont

    available = set(tkfont.families(tk_root))
    for candidate in (FONT_FAMILY_PRIMARY,) + FONT_FALLBACKS:
        if candidate in available:
            return candidate
    return "TkDefaultFont"


# --- Color math (used to derive hover/pressed/disabled shades) -------------


def _clamp(value: int) -> int:
    return max(0, min(255, value))


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(_clamp(c) for c in rgb))


def lighten(color: str, amount: float) -> str:
    """Blend `color` toward white by `amount` (0..1)."""
    r, g, b = _hex_to_rgb(color)
    return _rgb_to_hex(
        (
            round(r + (255 - r) * amount),
            round(g + (255 - g) * amount),
            round(b + (255 - b) * amount),
        )
    )


def darken(color: str, amount: float) -> str:
    """Blend `color` toward black by `amount` (0..1)."""
    r, g, b = _hex_to_rgb(color)
    return _rgb_to_hex(
        (
            round(r * (1 - amount)),
            round(g * (1 - amount)),
            round(b * (1 - amount)),
        )
    )


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_valid_hex_color(value: str) -> bool:
    """True if `value` is a strict `#rrggbb` string, for validating a
    user-entered custom accent color before it's accepted."""
    return bool(_HEX_COLOR_RE.match(value))


# --- Base palettes (straight from each variant's gtk*.css) -----------------

_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        # /usr/share/themes/Mint-Y-Dark-Purple/gtk-3.0/gtk-dark.css
        "BG_WINDOW": "#2e2e33",  # theme_bg_color / theme_base_color
        "BG_TITLEBAR": "#222226",  # wm_bg -- used for the in-window header strip
        "BG_BUTTON": "#333338",  # default (non-accent) button background
        "BG_SURFACE": "#333339",  # insensitive_bg_color -- raised panel/card bg
        "BORDER": "#202023",  # borders
        "FG_PRIMARY": "#e4e4e4",  # theme_fg_color (rgba(255,255,255,.87) flattened over BG_WINDOW)
        "FG_SECONDARY": "#a8a8a8",  # placeholder_text_color -- muted/help text
        "FG_ON_ACCENT": "#ffffff",  # theme_selected_fg_color
        "ACCENT": "#8c5dd9",  # accent_color / theme_selected_bg_color (Mint-Y-Purple)
        "SUCCESS": "#73d216",  # success_color
        "WARNING": "#f27835",  # warning_color
        "ERROR": "#fc4138",  # error_color
        # progressbar trough { background-color: #222226 } -- same as BG_TITLEBAR/wm_bg here.
        "PROGRESS_TROUGH": "#222226",
    },
    "light": {
        # /usr/share/themes/Mint-Y-Purple/gtk-3.0/gtk.css -- the light
        # counterpart of the dark palette above, confirmed to be a real,
        # separately-shipped Mint-Y variant, not invented.
        "BG_WINDOW": "#f8f8f9",  # theme_bg_color
        "BG_TITLEBAR": "#ebebed",  # wm_bg
        "BG_BUTTON": "#fefefe",  # default button background-color
        "BG_SURFACE": "#fcfcfd",  # insensitive_bg_color
        "BORDER": "#c8c8ce",  # borders
        "FG_PRIMARY": "#202020",  # theme_fg_color (rgba(0,0,0,.87) flattened over BG_WINDOW)
        "FG_SECONDARY": "#a8a8a8",  # placeholder_text_color -- identical to dark's value
        "FG_ON_ACCENT": "#ffffff",  # theme_selected_fg_color -- same as dark
        "ACCENT": "#8c5dd9",  # accent_color -- Mint-Y-Purple uses the exact same purple as the dark variant
        "SUCCESS": "#73d216",  # success_color -- same across both variants
        "WARNING": "#f27835",  # warning_color -- same across both variants
        "ERROR": "#fc4138",  # error_color -- same across both variants
        # progressbar trough { background-color: #FFFFFF } -- unlike dark, this
        # does NOT coincide with wm_bg, so it's its own value here.
        "PROGRESS_TROUGH": "#ffffff",
    },
}

# DISABLED_BG/DISABLED_FG aren't literal @define-color values (the original
# dark constants predate this refactor and were tuned by eye, then verified
# by sampling actual rendered pixel colors); derived the same way for both
# modes so the relationship (disabled fill sits close to BG_SURFACE, legible
# only via the always-on button border) stays consistent between them.
_DISABLED_BG_DARKEN_AMOUNT = {"dark": 0.22, "light": 0.06}
_DISABLED_FG = {
    "dark": "#6f6f74",
    "light": "#7c7c7c",  # insensitive_fg_color alpha(black,.5) flattened over BG_WINDOW
}


@dataclass(frozen=True)
class Theme:
    """A fully-resolved set of colors for one mode + accent combination.
    Every field name matches the constant names this module used to export
    at module level, so `theme.get_theme().BG_WINDOW` reads just like the
    old `theme.BG_WINDOW` did at every call site.
    """

    mode: str  # "dark" | "light"
    BG_WINDOW: str
    BG_TITLEBAR: str
    BG_BUTTON: str
    BG_SURFACE: str
    BORDER: str
    FG_PRIMARY: str
    FG_SECONDARY: str
    FG_ON_ACCENT: str
    ACCENT: str
    SUCCESS: str
    WARNING: str
    ERROR: str
    DISABLED_BG: str
    DISABLED_FG: str
    PROGRESS_TROUGH: str
    ACCENT_HOVER: str
    ACCENT_PRESSED: str
    BUTTON_HOVER: str
    BUTTON_PRESSED: str
    PROGRESS_FILL: str
    PROGRESS_TEXT: str


def build_theme(mode: str, accent_override: Optional[str] = None) -> Theme:
    """Resolve a full `Theme` for `mode` ("dark"/"light"), optionally
    overriding the accent color (falls back to that mode's own default
    accent otherwise).
    """
    base = _PALETTES[mode]
    accent = accent_override or base["ACCENT"]

    # Neutral (non-accent) buttons need opposite hover directions in the two
    # modes: dark buttons get *lighter* on hover (a highlight against a dark
    # surface); an already near-white light button would barely change (or
    # clip at white, losing the hover cue entirely) if lightened the same
    # way, so light buttons get slightly *darker* on hover instead.
    if mode == "dark":
        button_hover = lighten(base["BG_BUTTON"], 0.15)
        button_pressed = darken(base["BG_BUTTON"], 0.10)
    else:
        button_hover = darken(base["BG_BUTTON"], 0.06)
        button_pressed = darken(base["BG_BUTTON"], 0.12)

    disabled_bg = darken(base["BG_SURFACE"], _DISABLED_BG_DARKEN_AMOUNT[mode])

    return Theme(
        mode=mode,
        BG_WINDOW=base["BG_WINDOW"],
        BG_TITLEBAR=base["BG_TITLEBAR"],
        BG_BUTTON=base["BG_BUTTON"],
        BG_SURFACE=base["BG_SURFACE"],
        BORDER=base["BORDER"],
        FG_PRIMARY=base["FG_PRIMARY"],
        FG_SECONDARY=base["FG_SECONDARY"],
        FG_ON_ACCENT=base["FG_ON_ACCENT"],
        ACCENT=accent,
        SUCCESS=base["SUCCESS"],
        WARNING=base["WARNING"],
        ERROR=base["ERROR"],
        DISABLED_BG=disabled_bg,
        DISABLED_FG=_DISABLED_FG[mode],
        PROGRESS_TROUGH=base["PROGRESS_TROUGH"],
        # Accent interaction states: same lighten/darken direction regardless
        # of mode. The accent is always a deliberately-saturated color (the
        # presets above, or a user's own custom pick), so it carries its own
        # contrast against either background -- unlike the neutral buttons
        # above, it doesn't need a mode-aware direction flip.
        ACCENT_HOVER=lighten(accent, 0.12),
        ACCENT_PRESSED=darken(accent, 0.15),
        BUTTON_HOVER=button_hover,
        BUTTON_PRESSED=button_pressed,
        PROGRESS_FILL=accent,
        PROGRESS_TEXT=base["FG_ON_ACCENT"],
    )


# --- Persistence -------------------------------------------------------

# Standard XDG config location (respects $XDG_CONFIG_HOME if set), same
# convention any well-behaved Linux desktop app uses for its own settings.
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "mintdown"
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULT_MODE = "dark"


def load_settings() -> tuple[str, Optional[str]]:
    """Read the persisted (mode, accent_override) from disk, falling back to
    defaults if the file doesn't exist yet or is unreadable/corrupt --
    persistence is best-effort and must never crash startup.
    """
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return DEFAULT_MODE, None

    mode = data.get("theme_mode")
    if mode not in _PALETTES:
        mode = DEFAULT_MODE

    accent = data.get("accent_override")
    if accent is not None and not is_valid_hex_color(accent):
        accent = None

    return mode, accent


def save_settings(mode: str, accent_override: Optional[str]) -> None:
    """Write (mode, accent_override) to disk. Best-effort: a failure here
    (read-only home directory, etc.) shouldn't break the app -- the choice
    just won't survive a restart.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps({"theme_mode": mode, "accent_override": accent_override}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# --- Current theme + change notification --------------------------------

_current_mode, _current_accent = load_settings()
_current_theme: Theme = build_theme(_current_mode, _current_accent)

_listeners: list[Callable[[], None]] = []


def get_theme() -> Theme:
    """The currently-active resolved theme. Read this fresh (don't cache it
    across a theme change) -- every widget-building function does."""
    return _current_theme


def add_listener(callback: Callable[[], None]) -> None:
    """Register `callback` to be called (no args) whenever the theme
    changes. Used by app.py/about.py/settings.py to restyle live -- each
    should call `remove_listener` when it no longer needs updates (e.g. a
    dialog closing), so closed windows don't stay subscribed forever.
    """
    _listeners.append(callback)


def remove_listener(callback: Callable[[], None]) -> None:
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def _apply_and_notify() -> None:
    global _current_theme
    _current_theme = build_theme(_current_mode, _current_accent)
    save_settings(_current_mode, _current_accent)
    for callback in list(_listeners):  # copy: a callback may add/remove listeners
        callback()


def set_mode(mode: str) -> None:
    """Switch to "dark" or "light", keeping the current accent override (if
    any), and notify every listener so the whole app restyles immediately.
    """
    global _current_mode
    if mode not in _PALETTES or mode == _current_mode:
        return
    _current_mode = mode
    _apply_and_notify()


def set_accent(accent: Optional[str]) -> None:
    """Override the accent color (None to go back to the current mode's
    default), and notify every listener so the whole app restyles
    immediately.
    """
    global _current_accent
    if accent is not None and not is_valid_hex_color(accent):
        return
    _current_accent = accent
    _apply_and_notify()

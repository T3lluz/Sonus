"""Colors, metrics, and fonts. Palette carried over from SonarDeck."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase


BG = "#1A1F23"
PANEL = "#2D343C"
RULE = "#262B31"
TRACK = "#3A424C"
FILL = "#4F5FD7"
FILL_MUTED = "#5A6270"
TEXT = "#E8EDF5"
DIM = "#8B93A0"
MUTE_FG = "#E24B4A"
MUTE_FG_HOVER = "#FF6B6B"
THUMB = "#F1F4F9"
SWITCH_OFF = "#242A31"
MUTE_BG = "#3A424C"
MUTE_HOVER = "#484F57"
CLOSE_HOVER = "#6B2C2C"
CLOSE_FG_HOVER = "#F2D5D5"
APP_ACCENT = "#8B93A0"


SIDE_PAD = 22
STRIP_GAP = 10
STRIP_W = 126
STRIP_H = 528
ICON_SIZE = 46
ICON_Y = 46
LABEL_Y = 92
PCT_Y = 118
FADER_TOP = 166
MUTE_SIZE = 42
MUTE_BOTTOM = 26
MUTE_Y0 = STRIP_H - MUTE_BOTTOM - MUTE_SIZE
FADER_BOT = MUTE_Y0 - 40
THUMB_W = 22.0
THUMB_H = 40.0
TRACK_W = 10.0

# EQ affordance in the top-right corner of category strips.
EQ_ICON_SIZE = 26
EQ_ICON_PAD = 9

# Right block: category bins stacked in a left column, master container
# beside them so the app faders get the full height.
BIN_GAP = 10
BIN_COL_W = 170
MC_X = BIN_COL_W + BIN_GAP
MC_H = STRIP_H
MC_PAD = 12
MC_LABEL_H = 24
BIN_CHIP = 28
BIN_CHIP_GAP = 6

APP_STRIP_W = STRIP_W
APPS_MIN = 1
EMPTY_MASTER_INNER = 200
DIVIDER_W = 18
BAR_H = 44
TOP_PAD = 18
HEADER_GAP = 8
BOT_PAD = 18
CARD_R = 18
APP_ICON = 46
SCROLL_H = 10
SCROLL_PAD = 14
SETTINGS_W = 400
SETTINGS_CARD_R = 12
WINDOW_R = 20

# Full-page equaliser view.
EQ_MARGIN = 24
EQ_LEFT_COL = 260
EQ_BANDS_X = EQ_MARGIN + EQ_LEFT_COL + 40
EQ_TOPBAR_H = 56
EQ_SLIDER_H = 356
EQ_THUMB_W = 26.0
EQ_THUMB_H = 14.0
EQ_TRACK_W = 6.0

CHANNEL_COUNT = 5
SONAR_BLOCK_W = CHANNEL_COUNT * STRIP_W + (CHANNEL_COUNT - 1) * STRIP_GAP
APPS_VIEW_W = MC_X + MC_PAD * 2 + APPS_MIN * APP_STRIP_W
WIN_W = SIDE_PAD * 2 + SONAR_BLOCK_W + DIVIDER_W + APPS_VIEW_W
WIN_H = TOP_PAD + BAR_H + HEADER_GAP + STRIP_H + BOT_PAD
PAGE_W = WIN_W - SIDE_PAD * 2
PAGE_H = STRIP_H

RISE_PX = 10
SHOW_MS = 180
HIDE_MS = 140
DRAWER_MS = 220
PAGE_MS = 260


# Segoe UI is not on Linux; pick the closest grotesque that is actually installed.
_FAMILY_PREFERENCE = (
    "Inter",
    "Adwaita Sans",
    "Noto Sans",
    "Open Sans",
    "Cantarell",
    "Fira Sans",
    "DejaVu Sans",
)

_family_cache: str | None = None


def ui_family() -> str:
    global _family_cache
    if _family_cache is None:
        installed = set(QFontDatabase.families())
        _family_cache = next(
            (name for name in _FAMILY_PREFERENCE if name in installed),
            QFont().defaultFamily(),
        )
    return _family_cache


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(ui_family(), size)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def semibold(size: int) -> QFont:
    return font(size, QFont.Weight.DemiBold)


def color(value: str, alpha: int | None = None) -> QColor:
    c = QColor(value)
    if alpha is not None:
        c.setAlpha(alpha)
    return c

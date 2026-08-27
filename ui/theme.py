"""System / Light / Dark theming with persistence.

Qt 6.5+ already follows the OS light/dark setting when the *System* mode is
selected; Light and Dark install an explicit Fusion palette so the user can
override the OS.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_SETTINGS_KEY = "appearance/theme"


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return {"system": "System", "light": "Light", "dark": "Dark"}[self.value]


def _dark_palette() -> QPalette:
    p = QPalette()
    base = QColor(32, 33, 36)
    alt = QColor(42, 43, 46)
    text = QColor(230, 230, 230)
    disabled = QColor(127, 127, 127)
    highlight = QColor(66, 133, 244)

    p.setColor(QPalette.ColorRole.Window, base)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, QColor(24, 25, 28))
    p.setColor(QPalette.ColorRole.AlternateBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, alt)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.ColorRole.Link, highlight)
    p.setColor(QPalette.ColorRole.Highlight, highlight)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, disabled)

    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(245, 245, 247))
    p.setColor(QPalette.ColorRole.WindowText, QColor(20, 20, 20))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(238, 238, 240))
    p.setColor(QPalette.ColorRole.Text, QColor(20, 20, 20))
    p.setColor(QPalette.ColorRole.Button, QColor(238, 238, 240))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(20, 20, 20))
    p.setColor(QPalette.ColorRole.Highlight, QColor(66, 133, 244))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Link, QColor(26, 115, 232))
    return p


def load_mode() -> ThemeMode:
    raw = QSettings().value(_SETTINGS_KEY, ThemeMode.SYSTEM.value)
    try:
        return ThemeMode(str(raw))
    except ValueError:
        return ThemeMode.SYSTEM


def save_mode(mode: ThemeMode) -> None:
    QSettings().setValue(_SETTINGS_KEY, mode.value)


def apply(app: QApplication, mode: ThemeMode) -> None:
    """Apply *mode* to the running application."""

    app.setStyle("Fusion")
    if mode is ThemeMode.DARK:
        app.setPalette(_dark_palette())
    elif mode is ThemeMode.LIGHT:
        app.setPalette(_light_palette())
    else:
        # System: hand control back to the platform integration.
        app.setPalette(app.style().standardPalette())

    # A tiny stylesheet that both palettes share.
    app.setStyleSheet(
        """
        QSplitter::handle { background: palette(mid); }
        QSplitter::handle:horizontal { width: 3px; }
        QSplitter::handle:vertical { height: 3px; }
        QToolBar { border: 0; padding: 2px; spacing: 2px; }
        #RemoteBanner {
            background: palette(highlight);
            color: palette(highlighted-text);
            border-radius: 4px;
        }
        #AttachmentBar { background: palette(alternate-base); }
        """
    )


def is_dark(app: QApplication) -> bool:
    win = app.palette().color(QPalette.ColorRole.Window)
    return win.lightnessF() < 0.5

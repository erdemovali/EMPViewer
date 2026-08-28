"""System / Light / Dark theming with persistence.

Qt 6.5+ already follows the OS light/dark setting when the *System* mode is
selected; Light and Dark install an explicit Fusion palette so the user can
override the OS.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_SETTINGS_KEY = "appearance/theme"


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return {
            "system": QCoreApplication.translate("theme", "System"),
            "light": QCoreApplication.translate("theme", "Light"),
            "dark": QCoreApplication.translate("theme", "Dark"),
        }[self.value]


_ACCENT = QColor(76, 141, 255)


def _dark_palette() -> QPalette:
    p = QPalette()
    window = QColor(30, 32, 37)
    base = QColor(24, 26, 30)
    alt = QColor(37, 40, 46)
    mid = QColor(58, 62, 70)
    light = QColor(48, 52, 60)
    text = QColor(228, 230, 234)
    disabled = QColor(120, 124, 130)

    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, alt)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
    p.setColor(QPalette.ColorRole.Light, light)
    p.setColor(QPalette.ColorRole.Mid, mid)
    p.setColor(QPalette.ColorRole.Dark, QColor(70, 75, 84))
    p.setColor(QPalette.ColorRole.Link, _ACCENT)
    p.setColor(QPalette.ColorRole.Highlight, _ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, disabled)

    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(244, 245, 247))
    p.setColor(QPalette.ColorRole.WindowText, QColor(27, 29, 33))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 241, 244))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(27, 29, 33))
    p.setColor(QPalette.ColorRole.Text, QColor(27, 29, 33))
    p.setColor(QPalette.ColorRole.Button, QColor(249, 250, 251))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(27, 29, 33))
    p.setColor(QPalette.ColorRole.Light, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Mid, QColor(210, 213, 219))
    p.setColor(QPalette.ColorRole.Dark, QColor(160, 164, 172))
    p.setColor(QPalette.ColorRole.Highlight, QColor(47, 111, 237))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 144, 150))
    p.setColor(QPalette.ColorRole.Link, QColor(37, 99, 235))
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

    # Shared stylesheet: rounded controls, flat headers, thin scrollbars.
    app.setStyleSheet(_STYLESHEET)


_STYLESHEET = """
QLineEdit, QAbstractSpinBox, QComboBox {
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 4px 8px;
    background: palette(base);
    selection-background-color: palette(highlight);
}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {
    border-color: palette(highlight);
}

QPushButton {
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 5px 14px;
    background: palette(button);
}
QPushButton:hover { background: palette(light); }
QPushButton:pressed { background: palette(mid); }
QPushButton:default { border-color: palette(highlight); }
QPushButton:disabled { color: palette(placeholder-text); }

QToolButton { border: 0; border-radius: 6px; padding: 3px; }
QToolButton:hover { background: palette(alternate-base); }
QToolButton:pressed { background: palette(mid); }

QTreeView, QTableView, QListView {
    border: 1px solid palette(mid);
    border-radius: 8px;
    background: palette(base);
    outline: 0;
}
QTreeView::item, QTableView::item, QListView::item { padding: 4px 6px; }
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QHeaderView::section {
    background: palette(window);
    border: 0;
    border-bottom: 1px solid palette(mid);
    padding: 5px 8px;
    font-weight: 600;
}

QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: palette(highlight); }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle {
    background: palette(mid);
    border-radius: 4px;
    min-height: 28px;
    min-width: 28px;
}
QScrollBar::handle:hover { background: palette(dark); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenuBar { background: palette(window); }
QMenuBar::item { padding: 4px 10px; border-radius: 5px; }
QMenuBar::item:selected { background: palette(alternate-base); }
QMenu {
    border: 1px solid palette(mid);
    border-radius: 8px;
    padding: 4px;
    background: palette(window);
}
QMenu::item { padding: 5px 24px 5px 12px; border-radius: 5px; }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu::separator { height: 1px; background: palette(mid); margin: 4px 8px; }

QStatusBar { border-top: 1px solid palette(mid); }
QStatusBar::item { border: 0; }

QProgressBar {
    border: 1px solid palette(mid);
    border-radius: 6px;
    background: palette(base);
    text-align: center;
}
QProgressBar::chunk { background: palette(highlight); border-radius: 5px; }

QToolTip {
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 4px 8px;
    background: palette(base);
    color: palette(text);
}

QTextBrowser { border: 0; }

#HeaderBox { border-bottom: 1px solid palette(mid); }
#FindBar { border-bottom: 1px solid palette(mid); background: palette(alternate-base); }
#AttachmentBar { border-top: 1px solid palette(mid); background: palette(alternate-base); }
#RemoteBanner {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-radius: 6px;
}
#AttachmentChip {
    border: 1px solid palette(mid);
    border-radius: 13px;
    padding: 4px 12px;
    background: palette(button);
}
#AttachmentChip:hover { background: palette(light); border-color: palette(highlight); }
"""


def is_dark(app: QApplication) -> bool:
    win = app.palette().color(QPalette.ColorRole.Window)
    return win.lightnessF() < 0.5

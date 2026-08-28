"""Preferences dialog: theme, language, date format, body text, remote content,
updates."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
)

from ui import theme

#: appearance/dateFormat values -> label
DATE_FORMATS = ("local", "iso", "rfc")


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Preferences"))
        self.setModal(True)
        s = QSettings()

        form = QFormLayout(self)
        form.setContentsMargins(18, 18, 18, 14)
        form.setSpacing(10)

        self.theme_combo = QComboBox()
        for mode in theme.ThemeMode:
            self.theme_combo.addItem(mode.label, mode.value)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(theme.load_mode().value))
        form.addRow(self.tr("Theme:"), self.theme_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem(self.tr("Automatic (system)"), "auto")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Türkçe", "tr")
        self.lang_combo.setCurrentIndex(
            self.lang_combo.findData(str(s.value("appearance/language", "auto")))
        )
        form.addRow(self.tr("Language:"), self.lang_combo)

        note = QLabel(self.tr("Language changes take effect after restart."))
        note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", note)

        self.date_combo = QComboBox()
        self.date_combo.addItem(self.tr("Local (2024-03-05 10:30)"), "local")
        self.date_combo.addItem(self.tr("ISO 8601 (2024-03-05T10:30)"), "iso")
        self.date_combo.addItem(self.tr("RFC (Tue, 05 Mar 2024 10:30)"), "rfc")
        self.date_combo.setCurrentIndex(
            max(0, self.date_combo.findData(str(s.value("appearance/dateFormat", "local"))))
        )
        form.addRow(self.tr("Date format:"), self.date_combo)

        self.font_delta = QSpinBox()
        self.font_delta.setRange(-4, 12)
        self.font_delta.setSuffix(" pt")
        self.font_delta.setValue(int(s.value("viewer/fontDelta", 0) or 0))
        form.addRow(self.tr("Message text size:"), self.font_delta)

        self.prefer_text = QCheckBox(self.tr("Prefer plain text when a message has both"))
        self.prefer_text.setChecked(bool(s.value("viewer/preferPlainText", False, type=bool)))
        form.addRow("", self.prefer_text)

        self.auto_remote = QCheckBox(self.tr("Load remote content in messages automatically"))
        self.auto_remote.setChecked(bool(s.value("viewer/autoLoadRemote", False, type=bool)))
        form.addRow("", self.auto_remote)

        self.check_updates = QCheckBox(self.tr("Check for updates on startup"))
        self.check_updates.setChecked(bool(s.value("updates/checkOnStartup", False, type=bool)))
        form.addRow("", self.check_updates)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        s = QSettings()
        mode = theme.ThemeMode(self.theme_combo.currentData())
        theme.apply(QApplication.instance(), mode)
        theme.save_mode(mode)
        parent = self.parent()
        if parent is not None and hasattr(parent, "sync_theme_menu"):
            parent.sync_theme_menu(mode)
        s.setValue("appearance/language", self.lang_combo.currentData())
        s.setValue("appearance/dateFormat", self.date_combo.currentData())
        s.setValue("viewer/fontDelta", self.font_delta.value())
        s.setValue("viewer/preferPlainText", self.prefer_text.isChecked())
        s.setValue("viewer/autoLoadRemote", self.auto_remote.isChecked())
        s.setValue("updates/checkOnStartup", self.check_updates.isChecked())
        if parent is not None and hasattr(parent, "apply_viewer_prefs"):
            parent.apply_viewer_prefs()
        self.accept()

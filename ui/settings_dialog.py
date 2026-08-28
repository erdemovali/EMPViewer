"""Preferences dialog: theme, language, remote-content default."""

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
)

from ui import theme


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
        s.setValue("viewer/autoLoadRemote", self.auto_remote.isChecked())
        s.setValue("updates/checkOnStartup", self.check_updates.isChecked())
        self.accept()

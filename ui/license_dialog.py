"""A read-only viewer for EMPViewer's own and third-party license texts."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QDialog, QPlainTextEdit, QVBoxLayout

from utils.helpers import resource_path

_SEP = "\n\n" + "=" * 72 + "\n\n"


def _combined_text() -> str:
    parts: list[str] = []
    for name in ("LICENSE", "assets/THIRD_PARTY_LICENSES.txt"):
        try:
            parts.append(resource_path(name).read_text(encoding="utf-8").strip())
        except OSError:
            pass
    return _SEP.join(parts) if parts else "License files were not found in this build."


class LicenseDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Licenses"))
        self.resize(660, 540)

        lay = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        font = QFont()
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamily("monospace")
        view.setFont(font)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setPlainText(_combined_text())
        lay.addWidget(view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

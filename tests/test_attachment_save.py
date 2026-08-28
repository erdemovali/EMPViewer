"""Attachment saving: the "Save all" batch path must neutralise hostile names."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from parsers.models import Attachment, EmailMessage
from ui.viewer_widget import ViewerWidget

_app = QApplication.instance() or QApplication([])


def test_save_all_attachments_cannot_escape_the_target_dir(tmp_path, monkeypatch) -> None:
    outside = tmp_path / "outside.txt"
    dest = tmp_path / "dest"
    dest.mkdir()

    msg = EmailMessage(
        subject="hi",
        attachments=[
            Attachment(filename="../../outside.txt", mime_type="text/plain", data=b"evil"),
            Attachment(filename="good.txt", mime_type="text/plain", data=b"ok"),
        ],
    )

    w = ViewerWidget()
    w.set_message(msg)

    monkeypatch.setattr(
        "ui.viewer_widget.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(dest)),
    )
    monkeypatch.setattr(
        "ui.viewer_widget.QMessageBox.information", staticmethod(lambda *a, **k: None)
    )

    w.save_all_attachments()

    assert not outside.exists(), "path traversal escaped the chosen directory"
    written = sorted(p.name for p in dest.iterdir())
    assert written == ["good.txt", "outside.txt"]
    assert (dest / "outside.txt").read_bytes() == b"evil"

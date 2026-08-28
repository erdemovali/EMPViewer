"""Process-wide logging + a last-resort crash handler.

`configure()` is called once from :mod:`main`; everything else uses the stdlib
``logging`` module with a logger name under ``empviewer.*``. Nothing here needs a
running ``QApplication`` - the crash dialog is only shown if one happens to
exist.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_NAME = "empviewer"
_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False
_log_file: Path | None = None


def _log_dir() -> Path:
    """Writable per-user log directory, with or without Qt / an app name."""

    base: Path | None = None
    try:  # pragma: no cover - depends on the platform / Qt presence
        from PySide6.QtCore import QStandardPaths

        loc = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if loc:
            base = Path(loc)
    except Exception:
        base = None
    if base is None:
        base = Path(tempfile.gettempdir())
    if base.name.lower() != "empviewer":
        base = base / "EMPViewer"
    return base / "logs"


def log_file() -> Path | None:
    """Path to the active log file, or ``None`` if file logging is unavailable."""

    return _log_file


def configure(debug: bool = False) -> Path | None:
    """Install console + rotating-file handlers on the root logger (idempotent)."""

    global _configured, _log_file

    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()

    if _configured:
        root.setLevel(level)
        return _log_file

    root.setLevel(level)
    fmt = logging.Formatter(_FMT)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if debug else logging.WARNING)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        _log_file = d / "empviewer.log"
        fh = RotatingFileHandler(
            _log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        _log_file = None

    _configured = True
    logging.getLogger(_LOG_NAME).info(
        "logging started (debug=%s) -> %s", debug, _log_file or "console only"
    )
    return _log_file


def _show_crash_dialog(text: str) -> None:  # pragma: no cover - GUI path
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("EMPViewer - unexpected error")
        box.setText("EMPViewer hit an unexpected error and may be unstable.")
        box.setDetailedText(text)
        copy_btn = box.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
        open_btn = None
        if _log_file is not None:
            open_btn = box.addButton("Open log", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is copy_btn:
            QApplication.clipboard().setText(text)
        elif open_btn is not None and clicked is open_btn:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_log_file)))
    except Exception:
        pass


def install_excepthook(show_dialog: bool = True) -> None:
    """Log any unhandled exception (and optionally show a dialog) before exit."""

    prev = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            prev(exc_type, exc, tb)
            return
        logging.getLogger(_LOG_NAME).critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb)
        )
        if show_dialog:
            _show_crash_dialog("".join(traceback.format_exception(exc_type, exc, tb)))
        prev(exc_type, exc, tb)

    sys.excepthook = hook


def debug_requested(argv: list[str] | None = None) -> bool:
    """True if ``--debug`` is on the command line or ``EMPVIEWER_DEBUG=1``."""

    argv = sys.argv if argv is None else argv
    return "--debug" in argv or os.environ.get("EMPVIEWER_DEBUG") == "1"

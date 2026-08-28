"""EMPViewer entry point.

Handles both OS double-click mechanisms:

* **Windows / Linux** - the file path arrives as ``sys.argv[1]`` (and, for a
  single-instance launch, subsequent paths are forwarded over a local socket).
* **macOS** - Finder delivers a ``QFileOpenEvent``; :class:`EMPViewerApplication`
  overrides :meth:`event` to catch it, including events that fire before the main
  window exists.

Also provides ``--register`` / ``--unregister`` on Windows to associate the
mail extensions with this executable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QLocale,
    QSettings,
    QTimer,
    QTranslator,
    Signal,
)
from PySide6.QtWidgets import QApplication

# Make ``python main.py`` work regardless of the current directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui import theme  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from utils.branding import make_app_icon  # noqa: E402
from utils.helpers import is_supported_file, resource_path  # noqa: E402

APP_NAME = "EMPViewer"
ORG_NAME = "EMPViewer"


class EMPViewerApplication(QApplication):
    """QApplication that surfaces macOS file-open events as a Qt signal."""

    fileOpenRequested = Signal(str)

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._pending_open: list[str] = []
        self._ready = False

    def event(self, e: QEvent) -> bool:  # noqa: D401
        if e.type() == QEvent.Type.FileOpen:
            path = e.file() or (e.url().toLocalFile() if e.url() else "")
            if path:
                if self._ready:
                    self.fileOpenRequested.emit(path)
                else:
                    self._pending_open.append(path)
            return True
        return super().event(e)

    def flush_pending(self) -> None:
        self._ready = True
        for path in self._pending_open:
            self.fileOpenRequested.emit(path)
        self._pending_open.clear()


def _collect_cli_paths(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if not a.startswith("-") and is_supported_file(a)]


def _install_translators(app: QApplication) -> None:
    """Load the UI translation for the configured / system language.

    Language preference lives in QSettings ``appearance/language``
    (``auto`` | ``en`` | ``tr``); ``auto`` follows the OS locale.
    """

    pref = str(QSettings().value("appearance/language", "auto"))
    lang = QLocale.system().name().split("_")[0] if pref == "auto" else pref
    if lang == "en":
        return

    app_tr = QTranslator(app)
    qm = resource_path("translations") / f"empviewer_{lang}.qm"
    if qm.exists() and app_tr.load(str(qm)):
        app.installTranslator(app_tr)

    qt_tr = QTranslator(app)
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_tr.load(QLocale(lang), "qtbase", "_", qt_dir):
        app.installTranslator(qt_tr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    _win_actions = {"--register", "--unregister", "--set-default"}
    if sys.platform.startswith("win") and _win_actions.intersection(argv):
        from utils import win_integration

        if "--unregister" in argv:
            return win_integration.unregister()
        if "--set-default" in argv:
            return win_integration.set_default()
        return win_integration.register()

    app = EMPViewerApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain("empviewer.local")
    app.setWindowIcon(make_app_icon())

    _install_translators(app)
    theme.apply(app, theme.load_mode())

    window = MainWindow()
    app.fileOpenRequested.connect(window.load_external_path)
    window.show()

    # Files passed on the command line (Windows / Linux double-click, CLI use).
    for path in _collect_cli_paths(argv):
        QTimer.singleShot(0, lambda p=path: window.load_external_path(p))

    # Release any macOS FileOpen events queued before the window existed.
    QTimer.singleShot(0, app.flush_pending)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

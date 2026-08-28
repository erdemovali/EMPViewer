"""Background workers so file parsing never blocks the GUI thread.

Every task is a :class:`QRunnable` submitted to the global
:class:`QThreadPool`. Results and errors come back on the GUI thread through
queued-connection signals carried by :class:`WorkerSignals`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from parsers.errors import ParserError
from parsers.models import EmailMessage, MessageStub
from parsers.pst_parser import FolderId, MessageId, PstBackend

log = logging.getLogger("empviewer.workers")


class WorkerSignals(QObject):
    """Signals shared by every worker. A fresh instance is made per task."""

    #: Emitted with the task's result object on success.
    finished = Signal(object)
    #: Emitted with a user-facing error string on failure.
    error = Signal(str)
    #: Emitted as ``(percent, message)``; ``percent`` is -1 when indeterminate.
    progress = Signal(int, str)


class _BaseRunnable(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class FnRunnable(_BaseRunnable):
    """Run an arbitrary callable off-thread.

    Used for opening ``.eml`` / ``.msg`` files and for :func:`parsers.pst_parser.open_pst`.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        pass_cancel: bool = False,
        pass_progress: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        #: When True, ``fn`` is called with an extra ``should_cancel`` keyword
        #: that returns this runnable's cancelled flag - lets a long operation
        #: bail out mid-flight instead of only at the boundaries.
        self._pass_cancel = pass_cancel
        #: When True, ``fn`` gets an ``on_progress(done, total)`` keyword that
        #: forwards to :attr:`WorkerSignals.progress` as ``(percent, "")``.
        self._pass_progress = pass_progress

    def _emit_progress(self, done: int, total: int) -> None:
        if self._cancelled:
            return
        pct = int(done * 100 / total) if total else -1
        self.signals.progress.emit(pct, "")

    @Slot()
    def run(self) -> None:  # noqa: D401
        if self._cancelled:
            return
        kwargs = dict(self._kwargs)
        if self._pass_cancel:
            kwargs["should_cancel"] = lambda: self._cancelled
        if self._pass_progress:
            kwargs["on_progress"] = self._emit_progress
        try:
            result = self._fn(*self._args, **kwargs)
        except ParserError as exc:
            log.info("%s failed: %s", getattr(self._fn, "__name__", self._fn), exc.message)
            if not self._cancelled:
                self.signals.error.emit(exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in %s", getattr(self._fn, "__name__", self._fn))
            if not self._cancelled:
                self.signals.error.emit(f"Unexpected error: {exc}")
            return
        if not self._cancelled:
            self.signals.finished.emit(result)


class ListMessagesRunnable(_BaseRunnable):
    """Load the message list for one PST folder. Emits ``list[MessageStub]``."""

    def __init__(self, backend: PstBackend, folder_id: FolderId) -> None:
        super().__init__()
        self._backend = backend
        self._folder_id = folder_id

    @Slot()
    def run(self) -> None:
        if self._cancelled:
            return
        try:
            stubs: list[MessageStub] = self._backend.list_messages(
                self._folder_id, should_cancel=lambda: self._cancelled
            )
        except ParserError as exc:
            log.info("list_messages(%r) failed: %s", self._folder_id, exc.message)
            if not self._cancelled:
                self.signals.error.emit(exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not list folder %r", self._folder_id)
            if not self._cancelled:
                self.signals.error.emit(f"Could not list this folder: {exc}")
            return
        if not self._cancelled:
            self.signals.finished.emit(stubs)


class GetMessageRunnable(_BaseRunnable):
    """Load one full PST message. Emits :class:`EmailMessage`."""

    def __init__(self, backend: PstBackend, message_id: MessageId) -> None:
        super().__init__()
        self._backend = backend
        self._message_id = message_id

    @Slot()
    def run(self) -> None:
        if self._cancelled:
            return
        try:
            message: EmailMessage = self._backend.get_message(self._message_id)
        except ParserError as exc:
            log.info("get_message(%r) failed: %s", self._message_id, exc.message)
            if not self._cancelled:
                self.signals.error.emit(exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not open message %r", self._message_id)
            if not self._cancelled:
                self.signals.error.emit(f"Could not open this message: {exc}")
            return
        if not self._cancelled:
            self.signals.finished.emit(message)


def submit(runnable: QRunnable) -> None:
    """Hand a runnable to the global thread pool."""

    QThreadPool.globalInstance().start(runnable)

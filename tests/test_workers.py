"""utils.workers: cancellation semantics and error mapping.

The runnables are exercised directly (``run()`` on the calling thread) so the
assertions are deterministic - no thread pool involved.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from parsers.errors import CorruptFileError
from utils import workers

_app = QApplication.instance() or QApplication([])


def _collect(sig):
    out = []
    sig.connect(lambda *a: out.append(a[0] if len(a) == 1 else a))
    return out


def test_fn_runnable_success() -> None:
    r = workers.FnRunnable(lambda x: x + 1, 41)
    done = _collect(r.signals.finished)
    err = _collect(r.signals.error)
    r.run()
    assert done == [42]
    assert err == []


def test_fn_runnable_maps_parser_error_to_message() -> None:
    def boom():
        raise CorruptFileError("f.pst", "broken store")

    r = workers.FnRunnable(boom)
    err = _collect(r.signals.error)
    r.run()
    assert err and "broken store" in err[0]


def test_fn_runnable_wraps_unexpected_error() -> None:
    r = workers.FnRunnable(lambda: 1 / 0)
    err = _collect(r.signals.error)
    r.run()
    assert err and err[0].startswith("Unexpected error:")


def test_cancel_suppresses_the_result() -> None:
    r = workers.FnRunnable(lambda: "result")
    done = _collect(r.signals.finished)
    err = _collect(r.signals.error)
    r.cancel()
    r.run()
    assert done == [] and err == []
    assert r.cancelled is True


class _FakeBackend:
    def list_messages(self, folder_id, *, should_cancel=None):
        if folder_id == "bad":
            raise RuntimeError("kaboom")
        return ["stub-a", "stub-b"]

    def get_message(self, message_id):
        raise CorruptFileError("x.pst", "message unreadable")


def test_list_messages_runnable_error_text() -> None:
    r = workers.ListMessagesRunnable(_FakeBackend(), "bad")
    err = _collect(r.signals.error)
    r.run()
    assert err and "Could not list this folder" in err[0]


def test_get_message_runnable_uses_parser_message() -> None:
    r = workers.GetMessageRunnable(_FakeBackend(), ("f", 0))
    err = _collect(r.signals.error)
    r.run()
    assert err and "message unreadable" in err[0]


def test_pass_progress_forwards_percentages() -> None:
    seen = []

    def work(*, on_progress):
        on_progress(1, 4)
        on_progress(4, 4)
        return "done"

    r = workers.FnRunnable(work, pass_progress=True)
    r.signals.progress.connect(lambda pct, _m: seen.append(pct))
    done = _collect(r.signals.finished)
    r.run()
    assert seen == [25, 100]
    assert done == ["done"]


def test_pass_cancel_and_progress_together() -> None:
    def work(*, should_cancel, on_progress):
        assert should_cancel() is False
        on_progress(0, 0)  # indeterminate -> -1
        return "ok"

    r = workers.FnRunnable(work, pass_cancel=True, pass_progress=True)
    seen = []
    r.signals.progress.connect(lambda pct, _m: seen.append(pct))
    r.run()
    assert seen == [-1]

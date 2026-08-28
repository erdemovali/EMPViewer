"""Tests for utils.logging_setup (console + rotating file + crash hook)."""

from __future__ import annotations

import logging
import sys

from utils import logging_setup


def test_debug_requested_flag_and_env(monkeypatch) -> None:
    monkeypatch.delenv("EMPVIEWER_DEBUG", raising=False)
    assert logging_setup.debug_requested(["prog", "--debug"]) is True
    assert logging_setup.debug_requested(["prog", "file.eml"]) is False
    monkeypatch.setenv("EMPVIEWER_DEBUG", "1")
    assert logging_setup.debug_requested(["prog"]) is True


def test_configure_writes_a_log_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging_setup, "_configured", False)
    monkeypatch.setattr(logging_setup, "_log_file", None)
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        monkeypatch.setattr(logging_setup, "_log_dir", lambda: tmp_path / "logs")
        path = logging_setup.configure(debug=True)
        assert path is not None and path.exists()
        logging.getLogger("empviewer.test").warning("marker-line")
        for h in root.handlers:
            h.flush()
        assert "marker-line" in path.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved
        logging_setup._configured = False
        logging_setup._log_file = None


def test_install_excepthook_logs_and_chains(monkeypatch, caplog) -> None:
    calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: calls.append(a))
    logging_setup.install_excepthook(show_dialog=False)
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    with caplog.at_level(logging.CRITICAL, logger="empviewer"):
        sys.excepthook(*exc_info)
    assert any("Unhandled exception" in r.message for r in caplog.records)
    assert calls, "previous excepthook should still be called"
    sys.excepthook = sys.__excepthook__

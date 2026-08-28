"""utils.updates.UpdateChecker.check with a faked QNetworkAccessManager.

No real network: a fake reply carries a real Qt ``finished`` signal that the
test emits by hand to drive the callback.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

from utils import updates

_app = QApplication.instance() or QApplication([])


class _FakeByteArray:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def data(self) -> bytes:
        return self._raw


class _FakeReply(QObject):
    finished = Signal()

    def __init__(self, body: bytes, err=QNetworkReply.NetworkError.NoError) -> None:
        super().__init__()
        self._body = body
        self._err = err

    def error(self):
        return self._err

    def readAll(self):  # noqa: N802
        return _FakeByteArray(self._body)

    def deleteLater(self) -> None:  # noqa: N802
        pass


class _FakeNam:
    def __init__(self, reply: _FakeReply) -> None:
        self._reply = reply
        self.requested_url = None

    def get(self, req):
        self.requested_url = req.url().toString()
        return self._reply


def _run_check(monkeypatch, body: bytes, err=QNetworkReply.NetworkError.NoError):
    reply = _FakeReply(body, err)
    nam = _FakeNam(reply)
    monkeypatch.setattr(updates, "current_version", lambda: "1.0.3")

    checker = updates.UpdateChecker()
    checker._nam = nam

    captured = {}
    checker.check(lambda latest, newer, url: captured.update(
        latest=latest, newer=newer, url=url))
    reply.finished.emit()
    return captured, nam


def test_newer_release_is_reported(monkeypatch) -> None:
    captured, nam = _run_check(monkeypatch, json.dumps({"tag_name": "v1.1.0"}).encode())
    assert nam.requested_url.endswith("/releases/latest")
    assert captured == {
        "latest": "v1.1.0",
        "newer": True,
        "url": updates.RELEASES_PAGE,
    }


def test_same_version_is_not_newer(monkeypatch) -> None:
    captured, _ = _run_check(monkeypatch, json.dumps({"tag_name": "v1.0.3"}).encode())
    assert captured["latest"] == "v1.0.3"
    assert captured["newer"] is False


def test_network_error_yields_none(monkeypatch) -> None:
    captured, _ = _run_check(
        monkeypatch, b"", err=QNetworkReply.NetworkError.HostNotFoundError
    )
    assert captured["latest"] is None
    assert captured["newer"] is False


def test_garbage_json_is_tolerated(monkeypatch) -> None:
    captured, _ = _run_check(monkeypatch, b"<<not json>>")
    assert captured["latest"] is None

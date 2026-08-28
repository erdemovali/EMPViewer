"""Viewer: raw-source view, zoom persistence, prefer-plain-text, sender key,
per-sender remote allowlist, date-format setting."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication

from parsers.models import EmailMessage
from ui.viewer_widget import RemoteBlockingBrowser, ViewerWidget, _sender_key
from utils.helpers import format_datetime

_app = QApplication.instance() or QApplication([])
_RES = 2


def _settings(**kv):
    s = QSettings("EMPViewerTest", "prefs")
    s.clear()
    for k, v in kv.items():
        s.setValue(k.replace("_", "/", 1), v)
    return s


def test_sender_key_extracts_bare_address() -> None:
    assert _sender_key("Alice Example <ALICE@Example.COM>") == "alice@example.com"
    assert _sender_key("no address here") == ""


def test_format_datetime_styles() -> None:
    dt = datetime(2024, 3, 5, 10, 30, tzinfo=timezone.utc)
    assert format_datetime(dt, style="local").startswith("2024-03-05 ")
    assert "T" in format_datetime(dt, style="iso")
    assert format_datetime(dt, style="rfc").startswith(("Tue", "Mon", "Wed"))


def test_raw_source_view_shows_verbatim_bytes(monkeypatch) -> None:
    monkeypatch.setattr("ui.viewer_widget.QSettings", lambda *a, **k: _settings())
    v = ViewerWidget()
    m = EmailMessage(subject="s", body_html="<p>rendered</p>",
                     raw_source=b"X-Secret-Header: 42\r\n\r\nverbatim body")
    v.set_message(m)
    v.set_source_mode(True)
    text = v.browser.toPlainText()
    assert "X-Secret-Header: 42" in text
    assert "verbatim body" in text


def test_per_sender_allowlist_unblocks_http(monkeypatch) -> None:
    b = RemoteBlockingBrowser()
    b.sender_key = "trusted@x.com"
    b.allowed_senders = {"trusted@x.com"}
    hits = []
    b.remoteContentBlocked.connect(lambda: hits.append(1))
    # allowed sender -> delegates instead of emitting "blocked"
    b.loadResource(_RES, QUrl("http://imgs.example/x.png"))
    assert hits == []

    b.sender_key = "someone-else@x.com"
    b.loadResource(_RES, QUrl("http://imgs.example/x.png"))
    assert hits == [1]


def test_copy_body_strips_object_replacement_chars(monkeypatch) -> None:
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr("ui.viewer_widget.QSettings", lambda *a, **k: _settings())
    v = ViewerWidget()
    cb = QGuiApplication.clipboard()

    # Image-only HTML: QTextBrowser.toPlainText() is just U+FFFC runs, but the
    # message has a real text/plain part -> that is what must be copied.
    v.set_message(EmailMessage(
        subject="s", sender="a@x",
        body_html="<body><img src='cid:a'><img src='cid:b'></body>",
        body_text="the real plain text",
    ))
    cb.clear()
    v.copy_body()
    assert cb.text() == "the real plain text"

    # HTML text interleaved with an image: the U+FFFC is dropped, text kept.
    v.set_message(EmailMessage(subject="s", sender="a@x",
                               body_html="<p>Hello <img src='cid:x'> world</p>"))
    cb.clear()
    v.copy_body()
    assert "￼" not in cb.text()
    assert "Hello" in cb.text() and "world" in cb.text()

    # Genuinely empty message: clipboard is left untouched.
    cb.setText("SENTINEL")
    v.set_message(EmailMessage(subject="s", sender="a@x"))
    v.copy_body()
    assert cb.text() == "SENTINEL"


def test_zoom_by_persists_and_clamps(monkeypatch) -> None:
    store = _settings()
    monkeypatch.setattr("ui.viewer_widget.QSettings", lambda *a, **k: store)
    v = ViewerWidget()
    v.set_message(EmailMessage(subject="s", body_text="hi"))
    v.zoom_by(3)
    assert store.value("viewer/fontDelta", type=int) == 3
    for _ in range(20):
        v.zoom_by(1)
    assert v._zoom <= 16
    v.zoom_reset()
    assert store.value("viewer/fontDelta", type=int) == 0

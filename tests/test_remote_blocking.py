"""The remote-content security gate in ui.viewer_widget.RemoteBlockingBrowser."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from parsers.models import Attachment
from ui.viewer_widget import RemoteBlockingBrowser

_app = QApplication.instance() or QApplication([])

_RES = 2  # QTextDocument.ResourceType.ImageResource - value is irrelevant here


def _browser() -> RemoteBlockingBrowser:
    b = RemoteBlockingBrowser()
    b.set_inline_resources(
        {"logo@x": Attachment(filename="logo.png", mime_type="image/png",
                              data=b"PNGDATA", is_inline=True, content_id="logo@x")}
    )
    return b


def test_http_is_blocked_until_opted_in() -> None:
    b = _browser()
    hits = []
    b.remoteContentBlocked.connect(lambda: hits.append(1))

    data = b.loadResource(_RES, QUrl("http://tracker.example/pixel.gif"))
    assert bytes(data) == b""
    assert hits == [1]

    b.allow_remote = True
    # Now it delegates to QTextBrowser (which will fail to fetch offline, but the
    # point is our gate no longer short-circuits it).
    b.loadResource(_RES, QUrl("http://tracker.example/pixel.gif"))
    assert hits == [1]  # no new "blocked" signal


def test_cid_is_served_from_the_message() -> None:
    b = _browser()
    data = b.loadResource(_RES, QUrl("cid:logo@x"))
    assert bytes(data) == b"PNGDATA"


def test_cid_stem_fallback() -> None:
    b = _browser()
    # Referenced as a bare stem; stored with an @host suffix.
    data = b.loadResource(_RES, QUrl("cid:logo"))
    assert bytes(data) == b"PNGDATA"


def test_unknown_cid_returns_empty() -> None:
    b = _browser()
    assert bytes(b.loadResource(_RES, QUrl("cid:nope"))) == b""


def test_exotic_schemes_are_dropped() -> None:
    b = _browser()
    for url in ("ftp://x/y", "javascript:alert(1)", "about:blank"):
        assert bytes(b.loadResource(_RES, QUrl(url))) == b""

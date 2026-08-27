"""Inline (cid:) image handling in the viewer."""

from __future__ import annotations

import base64
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from parsers.models import Attachment  # noqa: E402
from ui.viewer_widget import _inline_cid_images, _lookup_cid  # noqa: E402

# A valid 1x1 transparent PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg=="
)


def _att(cid: str, name: str = "logo.png") -> Attachment:
    return Attachment(filename=name, mime_type="image/png", data=_PNG, is_inline=True, content_id=cid)


def test_exact_cid_is_inlined() -> None:
    html = '<p>hi</p><img src="cid:logo123">'
    out = _inline_cid_images(html, {"logo123": _att("logo123")})
    assert "cid:logo123" not in out
    assert "data:image/png;base64," in out


def test_outlook_style_cid_with_domain_suffix() -> None:
    html = '<img src="cid:image001.png@01DA1234.56789ABC">'
    out = _inline_cid_images(html, {"image001.png": _att("image001.png")})
    assert out.startswith("<img src=\"data:image/png;base64,")


def test_cid_in_css_url_is_inlined() -> None:
    html = '<div style="background:url(cid:bg1)">x</div>'
    out = _inline_cid_images(html, {"bg1": _att("bg1", "bg.png")})
    assert "url(data:image/png;base64," in out


def test_unknown_cid_left_untouched() -> None:
    html = '<img src="cid:missing">'
    assert _inline_cid_images(html, {"other": _att("other")}) == html


def test_no_op_when_no_cid() -> None:
    html = "<p>plain</p>"
    assert _inline_cid_images(html, {"x": _att("x")}) is html


def test_lookup_by_filename_fallback() -> None:
    by_cid = {"weird-generated-id": _att("weird-generated-id", "picture.png")}
    assert _lookup_cid(by_cid, "picture.png") is not None


def test_viewer_renders_inline_image_end_to_end() -> None:
    from PySide6.QtWidgets import QApplication

    from parsers.models import EmailMessage

    app = QApplication.instance() or QApplication([])
    from ui.viewer_widget import ViewerWidget

    v = ViewerWidget()
    msg = EmailMessage(
        subject="pic",
        sender="a@b.c",
        body_html='<p>see</p><img src="cid:pic1">',
        attachments=[_att("pic1")],
    )
    v.set_message(msg)
    app.processEvents()
    # The data URI must have made it into the rendered document, and the inline
    # image must not appear as a downloadable attachment chip.
    assert "data:image/png;base64," in v.browser.toHtml()
    assert v._attach_layout.count() == 0

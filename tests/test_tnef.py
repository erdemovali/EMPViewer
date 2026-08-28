"""TNEF / winmail.dat expansion (parsers._tnef) via the .eml parser."""

from __future__ import annotations

from email.message import EmailMessage as PyMsg
from pathlib import Path

import pytest

from parsers._tnef import is_tnef, merge
from parsers.eml_parser import parse_eml_bytes
from parsers.models import Attachment

_DATA = Path(__file__).parent / "data"
pytest.importorskip("tnefparse")

TWO_FILES = (_DATA / "two-files.tnef").read_bytes()
BODY = (_DATA / "body.tnef").read_bytes()


def test_is_tnef_matches_name_or_mime() -> None:
    assert is_tnef("winmail.dat", "application/octet-stream")
    assert is_tnef("whatever", "application/ms-tnef")
    assert not is_tnef("report.pdf", "application/pdf")


def test_merge_expands_a_winmail_attachment() -> None:
    atts = [
        Attachment(filename="report.pdf", mime_type="application/pdf", data=b"%PDF"),
        Attachment(filename="winmail.dat", mime_type="application/ms-tnef", data=TWO_FILES),
    ]
    out, html, text = merge(atts, None, None)
    names = sorted(a.filename for a in out)
    assert "report.pdf" in names
    assert "winmail.dat" not in names
    assert {"AUTHORS", "README"} <= set(names)


def test_merge_fills_body_from_tnef_when_absent() -> None:
    atts = [Attachment(filename="winmail.dat", mime_type="application/ms-tnef", data=BODY)]
    out, html, text = merge(atts, None, None)
    assert html and "html" in html.lower()
    assert out == []  # body.tnef carries no file attachments


def test_eml_with_winmail_attachment_is_expanded() -> None:
    inner = PyMsg()
    inner["Subject"] = "Fwd"
    inner["From"] = "a@x.com"
    inner.set_content("see attached")
    inner.add_attachment(
        TWO_FILES, maintype="application", subtype="ms-tnef", filename="winmail.dat"
    )
    msg = parse_eml_bytes(inner.as_bytes())
    names = sorted(a.filename for a in msg.visible_attachments)
    assert "winmail.dat" not in names
    assert {"AUTHORS", "README"} <= set(names)

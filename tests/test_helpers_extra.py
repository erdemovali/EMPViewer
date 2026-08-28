"""Coverage for the smaller utils.helpers functions."""

from __future__ import annotations

import sys
from pathlib import Path

from utils import helpers


def test_resource_path_from_source_tree() -> None:
    p = helpers.resource_path("VERSION")
    assert p.is_file()
    assert p.name == "VERSION"
    assert p.parent == Path(__file__).resolve().parent.parent


def test_resource_path_when_frozen(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert helpers.resource_path("translations") == tmp_path / "translations"


def test_is_pst_like_and_filter_supported(tmp_path) -> None:
    eml = tmp_path / "a.eml"
    eml.write_bytes(b"Subject: x\r\n\r\nhi")
    pst = tmp_path / "b.pst"
    pst.write_bytes(b"!BDN")
    junk = tmp_path / "c.zip"
    junk.write_bytes(b"PK")

    assert helpers.is_pst_like(pst) is True
    assert helpers.is_pst_like(eml) is False
    assert set(helpers.filter_supported([eml, pst, junk, tmp_path / "missing.eml"])) == {
        str(eml), str(pst)
    }


def test_open_with_os_prefers_qt(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "PySide6.QtGui.QDesktopServices.openUrl",
        staticmethod(lambda url: calls.append(url.toLocalFile()) or True),
    )
    assert helpers.open_with_os("/tmp/whatever.pdf") is True
    assert calls and calls[0].endswith("whatever.pdf")

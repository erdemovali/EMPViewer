"""Full-coverage tests for the .eml parser (no third-party deps needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsers.eml_parser import parse_eml, parse_eml_bytes
from parsers.errors import CorruptFileError


def test_headers_and_addresses(sample_eml_bytes: bytes) -> None:
    msg = parse_eml_bytes(sample_eml_bytes)
    assert msg.subject == "Quarterly report"
    assert msg.sender == "Alice Example <alice@example.com>"
    assert msg.to == ["Bob <bob@example.com>", "carol@example.com"]
    assert msg.cc == ["dave@example.com"]
    assert msg.date is not None
    assert msg.date.year == 2024 and msg.date.month == 3 and msg.date.day == 5
    assert msg.headers["Message-ID"] == "<abc123@example.com>"


def test_bodies(sample_eml_bytes: bytes) -> None:
    msg = parse_eml_bytes(sample_eml_bytes)
    assert msg.body_text is not None and "Plain text body" in msg.body_text
    assert msg.body_html is not None and "HTML body" in msg.body_html


def test_inline_and_regular_attachments(sample_eml_bytes: bytes) -> None:
    msg = parse_eml_bytes(sample_eml_bytes)

    inline = msg.inline_by_cid
    assert "logo123" in inline
    assert inline["logo123"].is_inline is True
    assert inline["logo123"].mime_type == "image/png"

    visible = msg.visible_attachments
    assert [a.filename for a in visible] == ["data.csv"]
    assert visible[0].data == b"col1,col2\n1,2\n"
    assert visible[0].is_inline is False


def test_parse_from_disk(sample_eml_file: Path) -> None:
    msg = parse_eml(sample_eml_file)
    assert msg.source_path == str(sample_eml_file)
    assert msg.display_name == "Quarterly report"


def test_plain_text_only_message() -> None:
    raw = b"Subject: Hi\r\nFrom: x@y.z\r\n\r\njust text\r\n"
    msg = parse_eml_bytes(raw)
    assert msg.body_text is not None and "just text" in msg.body_text
    assert msg.body_html is None
    assert msg.visible_attachments == []


def test_missing_file_raises_corrupt() -> None:
    with pytest.raises(CorruptFileError):
        parse_eml(Path("does-not-exist-12345.eml"))


def test_garbage_bytes_do_not_crash() -> None:
    # The email module is extremely lenient; this should still produce a message
    # object rather than raising.
    msg = parse_eml_bytes(b"\x00\x01\x02 not really an email")
    assert msg.subject == ""


def test_wrong_charset_falls_back(tmp_path: Path) -> None:
    raw = (
        b"Subject: Encoding test\r\n"
        b"Content-Type: text/plain; charset=us-ascii\r\n\r\n"
        + "café — naïve".encode("utf-8")
    )
    msg = parse_eml_bytes(raw)
    assert "caf" in (msg.body_text or "")

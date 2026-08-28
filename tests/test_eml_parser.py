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


def test_all_headers_kept_and_raw_source(sample_eml_bytes: bytes) -> None:
    raw = sample_eml_bytes.replace(
        b"Message-ID: <abc123@example.com>",
        b"Message-ID: <abc123@example.com>\r\nX-Weird-Custom: keep-me",
    )
    msg = parse_eml_bytes(raw)
    # No whitelist any more: an arbitrary header survives.
    assert msg.headers.get("X-Weird-Custom") == "keep-me"
    # Verbatim bytes are retained for a real "View Source".
    assert msg.raw_source == raw
    assert msg.size == len(raw)


def test_threading_and_importance_fields() -> None:
    raw = (
        b"Subject: Re: plan\r\n"
        b"From: a@x.com\r\n"
        b"Message-ID: <child@x.com>\r\n"
        b"In-Reply-To: <root@x.com>\r\n"
        b"References: <root@x.com> <mid@x.com>\r\n"
        b"Importance: high\r\n\r\nbody\r\n"
    )
    msg = parse_eml_bytes(raw)
    assert msg.message_id == "<child@x.com>"
    assert msg.in_reply_to == "<root@x.com>"
    assert msg.references == ["<root@x.com>", "<mid@x.com>"]
    assert msg.importance == "high"


def test_message_rfc822_becomes_a_navigable_attachment() -> None:
    inner = (
        b"From: Deep Sender <deep@x.com>\r\n"
        b"Subject: the inner one\r\n\r\n"
        b"inner body text\r\n"
    )
    outer = (
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"Subject: carrier\r\nFrom: a@x.com\r\n\r\n"
        b"--B\r\nContent-Type: text/plain\r\n\r\nplease see attached\r\n"
        b"--B\r\nContent-Type: message/rfc822\r\n"
        b'Content-Disposition: attachment; filename="fwd.eml"\r\n\r\n'
        + inner
        + b"\r\n--B--\r\n"
    )
    msg = parse_eml_bytes(outer)

    # The inner body must NOT have leaked into the carrier.
    assert "inner body text" not in (msg.body_text or "")
    assert "please see attached" in (msg.body_text or "")

    atts = msg.visible_attachments
    assert len(atts) == 1
    att = atts[0]
    assert att.attach_kind == "message"
    assert att.embedded is not None
    assert att.embedded.subject == "the inner one"
    assert "inner body text" in (att.embedded.body_text or "")
    assert att.embedded.sender == "Deep Sender <deep@x.com>"


def test_smime_signed_is_detected() -> None:
    raw = (
        b'Content-Type: multipart/signed; protocol="application/pkcs7-signature";'
        b' micalg=sha-256; boundary="b"\r\n'
        b"Subject: signed\r\nFrom: a@x.com\r\n\r\n"
        b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
        b"--b\r\nContent-Type: application/pkcs7-signature\r\n\r\nAAAA\r\n--b--\r\n"
    )
    msg = parse_eml_bytes(raw)
    assert msg.is_signed is True
    assert msg.is_encrypted is False

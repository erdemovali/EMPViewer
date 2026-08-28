"""parsers.export.to_eml_bytes - rebuilding an .eml from a parsed message."""

from __future__ import annotations

import email
from email.policy import default as default_policy
from datetime import datetime, timezone

from parsers.export import to_eml_bytes
from parsers.models import Attachment, EmailMessage


def _message(**overrides) -> EmailMessage:
    base = dict(
        subject="Quarterly report",
        sender="Alice <a@example.com>",
        to=["b@example.com", "c@example.com"],
        cc=["d@example.com"],
        date=datetime(2024, 3, 5, 9, 30, tzinfo=timezone.utc),
        headers={"Message-ID": "<abc123@example.com>"},
        body_text="the plain body",
        body_html="<p>the <b>rich</b> body</p>",
        attachments=[Attachment(filename="notes.txt", mime_type="text/plain", data=b"hello")],
    )
    base.update(overrides)
    return EmailMessage(**base)


def test_rebuilds_headers_body_and_attachment():
    raw = to_eml_bytes(_message())
    parsed = email.message_from_bytes(raw, policy=default_policy)

    assert parsed["Subject"] == "Quarterly report"
    assert "a@example.com" in parsed["From"]
    assert "b@example.com" in parsed["To"] and "c@example.com" in parsed["To"]
    assert parsed["Cc"] == "d@example.com"
    assert parsed["Message-ID"] == "<abc123@example.com>"
    assert "2024" in parsed["Date"]

    payloads = {part.get_content_type() for part in parsed.walk()}
    assert "text/plain" in payloads
    assert "text/html" in payloads

    attachments = [p for p in parsed.walk() if p.get_filename()]
    assert [p.get_filename() for p in attachments] == ["notes.txt"]
    assert attachments[0].get_payload(decode=True) == b"hello"


def test_text_only_message_has_no_html_part():
    raw = to_eml_bytes(_message(body_html=None, attachments=[]))
    parsed = email.message_from_bytes(raw, policy=default_policy)
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_content().strip() == "the plain body"


def test_eml_source_is_returned_verbatim(tmp_path):
    original = b"From: x@example.com\r\nSubject: verbatim\r\n\r\nbody\r\n"
    src = tmp_path / "orig.eml"
    src.write_bytes(original)
    msg = _message(source_path=str(src))
    assert to_eml_bytes(msg) == original

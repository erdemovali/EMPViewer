"""parsers for .ics / .vcf / .mbox -> rendered cards / a one-folder document."""

from __future__ import annotations

import pytest

from parsers.calendar_parser import parse_ics_bytes
from parsers.errors import CorruptFileError
from parsers.loader import load
from parsers.mbox_parser import open_mbox
from parsers.vcard_parser import parse_vcf_bytes

ICS = (
    "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
    "SUMMARY:Team sync\r\nDTSTART:20240305T103000Z\r\nDTEND:20240305T113000Z\r\n"
    "LOCATION:Room 4\\, Building A\r\nORGANIZER;CN=Alice Example:mailto:alice@x.com\r\n"
    "ATTENDEE;CN=Bob:mailto:bob@x.com\r\nATTENDEE:mailto:carol@x.com\r\n"
    "DESCRIPTION:first line\\nsecond line\r\nRRULE:FREQ=WEEKLY\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
).encode()

VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Jane Doe\r\nEMAIL;TYPE=WORK:jane@x.com\r\n"
    "EMAIL:jane.home@x.com\r\nTEL;TYPE=CELL:+90 555 111 22 33\r\n"
    "ORG:Acme;R and D\r\nTITLE:Engineer\r\nURL:https://example.com\r\nEND:VCARD\r\n"
).encode()


def test_ics_event_fields_and_card() -> None:
    m = parse_ics_bytes(ICS, source_path="e.ics")
    assert m.subject == "Team sync"
    assert m.sender == "Alice Example"
    assert m.to == ["Bob", "carol@x.com"]
    assert m.date is not None and m.date.hour == 10
    html = m.body_html or ""
    assert "Room 4, Building A" in html          # \, unescaped
    assert "first line\nsecond line" in html      # \n unescaped
    assert "FREQ=WEEKLY" in html
    assert m.raw_source == ICS


def test_ics_rejects_non_calendar() -> None:
    with pytest.raises(CorruptFileError):
        parse_ics_bytes(b"just some text")


def test_vcf_contact_card() -> None:
    c = parse_vcf_bytes(VCF, source_path="j.vcf")
    assert c.subject == "Jane Doe"
    assert "jane@x.com" in c.sender and "jane.home@x.com" in c.sender
    html = c.body_html or ""
    assert "Engineer" in html and "Acme, R and D" in html
    assert "+90 555 111 22 33" in html
    assert "https://example.com" in html


def test_mbox_opens_as_one_folder(tmp_path) -> None:
    mb = tmp_path / "inbox.mbox"
    mb.write_bytes(
        b"From x Thu Jan  1 00:00:00 2024\r\nSubject: One\r\nFrom: a@x\r\n\r\nbody one\r\n\r\n"
        b"From x Thu Jan  1 00:00:00 2024\r\nSubject: Two\r\nFrom: b@y\r\n\r\nbody two\r\n\r\n"
    )
    doc = open_mbox(mb)
    try:
        assert [c.message_count for c in doc.root.children] == [2]
        stubs = doc.backend.list_messages(0)
        assert [s.subject for s in stubs] == ["One", "Two"]
        full = doc.backend.get_message((0, 1))
        assert (full.body_text or "").strip() == "body two"
    finally:
        doc.backend.close()


def test_loader_dispatches_new_extensions(tmp_path) -> None:
    ics = tmp_path / "x.ics"
    ics.write_bytes(ICS)
    vcf = tmp_path / "x.vcf"
    vcf.write_bytes(VCF)
    assert load(ics).subject == "Team sync"
    assert load(vcf).subject == "Jane Doe"

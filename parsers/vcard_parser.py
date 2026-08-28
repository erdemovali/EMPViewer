"""``.vcf`` (vCard) parsing -> a rendered contact card.

Like :mod:`parsers.calendar_parser`, the result is an
:class:`~parsers.models.EmailMessage` so the viewer needs no special-casing.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from . import _ical
from .errors import CorruptFileError
from .models import EmailMessage


def _esc(text: str) -> str:
    return _html.escape(text or "")


def _cards(text: str) -> list[dict]:
    cards: list[dict] = []
    cur: dict | None = None
    for line in _ical.unfold(text):
        name, params, value = _ical.split_line(line)
        if name == "BEGIN" and value.upper() == "VCARD":
            cur = {"emails": [], "phones": [], "urls": [], "addresses": []}
        elif name == "END" and value.upper() == "VCARD":
            if cur is not None:
                cards.append(cur)
            cur = None
        elif cur is None:
            continue
        elif name == "FN":
            cur["fn"] = _ical.unescape_text(value)
        elif name == "N" and "fn" not in cur:
            fields = [_ical.unescape_text(p) for p in value.split(";")]
            cur["fn"] = " ".join(f for f in (fields[1:2] + fields[:1]) if f).strip()
        elif name == "EMAIL":
            cur["emails"].append(value.strip())
        elif name == "TEL":
            typ = params.get("TYPE", "")
            cur["phones"].append(f"{value.strip()}" + (f" ({typ})" if typ else ""))
        elif name == "ORG":
            cur["org"] = _ical.unescape_text(value.replace(";", ", ").strip(", "))
        elif name == "TITLE":
            cur["title"] = _ical.unescape_text(value)
        elif name == "URL":
            cur["urls"].append(value.strip())
        elif name == "ADR":
            adr = ", ".join(p for p in (_ical.unescape_text(x) for x in value.split(";")) if p)
            if adr:
                cur["addresses"].append(adr)
    return cards


def _card_html(cards: list[dict]) -> str:
    blocks: list[str] = []
    for c in cards:
        rows = [f"<h2 style='margin:0 0 8px'>\U0001F464 {_esc(c.get('fn') or '(no name)')}</h2>"]
        if c.get("title") or c.get("org"):
            rows.append(f"<p>{_esc(' · '.join(x for x in (c.get('title'), c.get('org')) if x))}</p>")
        for label, key in (("Email", "emails"), ("Phone", "phones"),
                           ("Address", "addresses"), ("Web", "urls")):
            if c.get(key):
                rows.append(f"<p><b>{label}:</b> {_esc('; '.join(c[key]))}</p>")
        blocks.append(
            "<div style='border:1px solid #d0d7de;border-radius:10px;padding:14px;margin:0 0 12px'>"
            + "".join(rows) + "</div>"
        )
    return "<div style='font-family:sans-serif;padding:16px;max-width:680px'>" + "".join(blocks) + "</div>"


def parse_vcf_bytes(raw: bytes, *, source_path: str | None = None) -> EmailMessage:
    text = raw.decode("utf-8", "replace")
    if "BEGIN:VCARD" not in text.upper():
        raise CorruptFileError(source_path or "<bytes>", "Not a vCard file.")
    cards = _cards(text)
    if not cards:
        raise CorruptFileError(source_path or "<bytes>", "No contacts found.")
    first = cards[0]
    return EmailMessage(
        subject=first.get("fn") or "Contact",
        sender="; ".join(first.get("emails", [])),
        body_html=_card_html(cards),
        source_path=source_path,
        raw_source=raw,
    )


def parse_vcf(path: str | Path) -> EmailMessage:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise CorruptFileError(str(p), f"Could not read the file: {exc}") from exc
    return parse_vcf_bytes(raw, source_path=str(p))

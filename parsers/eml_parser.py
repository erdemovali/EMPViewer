"""``.eml`` parsing built on the standard-library :mod:`email` package.

No third-party dependency: this parser always works, which is why the test suite
exercises it fully.
"""

from __future__ import annotations

import email
from email.header import decode_header, make_header
from email.message import EmailMessage as PyEmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from .errors import CorruptFileError
from .models import Attachment, EmailMessage

_INTERESTING_HEADERS = (
    "Message-ID", "Date", "From", "To", "Cc", "Bcc", "Reply-To", "Subject",
    "Return-Path", "Delivered-To", "In-Reply-To", "References",
    "Content-Type", "User-Agent", "X-Mailer",
)


def _decode(value: object) -> str:
    """Best-effort decode of a raw header value into a display string."""

    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addr_list(msg: PyEmailMessage, field: str) -> list[str]:
    raw = msg.get_all(field, [])
    if not raw:
        return []
    out: list[str] = []
    for name, addr in getaddresses([str(r) for r in raw]):
        name = _decode(name)
        if name and addr:
            out.append(f"{name} <{addr}>")
        elif addr:
            out.append(addr)
        elif name:
            out.append(name)
    return out


def _parse_date(msg: PyEmailMessage):
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None


def _part_text(part: PyEmailMessage) -> str:
    """Decode a text part, tolerating a wrong or missing charset."""

    payload = part.get_payload(decode=True)
    if payload is None:
        return part.get_content() if isinstance(part.get_payload(), str) else ""
    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return payload.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _collect_bodies(msg: PyEmailMessage) -> tuple[str | None, str | None]:
    """Return ``(html, text)`` bodies, preferring the richest available."""

    html: str | None = None
    text: str | None = None

    if not msg.is_multipart():
        ctype = msg.get_content_type()
        body = _part_text(msg)
        if ctype == "text/html":
            return body, None
        return None, body

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        if disp == "attachment":
            continue
        if ctype == "text/html" and html is None:
            html = _part_text(part)
        elif ctype == "text/plain" and text is None:
            text = _part_text(part)

    return html, text


def _collect_attachments(msg: PyEmailMessage) -> list[Attachment]:
    if not msg.is_multipart():
        return []

    attachments: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        cid = part.get("Content-ID")
        ctype = part.get_content_type()
        is_body_text = ctype in ("text/plain", "text/html") and disp != "attachment" and not cid
        if is_body_text:
            continue
        if disp not in ("attachment", "inline") and not cid:
            continue

        data = part.get_payload(decode=True)
        if data is None:
            continue

        filename = _decode(part.get_filename()) or (
            f"inline-{len(attachments) + 1}.{(ctype.split('/')[-1] or 'bin')}"
        )
        attachments.append(
            Attachment(
                filename=filename,
                mime_type=ctype,
                data=data,
                is_inline=bool(cid) or disp == "inline",
                content_id=cid.strip("<>").strip() if cid else None,
            )
        )
    return attachments


def parse_eml_bytes(raw: bytes, *, source_path: str | None = None) -> EmailMessage:
    """Parse raw ``.eml`` bytes. Used by both :func:`parse_eml` and the tests."""

    try:
        msg: PyEmailMessage = email.message_from_bytes(raw, policy=default_policy)  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001 - want any failure surfaced cleanly
        raise CorruptFileError(source_path or "<bytes>", f"Not a valid RFC 822 message: {exc}") from exc

    try:
        html, text = _collect_bodies(msg)
        headers = {
            key: _decode(msg.get(key))
            for key in _INTERESTING_HEADERS
            if msg.get(key) is not None
        }
        return EmailMessage(
            subject=_decode(msg.get("Subject")),
            sender=(_addr_list(msg, "From") or [""])[0],
            to=_addr_list(msg, "To"),
            cc=_addr_list(msg, "Cc"),
            date=_parse_date(msg),
            headers=headers,
            body_html=html,
            body_text=text,
            attachments=_collect_attachments(msg),
            source_path=source_path,
        )
    except CorruptFileError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CorruptFileError(source_path or "<bytes>", f"Unexpected structure: {exc}") from exc


def parse_eml(path: str | Path) -> EmailMessage:
    """Parse an ``.eml`` file from disk into an :class:`EmailMessage`."""

    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise CorruptFileError(str(p), f"Could not read the file: {exc}") from exc
    return parse_eml_bytes(raw, source_path=str(p))

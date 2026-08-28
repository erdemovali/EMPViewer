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

from .errors import CorruptFileError, ParserError
from .models import Attachment, EmailMessage

_SIGNED_TYPES = {
    "multipart/signed",
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
}
_ENCRYPTED_TYPES = {
    "multipart/encrypted",
    "application/pgp-encrypted",
}


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


def _embedded_message(part: PyEmailMessage) -> Attachment:
    """Turn a ``message/rfc822`` part into a message-kind :class:`Attachment`
    carrying a parsed :attr:`Attachment.embedded` child when possible."""

    payload = part.get_payload()
    inner = payload[0] if isinstance(payload, list) and payload else None
    raw = b""
    parsed: EmailMessage | None = None
    if inner is not None:
        try:
            raw = inner.as_bytes()
        except Exception:  # noqa: BLE001
            raw = b""
        if raw:
            try:
                parsed = parse_eml_bytes(raw)
            except ParserError:
                parsed = None
    name = _decode(part.get_filename()) or (
        f"{(parsed.subject if parsed and parsed.subject else 'attached message')}.eml"
    )
    return Attachment(
        filename=name,
        mime_type="message/rfc822",
        data=raw,
        attach_kind="message",
        embedded=parsed,
    )


def _partition(msg: PyEmailMessage) -> tuple[str | None, str | None, list[Attachment]]:
    """One traversal of the MIME tree -> ``(html, text, attachments)``.

    ``message/rfc822`` parts are not descended into: each becomes a message-kind
    attachment carrying the parsed child, so the inner message's parts don't
    leak into this one.
    """

    html: str | None = None
    text: str | None = None
    atts: list[Attachment] = []

    def visit(part: PyEmailMessage, *, is_root: bool) -> None:
        nonlocal html, text
        ctype = part.get_content_type()

        if ctype == "message/rfc822":
            atts.append(_embedded_message(part))
            return
        if part.is_multipart():
            for sub in part.get_payload():
                visit(sub, is_root=False)
            return

        disp = (part.get_content_disposition() or "").lower()
        cid = part.get("Content-ID")
        is_body = ctype in ("text/plain", "text/html") and disp != "attachment" and not cid
        if is_body:
            if ctype == "text/html":
                if html is None:
                    html = _part_text(part)
            elif text is None:
                text = _part_text(part)
            return
        if is_root and not part.is_multipart():
            # Bare single-part message (text or otherwise): payload is the body.
            body = _part_text(part)
            if ctype == "text/html":
                html = html if html is not None else body
            else:
                text = text if text is not None else body
            return
        if disp not in ("attachment", "inline") and not cid:
            return

        data = part.get_payload(decode=True)
        if data is None:
            return
        filename = _decode(part.get_filename()) or (
            f"inline-{len(atts) + 1}.{(ctype.split('/')[-1] or 'bin')}"
        )
        atts.append(
            Attachment(
                filename=filename,
                mime_type=ctype,
                data=data,
                is_inline=bool(cid) or disp == "inline",
                content_id=cid.strip("<>").strip() if cid else None,
            )
        )

    visit(msg, is_root=True)
    return html, text, atts


def _refs(value: object) -> list[str]:
    """Split a ``References`` / ``In-Reply-To`` header into individual IDs."""

    if not value:
        return []
    return [tok for tok in str(value).replace(",", " ").split() if tok.startswith("<")]


def _importance(msg: PyEmailMessage) -> str | None:
    imp = (msg.get("Importance") or "").strip().lower()
    if imp in ("low", "normal", "high"):
        return imp
    prio = (msg.get("X-Priority") or "").strip()[:1]
    if prio in ("1", "2"):
        return "high"
    if prio in ("4", "5"):
        return "low"
    return None


def _crypto_flags(msg: PyEmailMessage) -> tuple[bool, bool]:
    """Detect (not verify) an S/MIME or PGP envelope."""

    signed = encrypted = False
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in _SIGNED_TYPES:
            signed = True
        elif ctype in _ENCRYPTED_TYPES:
            encrypted = True
        elif ctype == "application/pkcs7-mime":
            smime = (part.get_param("smime-type") or "").lower()
            if smime == "signed-data":
                signed = True
            else:  # enveloped-data, or unspecified -> treat as encrypted
                encrypted = True
    return signed, encrypted


def parse_eml_bytes(raw: bytes, *, source_path: str | None = None) -> EmailMessage:
    """Parse raw ``.eml`` bytes. Used by both :func:`parse_eml` and the tests."""

    try:
        msg: PyEmailMessage = email.message_from_bytes(raw, policy=default_policy)  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001 - want any failure surfaced cleanly
        raise CorruptFileError(source_path or "<bytes>", f"Not a valid RFC 822 message: {exc}") from exc

    try:
        html, text, attachments = _partition(msg)
        # Keep *every* header (last-wins for repeats; the verbatim truth for
        # duplicated Received lines etc. lives in ``raw_source``).
        headers = {key: _decode(val) for key, val in msg.items()}
        signed, encrypted = _crypto_flags(msg)
        return EmailMessage(
            subject=_decode(msg.get("Subject")),
            sender=(_addr_list(msg, "From") or [""])[0],
            to=_addr_list(msg, "To"),
            cc=_addr_list(msg, "Cc"),
            bcc=_addr_list(msg, "Bcc"),
            date=_parse_date(msg),
            headers=headers,
            body_html=html,
            body_text=text,
            attachments=attachments,
            source_path=source_path,
            message_id=(str(msg.get("Message-ID")).strip() or None) if msg.get("Message-ID") else None,
            in_reply_to=(_refs(msg.get("In-Reply-To")) or [None])[0],
            references=_refs(msg.get("References")),
            importance=_importance(msg),
            size=len(raw),
            is_signed=signed,
            is_encrypted=encrypted,
            raw_source=raw,
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

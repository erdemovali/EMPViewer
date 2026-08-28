"""``.msg`` (Outlook / MAPI) parsing via the :mod:`extract_msg` library.

Everything is normalised into the same :class:`~parsers.models.EmailMessage`
shape the ``.eml`` parser produces, so the viewer needs no special-casing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ._hdr import enrich_from_headers
from .errors import CorruptFileError, MissingDependencyError
from .models import Attachment, EmailMessage


def _require_extract_msg():
    try:
        import extract_msg  # noqa: F401

        return extract_msg
    except ImportError as exc:
        raise MissingDependencyError(
            "extract_msg",
            purpose="open Outlook .msg files",
            pip_name="extract-msg",
        ) from exc


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    # extract_msg gives a single ';'-separated string for To/Cc.
    return [chunk.strip() for chunk in str(value).replace(",", ";").split(";") if chunk.strip()]


def _coerce_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(str(value))
    except Exception:
        return None


def _hget(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""

    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return v
    return None


def _refs(value: str | None) -> list[str]:
    if not value:
        return []
    return [tok for tok in str(value).replace(",", " ").split() if tok.startswith("<")]


def _importance_msg(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.lower() in ("low", "normal", "high"):
        return name.lower()
    try:
        return {0: "low", 1: "normal", 2: "high"}.get(int(value))
    except (TypeError, ValueError):
        return None


def _decode_html(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return value.decode(enc)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def _rtf_to_html(msg: Any) -> str | None:
    """Last-resort body: decompress the RTF stream and strip it to text.

    Both helper packages are optional; if either is missing we simply return
    ``None`` and the caller falls back to whatever plain text exists.
    """

    raw = getattr(msg, "rtfBody", None)
    if not raw:
        return None
    try:
        from compressed_rtf import decompress

        rtf = decompress(raw)
    except Exception:
        rtf = raw
    try:
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(rtf.decode("latin-1") if isinstance(rtf, bytes) else rtf)
    except Exception:
        return None
    if not text.strip():
        return None
    import html as _html

    return f"<pre style='white-space:pre-wrap;font-family:inherit'>{_html.escape(text)}</pre>"


_MAX_NEST = 5


def _collect_attachments(msg: Any, depth: int = 0) -> list[Attachment]:
    out: list[Attachment] = []
    for att in getattr(msg, "attachments", []) or []:
        try:
            data = getattr(att, "data", None)
            embedded: EmailMessage | None = None
            # Embedded .msg attachments expose a nested Message as ``.data``.
            if data is not None and not isinstance(data, (bytes, bytearray)):
                nested = data
                if depth < _MAX_NEST:
                    try:
                        embedded = _build_message(nested, None, depth + 1)
                    except Exception:  # noqa: BLE001
                        embedded = None
                try:
                    data = nested.export()  # extract_msg >= 0.30
                except Exception:
                    data = b"" if embedded is not None else None
            if data is None and embedded is None:
                continue
            name = (
                getattr(att, "longFilename", None)
                or getattr(att, "shortFilename", None)
                or getattr(att, "name", None)
                or (f"{embedded.subject}.msg" if embedded and embedded.subject else None)
                or f"attachment-{len(out) + 1}"
            )
            cid = getattr(att, "cid", None) or getattr(att, "contentId", None)
            mime = getattr(att, "mimetype", None) or (
                "message/rfc822" if embedded is not None else "application/octet-stream"
            )
            out.append(
                Attachment(
                    filename=str(name),
                    mime_type=str(mime),
                    data=bytes(data or b""),
                    is_inline=bool(cid),
                    content_id=str(cid).strip("<>").strip() if cid else None,
                    attach_kind="message" if embedded is not None else "",
                    embedded=embedded,
                )
            )
        except Exception:
            # One bad attachment must not sink the whole message.
            continue
    return out


def _build_message(msg: Any, source_path: str | None, depth: int = 0) -> EmailMessage:
    """Normalise one ``extract_msg`` Message (top-level or embedded)."""

    html = _decode_html(getattr(msg, "htmlBody", None))
    text = getattr(msg, "body", None) or None
    if not html and not text:
        html = _rtf_to_html(msg)

    header_obj = getattr(msg, "header", None)
    headers: dict[str, str] = {}
    if header_obj is not None:
        try:
            headers = {k: str(v) for k, v in header_obj.items()}
        except Exception:
            headers = {}

    atts = _collect_attachments(msg, depth)
    msg_class = str(getattr(msg, "messageClass", "") or "").lower()
    att_names = " ".join(a.filename.lower() for a in atts)
    is_signed = "smime" in msg_class or "signed" in msg_class or ".p7s" in att_names
    is_encrypted = ".p7m" in att_names or "encrypted" in msg_class

    out = EmailMessage(
        subject=str(getattr(msg, "subject", "") or ""),
        sender=str(getattr(msg, "sender", "") or ""),
        to=_as_list(getattr(msg, "to", None)),
        cc=_as_list(getattr(msg, "cc", None)),
        bcc=_as_list(getattr(msg, "bcc", None)),
        date=_coerce_date(getattr(msg, "date", None)),
        headers=headers,
        body_html=html,
        body_text=text,
        attachments=atts,
        source_path=source_path,
        message_id=(str(getattr(msg, "messageId", "") or "").strip()
                    or _hget(headers, "Message-ID") or None),
        in_reply_to=(_refs(_hget(headers, "In-Reply-To")) or [None])[0],
        references=_refs(_hget(headers, "References")),
        importance=_importance_msg(getattr(msg, "importance", None)),
        is_signed=is_signed,
        is_encrypted=is_encrypted,
    )
    enrich_from_headers(out)
    return out


def parse_msg(path: str | Path) -> EmailMessage:
    """Parse a ``.msg`` file into an :class:`EmailMessage`."""

    extract_msg = _require_extract_msg()
    p = Path(path)
    if not p.is_file():
        raise CorruptFileError(str(p), "File does not exist.")

    msg = None
    try:
        msg = extract_msg.Message(str(p))
    except Exception as exc:  # noqa: BLE001
        raise CorruptFileError(str(p), f"extract_msg could not read this file: {exc}") from exc

    try:
        return _build_message(msg, str(p))
    except Exception as exc:  # noqa: BLE001
        raise CorruptFileError(str(p), f"Unexpected .msg structure: {exc}") from exc
    finally:
        try:
            if msg is not None:
                msg.close()
        except Exception:
            pass

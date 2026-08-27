"""``.msg`` (Outlook / MAPI) parsing via the :mod:`extract_msg` library.

Everything is normalised into the same :class:`~parsers.models.EmailMessage`
shape the ``.eml`` parser produces, so the viewer needs no special-casing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

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


def _collect_attachments(msg: Any) -> list[Attachment]:
    out: list[Attachment] = []
    for att in getattr(msg, "attachments", []) or []:
        try:
            data = getattr(att, "data", None)
            # Embedded .msg attachments expose a nested Message as ``.data``.
            if data is not None and not isinstance(data, (bytes, bytearray)):
                nested = data
                try:
                    data = nested.export()  # extract_msg >= 0.30
                except Exception:
                    data = None
            if not data:
                continue
            name = (
                getattr(att, "longFilename", None)
                or getattr(att, "shortFilename", None)
                or getattr(att, "name", None)
                or f"attachment-{len(out) + 1}"
            )
            cid = getattr(att, "cid", None) or getattr(att, "contentId", None)
            mime = getattr(att, "mimetype", None) or "application/octet-stream"
            out.append(
                Attachment(
                    filename=str(name),
                    mime_type=str(mime),
                    data=bytes(data),
                    is_inline=bool(cid),
                    content_id=str(cid).strip("<>").strip() if cid else None,
                )
            )
        except Exception:
            # One bad attachment must not sink the whole message.
            continue
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

        return EmailMessage(
            subject=str(getattr(msg, "subject", "") or ""),
            sender=str(getattr(msg, "sender", "") or ""),
            to=_as_list(getattr(msg, "to", None)),
            cc=_as_list(getattr(msg, "cc", None)),
            date=_coerce_date(getattr(msg, "date", None)),
            headers=headers,
            body_html=html,
            body_text=text,
            attachments=_collect_attachments(msg),
            source_path=str(p),
        )
    except Exception as exc:  # noqa: BLE001
        raise CorruptFileError(str(p), f"Unexpected .msg structure: {exc}") from exc
    finally:
        try:
            if msg is not None:
                msg.close()
        except Exception:
            pass

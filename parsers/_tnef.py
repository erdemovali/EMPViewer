"""Expand a TNEF blob (``winmail.dat`` / ``application/ms-tnef``).

Optional: needs the ``tnefparse`` package. When it is missing every function
here quietly returns "nothing", so the caller just keeps the opaque blob.
"""

from __future__ import annotations

import logging

from .models import Attachment

log = logging.getLogger("empviewer.tnef")

_TNEF_NAMES = {"winmail.dat", "win.dat"}
_TNEF_MIMES = {"application/ms-tnef", "application/vnd.ms-tnef", "application/x-ms-tnef"}


def is_tnef(filename: str, mime_type: str) -> bool:
    return (filename or "").lower() in _TNEF_NAMES or (mime_type or "").lower() in _TNEF_MIMES


def expand(data: bytes) -> tuple[list[Attachment], str | None, str | None]:
    """``(attachments, html_body, text_body)`` extracted from *data*.

    Everything is empty / ``None`` when ``tnefparse`` is unavailable or the blob
    doesn't parse.
    """

    try:
        from tnefparse import TNEF
    except ImportError:
        return [], None, None

    try:
        tnef = TNEF(data)
    except Exception:  # noqa: BLE001 - malformed blob -> give up gracefully
        log.debug("tnefparse could not read the blob", exc_info=True)
        return [], None, None

    atts: list[Attachment] = []
    for i, a in enumerate(getattr(tnef, "attachments", []) or []):
        payload = getattr(a, "data", b"") or b""
        if not payload:
            continue
        try:
            name = a.long_filename() or a.name
        except Exception:  # noqa: BLE001
            name = getattr(a, "name", "") or ""
        atts.append(Attachment(
            filename=str(name) or f"tnef-{i + 1}.bin",
            mime_type="application/octet-stream",
            data=bytes(payload),
        ))

    html = getattr(tnef, "htmlbody", None) or None
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    text = getattr(tnef, "body", None) or None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")

    return atts, html, text


def merge(attachments: list[Attachment], html: str | None, text: str | None):
    """Replace any TNEF attachment in *attachments* with its expanded contents.

    Returns ``(attachments, html, text)`` - the bodies are only filled from the
    TNEF blob when the message didn't already carry one.
    """

    if not any(is_tnef(a.filename, a.mime_type) for a in attachments):
        return attachments, html, text

    out: list[Attachment] = []
    for a in attachments:
        if not is_tnef(a.filename, a.mime_type):
            out.append(a)
            continue
        inner, t_html, t_text = expand(a.data)
        if not inner and not t_html and not t_text:
            out.append(a)  # tnefparse missing / blob unreadable -> keep as-is
            continue
        out.extend(inner)
        html = html or t_html
        text = text or t_text
    return out, html, text

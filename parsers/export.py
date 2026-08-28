"""Turn an :class:`~parsers.models.EmailMessage` back into an ``.eml`` byte
stream, so PST/OST and ``.msg`` messages can be saved as standard mail files.

For a message that came from an ``.eml`` file the original bytes are returned
unchanged; everything else is rebuilt from the parsed model.
"""

from __future__ import annotations

from email.message import EmailMessage as _RFC822
from email.utils import format_datetime
from pathlib import Path

from parsers.models import EmailMessage

_CARRIED_HEADERS = ("Message-ID", "Reply-To", "In-Reply-To", "References")


def to_eml_bytes(message: EmailMessage) -> bytes:
    src = message.source_path
    if src and src.lower().endswith(".eml"):
        try:
            return Path(src).read_bytes()
        except OSError:
            pass

    out = _RFC822()
    if message.sender:
        out["From"] = message.sender
    if message.to:
        out["To"] = ", ".join(message.to)
    if message.cc:
        out["Cc"] = ", ".join(message.cc)
    out["Subject"] = message.subject or ""
    if message.date is not None:
        try:
            out["Date"] = format_datetime(message.date)
        except (TypeError, ValueError):
            pass
    for name in _CARRIED_HEADERS:
        value = message.headers.get(name) or message.headers.get(name.lower())
        if value and name not in out:
            out[name] = value

    text = message.body_text or ""
    html = message.body_html
    if html and text:
        out.set_content(text)
        out.add_alternative(html, subtype="html")
    elif html:
        out.set_content("This message has an HTML body.\n")
        out.add_alternative(html, subtype="html")
    else:
        out.set_content(text)

    for att in message.attachments:
        maintype, _, subtype = (att.mime_type or "application/octet-stream").partition("/")
        try:
            out.add_attachment(
                att.data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=att.filename or "attachment",
            )
        except (TypeError, ValueError):
            continue

    return out.as_bytes()

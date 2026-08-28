"""Turn an :class:`~parsers.models.EmailMessage` back into an ``.eml`` byte
stream, so PST/OST and ``.msg`` messages can be saved as standard mail files.

For a message that came from an ``.eml`` file the original bytes are returned
unchanged; everything else is rebuilt from the parsed model.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage as _RFC822
from email.utils import format_datetime
from pathlib import Path
from typing import Callable

from parsers.models import EmailMessage, FolderNode

_CARRIED_HEADERS = ("Message-ID", "Reply-To", "In-Reply-To", "References")

log = logging.getLogger("empviewer.export")


class ExportCancelled(Exception):
    """Raised inside :func:`export_folder` when ``should_cancel`` returns True."""


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


# --------------------------------------------------------------------------- #
# Bulk export: a PST folder (optionally recursive) -> a tree of .eml files
# --------------------------------------------------------------------------- #
def _estimate(node: FolderNode, recursive: bool) -> int:
    total = node.message_count
    if recursive:
        for child in node.iter_descendants():
            total += child.message_count
    return total


def _safe_segment(name: str) -> str:
    """One safe path component - like helpers.safe_filename but slashes in the
    name become ``_`` instead of splitting off everything before them."""

    from utils.helpers import safe_filename

    flattened = (name or "").replace("/", "_").replace("\\", "_")
    return safe_filename(flattened, fallback="item")


def _unique(folder: Path, stem: str, suffix: str, used: set[str]) -> str:
    name = f"{stem}{suffix}"
    n = 1
    while name.lower() in used or (folder / name).exists():
        name = f"{stem} ({n}){suffix}"
        n += 1
    used.add(name.lower())
    return name


def export_folder(
    backend,
    node: FolderNode,
    dest: str | Path,
    *,
    recursive: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Write every message under *node* to ``dest`` as ``.eml`` files.

    Sub-folders become sub-directories. Returns the number of messages written.
    A message that fails to load is skipped (logged), not fatal. Raising
    :class:`ExportCancelled` unwinds the walk if ``should_cancel`` trips.
    """

    dest = Path(dest)
    total = _estimate(node, recursive)
    done = 0
    written = 0

    def _check() -> None:
        if should_cancel is not None and should_cancel():
            raise ExportCancelled()

    def _walk(n: FolderNode, folder_dir: Path) -> None:
        nonlocal done, written
        _check()
        try:
            stubs = backend.list_messages(n.backend_id)
        except Exception:  # noqa: BLE001 - one bad folder shouldn't abort the run
            log.warning("export: could not list folder %r", n.name, exc_info=True)
            stubs = []
        if stubs:
            folder_dir.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        for i, stub in enumerate(stubs, 1):
            _check()
            try:
                data = to_eml_bytes(backend.get_message(stub.backend_id))
            except Exception:  # noqa: BLE001
                log.warning("export: skipped message %r", stub.backend_id, exc_info=True)
                done += 1
                continue
            stem = f"{i:04d}-{_safe_segment(stub.display_subject or 'message')}"
            try:
                (folder_dir / _unique(folder_dir, stem, ".eml", used)).write_bytes(data)
                written += 1
            except OSError:
                log.warning("export: could not write %s", stem, exc_info=True)
            done += 1
            if on_progress is not None:
                on_progress(done, total)
        if recursive:
            child_used: set[str] = set()
            for child in n.children:
                sub = folder_dir / _unique(folder_dir, _safe_segment(child.name), "", child_used)
                _walk(child, sub)

    try:
        _walk(node, dest)
    except ExportCancelled:
        log.info("export cancelled after %d message(s)", written)
    return written

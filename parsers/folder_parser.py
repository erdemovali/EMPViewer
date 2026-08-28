"""Open a *directory* of mail / card files as one browsable document.

Every ``.eml`` / ``.msg`` / ``.ics`` / ``.vcf`` found under the folder (recursively)
becomes a row in a single message list, reusing the normal per-format parsers.
Container formats (``.pst`` / ``.ost`` / ``.mbox``) are skipped so the folder
view never recurses into another store.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .errors import CorruptFileError
from .models import EmailMessage, FolderNode, MessageStub, PstDocument

LEAF_EXTS = {".eml", ".msg", ".ics", ".vcf"}


class DirBackend:
    name = "folder"

    def __init__(self) -> None:
        self._root = Path(".")
        self._files: list[Path] = []
        self._cache: dict[int, EmailMessage] = {}
        self._lock = threading.RLock()

    def open(self, path: str) -> None:
        self._root = Path(path)
        if not self._root.is_dir():
            raise CorruptFileError(path, "This path is not a folder.")
        self._files = sorted(
            p for p in self._root.rglob("*")
            if p.is_file() and p.suffix.lower() in LEAF_EXTS
        )

    def close(self) -> None:
        with self._lock:
            self._cache.clear()

    def folder_tree(self) -> FolderNode:
        root = FolderNode(name=self._root.name or "folder", backend_id=-1)
        root.children.append(
            FolderNode(name="All items", backend_id=0, message_count=len(self._files))
        )
        return root

    def _load(self, index: int) -> EmailMessage:
        with self._lock:
            hit = self._cache.get(index)
        if hit is not None:
            return hit
        from .loader import load

        msg = load(self._files[index])  # ParserError propagates
        if not isinstance(msg, EmailMessage):  # pragma: no cover - LEAF_EXTS guards this
            raise CorruptFileError(str(self._files[index]), "Unexpected container file.")
        with self._lock:
            self._cache[index] = msg
        return msg

    def list_messages(self, folder_id, *, should_cancel=None) -> list[MessageStub]:
        out: list[MessageStub] = []
        for i, f in enumerate(self._files):
            if should_cancel is not None and (i & 0x3F) == 0 and should_cancel():
                return out
            try:
                m = self._load(i)
                out.append(MessageStub(
                    backend_id=(0, i), sender=m.sender, subject=m.subject or f.name,
                    date=m.date, has_attachments=bool(m.visible_attachments), size=m.size,
                ))
            except Exception:  # noqa: BLE001 - one bad file must not sink the list
                out.append(MessageStub(backend_id=(0, i), sender="", subject=f.name, date=None))
        return out

    def get_message(self, message_id) -> EmailMessage:
        _folder, index = message_id
        try:
            m = self._load(index)
        except IndexError as exc:
            raise CorruptFileError(str(self._root), f"Item #{index} is out of range.") from exc
        try:
            rel = str(self._files[index].parent.relative_to(self._root))
        except ValueError:
            rel = ""
        m.folder_path = rel if rel and rel != "." else self._root.name
        return m


def open_dir(path: str | Path) -> PstDocument:
    p = Path(path)
    backend = DirBackend()
    backend.open(str(p))
    return PstDocument(path=str(p), root=backend.folder_tree(), backend=backend)

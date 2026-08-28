"""``.mbox`` parsing via the standard-library :mod:`mailbox` module.

An mbox is presented like a one-folder PST: :func:`open_mbox` returns a
:class:`~parsers.models.PstDocument` whose backend lists / fetches messages,
reusing the ``.eml`` parser for each one.
"""

from __future__ import annotations

import mailbox
import threading
from pathlib import Path

from .eml_parser import parse_eml_bytes
from .errors import CorruptFileError
from .models import EmailMessage, FolderNode, MessageStub, PstDocument


class MboxBackend:
    name = "mbox"

    def __init__(self) -> None:
        self._box: mailbox.mbox | None = None
        self._keys: list = []
        self._path = ""
        self._lock = threading.RLock()

    def open(self, path: str) -> None:
        self._path = path
        try:
            self._box = mailbox.mbox(path, create=False)
            self._keys = list(self._box.keys())
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(path, f"Could not read the mbox: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            try:
                if self._box is not None:
                    self._box.close()
            except Exception:  # noqa: BLE001
                pass
            self._box = None

    def folder_tree(self) -> FolderNode:
        name = Path(self._path).name or "mbox"
        # A PST root shows no message list itself, so hang the messages off a
        # single child folder the UI can select.
        root = FolderNode(name=name, backend_id=-1)
        root.children.append(
            FolderNode(name="Messages", backend_id=0, message_count=len(self._keys))
        )
        return root

    def _message_bytes(self, index: int) -> bytes:
        assert self._box is not None
        return self._box.get_bytes(self._keys[index])

    def list_messages(self, folder_id, *, should_cancel=None) -> list[MessageStub]:
        with self._lock:
            stubs: list[MessageStub] = []
            for i in range(len(self._keys)):
                if should_cancel is not None and (i & 0xFF) == 0 and should_cancel():
                    return stubs
                try:
                    m = parse_eml_bytes(self._message_bytes(i))
                    stubs.append(MessageStub(
                        backend_id=(0, i), sender=m.sender, subject=m.subject, date=m.date,
                        has_attachments=bool(m.visible_attachments), size=m.size,
                    ))
                except Exception:  # noqa: BLE001
                    stubs.append(MessageStub(backend_id=(0, i), sender="",
                                             subject=f"(message {i + 1})", date=None))
            return stubs

    def get_message(self, message_id) -> EmailMessage:
        _folder, index = message_id
        with self._lock:
            try:
                msg = parse_eml_bytes(self._message_bytes(index))
            except IndexError as exc:
                raise CorruptFileError(self._path, f"Message #{index} is out of range.") from exc
            msg.source_path = None
            msg.folder_path = Path(self._path).name
            return msg


def open_mbox(path: str | Path) -> PstDocument:
    p = Path(path)
    backend = MboxBackend()
    backend.open(str(p))
    return PstDocument(path=str(p), root=backend.folder_tree(), backend=backend)

"""parsers.export.export_folder: recursive .eml dump with a fake PST backend."""

from __future__ import annotations

from datetime import datetime

import pytest

from parsers.export import ExportCancelled, export_folder
from parsers.models import EmailMessage, FolderNode, MessageStub


class _FakeBackend:
    """folder backend_id -> list of (subject, body) messages."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def list_messages(self, folder_id):
        return [
            MessageStub(backend_id=(folder_id, i), sender="s", subject=subj,
                        date=datetime(2024, 1, 1))
            for i, (subj, _body) in enumerate(self._data.get(folder_id, []))
        ]

    def get_message(self, message_id):
        folder_id, i = message_id
        subj, body = self._data[folder_id][i]
        if body is None:
            raise RuntimeError("boom")
        return EmailMessage(subject=subj, sender="s@x", body_text=body)


def _tree() -> FolderNode:
    root = FolderNode(name="Root", backend_id="root", message_count=1)
    inbox = FolderNode(name="Inbox", backend_id="inbox", message_count=2)
    sub = FolderNode(name="Sub/Folder", backend_id="sub", message_count=1)
    inbox.children.append(sub)
    root.children.append(inbox)
    return root


def test_recursive_export_mirrors_the_tree(tmp_path) -> None:
    backend = _FakeBackend({
        "root": [("hello root", "r0")],
        "inbox": [("a", "a0"), ("b", "b0")],
        "sub": [("deep", "d0")],
    })
    n = export_folder(backend, _tree(), tmp_path)
    assert n == 4
    assert (tmp_path / "0001-hello root.eml").exists()
    assert (tmp_path / "Inbox" / "0001-a.eml").exists()
    assert (tmp_path / "Inbox" / "0002-b.eml").exists()
    # "Sub/Folder" must be sanitised into a single path component.
    assert (tmp_path / "Inbox" / "Sub_Folder" / "0001-deep.eml").exists()
    body = (tmp_path / "Inbox" / "0001-a.eml").read_bytes()
    assert b"a0" in body


def test_non_recursive_skips_children(tmp_path) -> None:
    backend = _FakeBackend({"root": [("x", "x0")], "inbox": [("y", "y0")]})
    n = export_folder(backend, _tree(), tmp_path, recursive=False)
    assert n == 1
    assert not (tmp_path / "Inbox").exists()


def test_bad_message_is_skipped_not_fatal(tmp_path) -> None:
    backend = _FakeBackend({"root": [("ok", "0"), ("bad", None), ("ok2", "2")]})
    root = FolderNode(name="Root", backend_id="root")
    n = export_folder(backend, root, tmp_path, recursive=False)
    assert n == 2


def test_cancellation_returns_partial_count(tmp_path) -> None:
    backend = _FakeBackend({"root": [(f"m{i}", str(i)) for i in range(10)]})
    root = FolderNode(name="Root", backend_id="root")
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 4

    n = export_folder(backend, root, tmp_path, recursive=False, should_cancel=should_cancel)
    assert 0 < n < 10

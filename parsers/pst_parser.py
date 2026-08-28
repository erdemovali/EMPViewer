"""``.pst`` / ``.ost`` parsing behind a pluggable backend interface.

``.pst`` (Outlook data file) and ``.ost`` (Outlook offline cache) share the same
underlying PFF container format, so both extensions are handled here identically.

Three interchangeable backends are provided; :func:`open_pst` picks the first
that works:

* :class:`NativePstBackend` - a dependency-free reader (:mod:`parsers.pst_native`)
  for Unicode PST/OST. Always available; the default. Handles none / permute /
  cyclic encryption.
* :class:`LibpffBackend` - wraps the ``pypff`` bindings for ``libpff`` (C).
  Very tolerant of large / slightly-damaged stores. Used if importable.
* :class:`ReadpstBackend` - shells out to the ``readpst`` command from
  `libpst <https://www.five-ten-sg.com/libpst/>`_, extracting the store to a
  temporary tree of ``.eml`` files parsed with :mod:`parsers.eml_parser`. Used if
  ``readpst`` is on ``PATH``.

Backends never hand live library objects to the rest of the app: a ``backend_id``
is a small, picklable locator and every call re-navigates from scratch under a
lock, which keeps things safe when different :class:`~PySide6.QtCore.QThreadPool`
threads call in.
"""

from __future__ import annotations

import abc
import html as _html
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from ._hdr import enrich_from_headers
from .errors import CorruptFileError, MissingDependencyError, ParserError
from .models import Attachment, EmailMessage, FolderNode, MessageStub, PstDocument

# A backend_id for a folder is a tuple of sub-folder indices from the root.
FolderId = tuple[int, ...]
# A backend_id for a message is (folder_id, message_index).
MessageId = tuple[FolderId, int]


class PstBackend(abc.ABC):
    """Abstract read interface over an open PST/OST store."""

    name: str = "abstract"

    @abc.abstractmethod
    def open(self, path: str) -> None: ...

    @abc.abstractmethod
    def folder_tree(self) -> FolderNode: ...

    @abc.abstractmethod
    def list_messages(self, folder_id: FolderId) -> list[MessageStub]: ...

    @abc.abstractmethod
    def get_message(self, message_id: MessageId) -> EmailMessage: ...

    def close(self) -> None:  # pragma: no cover - trivial default
        ...


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _decode(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _split_recipients(value: str) -> list[str]:
    if not value:
        return []
    return [chunk.strip() for chunk in value.replace(",", ";").split(";") if chunk.strip()]


def _pst_failure_detail(path: str, exc: object) -> str:
    ext = Path(path).suffix.lower()
    base = f"The store could not be parsed ({exc})."
    if ext == ".ost":
        base += (
            "\n\nNote: .ost files created by modern Outlook are often encrypted "
            "with a profile-bound key and cannot be read by third-party tools."
        )
    return base


# --------------------------------------------------------------------------- #
# Native pure-Python backend (default)
# --------------------------------------------------------------------------- #
class NativePstBackend(PstBackend):
    """Read Unicode PST/OST with the built-in :mod:`parsers.pst_native` reader.

    ``backend_id`` for a folder is its 32-bit NID (int); for a message it is
    ``(folder_nid, message_nid)``.
    """

    name = "native"

    def __init__(self) -> None:
        self._pst = None
        self._path = ""
        self._lock = threading.RLock()
        self._folder_paths: dict[int, str] = {}

    def open(self, path: str) -> None:
        from . import pst_native

        self._path = path
        try:
            self._pst = pst_native.PstFile(path)
        except pst_native.PstFormatError as exc:
            raise CorruptFileError(path, _pst_failure_detail(path, exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(path, _pst_failure_detail(path, exc)) from exc

    def close(self) -> None:
        with self._lock:
            try:
                if self._pst is not None:
                    self._pst.close()
            except Exception:
                pass
            self._pst = None

    def folder_tree(self) -> FolderNode:
        with self._lock:
            root_nid = self._pst.root_folder_nid()
            self._folder_paths.clear()
            return self._walk(root_nid, "", 0)

    def _walk(self, nid: int, parent_path: str, depth: int) -> FolderNode:
        f = self._pst.folder(nid)
        name = f.name or ("Top of Personal Folders" if depth == 0 else f"Folder {nid:#x}")
        path = name if not parent_path else f"{parent_path}/{name}"
        self._folder_paths[nid] = path
        node = FolderNode(name=name, backend_id=nid, message_count=f.message_count)
        if depth < 64:
            for child in f.child_nids:
                try:
                    node.children.append(self._walk(child, path, depth + 1))
                except Exception:
                    continue
        return node

    def list_messages(self, folder_id) -> list[MessageStub]:
        from .pst_native import (
            MSGFLAG_HASATTACH,
            MSGFLAG_READ,
            PID_CLIENT_SUBMIT_TIME,
            PID_DELIVERY_TIME,
            PID_HAS_ATTACHMENTS,
            PID_MESSAGE_FLAGS,
            PID_MESSAGE_SIZE,
            PID_SENDER_NAME,
            PID_SENT_REPR_NAME,
            PID_SUBJECT,
            _clean_subject,
        )

        with self._lock:
            rows = self._pst.folder_contents(folder_id)
            stubs: list[MessageStub] = []
            for row in rows:
                nid = row.get("_rowid", 0)
                if not nid:
                    continue
                subj = row.get(PID_SUBJECT) or ""
                if isinstance(subj, bytes):
                    subj = subj.decode("utf-8", "replace")
                sender = row.get(PID_SENT_REPR_NAME) or row.get(PID_SENDER_NAME) or ""
                if isinstance(sender, bytes):
                    sender = sender.decode("utf-8", "replace")
                date = row.get(PID_DELIVERY_TIME) or row.get(PID_CLIENT_SUBMIT_TIME)
                mflags = row.get(PID_MESSAGE_FLAGS)
                mflags = mflags if isinstance(mflags, int) else 0
                msize = row.get(PID_MESSAGE_SIZE)
                stubs.append(
                    MessageStub(
                        backend_id=(folder_id, nid),
                        sender=str(sender),
                        subject=_clean_subject(str(subj)),
                        date=date if isinstance(date, datetime) else None,
                        has_attachments=bool(row.get(PID_HAS_ATTACHMENTS))
                        or bool(mflags & MSGFLAG_HASATTACH),
                        size=msize if isinstance(msize, int) and msize > 0 else None,
                        unread=not (mflags & MSGFLAG_READ),
                    )
                )
            return stubs

    def get_message(self, message_id) -> EmailMessage:
        folder_id, msg_nid = message_id
        with self._lock:
            try:
                m = self._pst.message(msg_nid)
            except Exception as exc:  # noqa: BLE001
                raise CorruptFileError(self._path, f"Message {msg_nid:#x} could not be read: {exc}") from exc

            atts = [
                Attachment(
                    filename=a["filename"],
                    mime_type=a["mime_type"],
                    data=a["data"],
                    is_inline=bool(a["content_id"]),
                    content_id=a["content_id"],
                )
                for a in m["attachments"]
            ]
            out = EmailMessage(
                subject=m["subject"],
                sender=m["sender"],
                to=m["to"],
                cc=m["cc"],
                date=m["date"] if isinstance(m["date"], datetime) else None,
                headers=m["headers"],
                body_html=m["body_html"],
                body_text=m["body_text"],
                attachments=atts,
                folder_path=self._folder_paths.get(folder_id),
            )
            enrich_from_headers(out)
            return out


# --------------------------------------------------------------------------- #
# libpff / pypff backend
# --------------------------------------------------------------------------- #
class LibpffBackend(PstBackend):
    name = "libpff"

    def __init__(self) -> None:
        self._file = None
        self._path = ""
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------- #
    def open(self, path: str) -> None:
        try:
            import pypff
        except ImportError as exc:
            raise MissingDependencyError(
                "pypff",
                purpose="open .pst / .ost files with the libpff backend",
                pip_name="libpff-python",
            ) from exc

        # PyPI has an unrelated 'pypff' (an astronomy tool). Make sure this is
        # really the libpff binding before trusting it.
        if not hasattr(pypff, "file"):
            raise MissingDependencyError(
                "pypff",
                purpose="open .pst / .ost files (the installed 'pypff' is not libpff)",
                pip_name="libpff-python",
            )

        self._path = path
        try:
            handle = pypff.file()
            handle.open(path)
        except Exception as exc:  # noqa: BLE001
            raise CorruptFileError(path, _pst_failure_detail(path, exc)) from exc
        self._file = handle

    def close(self) -> None:
        with self._lock:
            try:
                if self._file is not None:
                    self._file.close()
            except Exception:
                pass
            self._file = None

    # -- navigation ---------------------------------------------------- #
    def _root(self):
        if self._file is None:
            raise CorruptFileError(self._path, "The store is not open.")
        return self._file.get_root_folder()

    def _resolve_folder(self, folder_id: FolderId):
        node = self._root()
        for idx in folder_id:
            node = node.get_sub_folder(idx)
        return node

    # -- interface --------------------------------------------------- #
    def folder_tree(self) -> FolderNode:
        with self._lock:
            root = self._root()
            return self._walk(root, (), _pff_folder_name(root) or "Root")

    def _walk(self, folder, folder_id: FolderId, name: str) -> FolderNode:
        try:
            msg_count = int(folder.get_number_of_sub_messages())
        except Exception:
            msg_count = 0
        node = FolderNode(name=name, backend_id=folder_id, message_count=msg_count)
        try:
            sub_count = int(folder.get_number_of_sub_folders())
        except Exception:
            sub_count = 0
        for i in range(sub_count):
            try:
                child = folder.get_sub_folder(i)
            except Exception:
                continue
            node.children.append(
                self._walk(child, folder_id + (i,), _pff_folder_name(child) or f"Folder {i + 1}")
            )
        return node

    def list_messages(self, folder_id: FolderId) -> list[MessageStub]:
        with self._lock:
            folder = self._resolve_folder(folder_id)
            try:
                count = int(folder.get_number_of_sub_messages())
            except Exception:
                count = 0
            stubs: list[MessageStub] = []
            for i in range(count):
                try:
                    m = folder.get_sub_message(i)
                    n_att = _call(m, "get_number_of_attachments")
                    stubs.append(
                        MessageStub(
                            backend_id=(folder_id, i),
                            sender=_decode(_call(m, "get_sender_name")),
                            subject=_decode(_call(m, "get_subject")),
                            date=_pff_date(m),
                            has_attachments=bool(n_att) if isinstance(n_att, int) else False,
                        )
                    )
                except Exception:
                    stubs.append(
                        MessageStub(backend_id=(folder_id, i), sender="", subject="(unreadable message)", date=None)
                    )
            return stubs

    def get_message(self, message_id: MessageId) -> EmailMessage:
        folder_id, msg_index = message_id
        with self._lock:
            folder = self._resolve_folder(folder_id)
            try:
                m = folder.get_sub_message(msg_index)
            except Exception as exc:  # noqa: BLE001
                raise CorruptFileError(self._path, f"Message #{msg_index} could not be read: {exc}") from exc

            headers = _pff_headers(m)
            html_body = _pff_body(m, "get_html_body")
            text_body = _pff_body(m, "get_plain_text_body")
            if not html_body and not text_body:
                html_body = _pff_rtf_as_html(m)

            out = EmailMessage(
                subject=_decode(_call(m, "get_subject")),
                sender=headers.get("From") or _decode(_call(m, "get_sender_name")),
                to=_split_recipients(headers.get("To", "")),
                cc=_split_recipients(headers.get("Cc", "")),
                date=_pff_date(m),
                headers=headers,
                body_html=html_body,
                body_text=text_body,
                attachments=_pff_attachments(m),
            )
            enrich_from_headers(out)
            return out


def _call(obj, name):
    fn = getattr(obj, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def _pff_folder_name(folder) -> str:
    for attr in ("get_name", "name"):
        val = getattr(folder, attr, None)
        val = val() if callable(val) else val
        if val:
            return _decode(val)
    return ""


def _pff_date(message) -> datetime | None:
    for attr in ("get_client_submit_time", "get_delivery_time", "get_creation_time"):
        val = _call(message, attr)
        if isinstance(val, datetime):
            return val
    return None


def _pff_body(message, attr: str) -> str | None:
    raw = _call(message, attr)
    if not raw:
        return None
    if isinstance(raw, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _pff_rtf_as_html(message) -> str | None:
    raw = _call(message, "get_rtf_body")
    if not raw:
        return None
    try:
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(raw.decode("latin-1") if isinstance(raw, bytes) else raw)
    except Exception:
        return None
    if not text.strip():
        return None
    return f"<pre style='white-space:pre-wrap;font-family:inherit'>{_html.escape(text)}</pre>"


def _pff_headers(message) -> dict[str, str]:
    raw = _call(message, "get_transport_headers")
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        import email

        return {k: str(v) for k, v in email.message_from_string(raw).items()}
    except Exception:
        return {}


def _pff_attachments(message) -> list[Attachment]:
    out: list[Attachment] = []
    count = _call(message, "get_number_of_attachments") or 0
    for i in range(int(count)):
        try:
            att = message.get_attachment(i)
            try:
                size = int(att.get_size())
            except Exception:
                size = 0
            data = b""
            if size:
                try:
                    data = att.read_buffer(size)
                except Exception:
                    data = b""
            name = _decode(_call(att, "get_name")) or f"attachment-{i + 1}.bin"
            out.append(
                Attachment(filename=name, mime_type="application/octet-stream", data=bytes(data))
            )
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# readpst (libpst) backend
# --------------------------------------------------------------------------- #
class ReadpstBackend(PstBackend):
    """Extract the whole store once with ``readpst -e`` and read the ``.eml`` files.

    Trades an up-front extraction cost (done off the GUI thread) for zero
    native-Python dependencies and reuse of the well-tested EML parser.
    """

    name = "readpst"

    def __init__(self) -> None:
        self._path = ""
        self._work: Path | None = None
        self._lock = threading.RLock()
        #: folder_id -> (display path, list[Path of .eml files])
        self._index: dict[FolderId, tuple[str, list[Path]]] = {}
        self._root_node: FolderNode | None = None

    # -- lifecycle ------------------------------------------------------- #
    def open(self, path: str) -> None:
        exe = shutil.which("readpst")
        if not exe:
            raise MissingDependencyError(
                "readpst",
                purpose="open .pst / .ost files without a native Python PST module",
                pip_name="libpst  (choco install libpst | brew install libpst | apt install libpst)",
            )
        self._path = path
        self._work = Path(tempfile.mkdtemp(prefix="empviewer-pst-"))
        cmd = [exe, "-e", "-D", "-q", "-o", str(self._work), path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired as exc:
            raise CorruptFileError(path, "readpst timed out after 30 minutes.") from exc
        except OSError as exc:
            raise CorruptFileError(path, f"Could not run readpst: {exc}") from exc

        eml_files = list(self._work.rglob("*.eml"))
        if proc.returncode != 0 and not eml_files:
            raise CorruptFileError(path, _pst_failure_detail(path, (proc.stderr or "").strip() or "readpst failed"))

        self._build_index(eml_files)

    def close(self) -> None:
        with self._lock:
            if self._work and self._work.exists():
                shutil.rmtree(self._work, ignore_errors=True)
            self._work = None

    # -- indexing ----------------------------------------------------- #
    def _build_index(self, eml_files: list[Path]) -> None:
        assert self._work is not None
        # Group message files by their containing directory (== PST folder).
        by_dir: dict[Path, list[Path]] = {}
        for f in sorted(eml_files):
            by_dir.setdefault(f.parent, []).append(f)

        root = FolderNode(name=Path(self._path).stem, backend_id=(), message_count=0)
        self._index[()] = (root.name, by_dir.get(self._work, []))
        root.message_count = len(self._index[()][1])

        # Build folder nodes for every directory that contains messages or
        # leads to one, keyed by a stable index path.
        dir_nodes: dict[Path, FolderNode] = {self._work: root}
        counters: dict[Path, int] = {}

        for directory in sorted(d for d in by_dir if d != self._work):
            rel_parts = directory.relative_to(self._work).parts
            cursor = self._work
            parent_node = root
            fid: FolderId = ()
            for part in rel_parts:
                cursor = cursor / part
                if cursor in dir_nodes:
                    parent_node = dir_nodes[cursor]
                    fid = parent_node.backend_id
                    continue
                idx = counters.get(parent_node.backend_id, 0)
                counters[parent_node.backend_id] = idx + 1
                fid = parent_node.backend_id + (idx,)
                files = by_dir.get(cursor, [])
                node = FolderNode(name=part, backend_id=fid, message_count=len(files))
                parent_node.children.append(node)
                dir_nodes[cursor] = node
                self._index[fid] = (
                    "/".join((root.name, *cursor.relative_to(self._work).parts)),
                    files,
                )
                parent_node = node

        self._root_node = root

    # -- interface -------------------------------------------------- #
    def folder_tree(self) -> FolderNode:
        if self._root_node is None:
            raise CorruptFileError(self._path, "The store is not open.")
        return self._root_node

    def list_messages(self, folder_id: FolderId) -> list[MessageStub]:
        from .eml_parser import parse_eml

        with self._lock:
            _path, files = self._index.get(folder_id, ("", []))
            stubs: list[MessageStub] = []
            for i, f in enumerate(files):
                try:
                    m = parse_eml(f)
                    stubs.append(
                        MessageStub(
                            backend_id=(folder_id, i), sender=m.sender, subject=m.subject, date=m.date,
                            has_attachments=bool(m.visible_attachments), size=m.size,
                        )
                    )
                except Exception:
                    stubs.append(
                        MessageStub(backend_id=(folder_id, i), sender="", subject=f.stem, date=None)
                    )
            return stubs

    def get_message(self, message_id: MessageId) -> EmailMessage:
        from .eml_parser import parse_eml

        folder_id, idx = message_id
        with self._lock:
            folder_path, files = self._index.get(folder_id, ("", []))
            try:
                target = files[idx]
            except IndexError as exc:
                raise CorruptFileError(self._path, f"Message #{idx} is out of range.") from exc
            msg = parse_eml(target)
            msg.folder_path = folder_path
            msg.source_path = None
            return msg


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
_BACKENDS: dict[str, type[PstBackend]] = {
    "native": NativePstBackend,
    "libpff": LibpffBackend,
    "readpst": ReadpstBackend,
}


def available_backends() -> list[str]:
    found: list[str] = ["native"]  # always present, no dependencies
    try:
        mod = __import__("pypff")
        if hasattr(mod, "file"):  # the real libpff binding, not the astronomy pkg
            found.append("libpff")
    except ImportError:
        pass
    if shutil.which("readpst"):
        found.append("readpst")
    return found


def open_pst(path: str | Path, *, prefer: str = "native") -> PstDocument:
    """Open a ``.pst`` / ``.ost`` file and return a :class:`PstDocument`.

    Tries the *prefer* backend first, then the others. Raises
    :class:`~parsers.errors.MissingDependencyError` if no backend is usable, or
    :class:`~parsers.errors.CorruptFileError` if every usable backend fails on
    the file itself.
    """

    p = Path(path)
    if not p.is_file():
        raise CorruptFileError(str(p), "File does not exist.")

    order = [prefer] + [k for k in _BACKENDS if k != prefer]
    errors: list[str] = []
    missing: list[str] = []

    for key in order:
        backend = _BACKENDS[key]()
        try:
            backend.open(str(p))
        except MissingDependencyError as exc:
            missing.append(exc.message)
            continue
        except CorruptFileError as exc:
            errors.append(f"[{key}] {exc.detail}")
            backend.close()
            continue

        try:
            tree = backend.folder_tree()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"[{key}] building the folder tree failed: {exc}")
            backend.close()
            continue
        return PstDocument(path=str(p), root=tree, backend=backend)

    if errors:
        raise CorruptFileError(str(p), "\n".join(errors))
    raise ParserError(
        "Opening .pst / .ost files needs a PST engine, and none was found.\n\n"
        "Set up ONE of these, then reopen the file:\n\n"
        "  1. libpff Python bindings (best):\n"
        "       pip install libpff-python\n"
        "     This compiles from source - it needs a C toolchain "
        "(on Windows: 'Microsoft C++ Build Tools').\n\n"
        "  2. The 'readpst' command-line tool from libpst, anywhere on your PATH:\n"
        "       macOS:    brew install libpst\n"
        "       Linux:    apt install libpst   /   dnf install libpst\n"
        "       Windows:  install libpst via MSYS2/WSL, or drop readpst.exe on PATH\n\n"
        ".eml and .msg files work without any of this."
    )

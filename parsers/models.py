"""Format-agnostic data model shared by the parsers and the UI.

Nothing in :mod:`ui` imports ``email``, ``extract_msg`` or any PST library
directly - the widgets only ever see these dataclasses, which is what lets a
single viewer render an ``.eml``, an ``.msg`` and a message pulled from a
``.pst`` folder identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:  # avoid a runtime import cycle with pst_parser
    from .pst_parser import PstBackend


@dataclass(slots=True)
class Attachment:
    """A single attachment (or inline resource) belonging to a message."""

    filename: str
    mime_type: str
    data: bytes
    is_inline: bool = False
    #: Normalised Content-ID with angle brackets stripped, used to resolve
    #: ``src="cid:..."`` references in HTML bodies.
    content_id: str | None = None
    #: Coarse classification for the UI: ``"file"`` | ``"inline"`` | ``"message"``.
    #: Left blank by callers means "derive from :attr:`is_inline`".
    attach_kind: str = ""
    #: For ``attach_kind == "message"``: the parsed child message, when the
    #: parser was able to recurse into it (``message/rfc822`` part, embedded
    #: ``.msg``, embedded PST message). ``None`` if it stayed opaque.
    embedded: "EmailMessage | None" = None

    def __post_init__(self) -> None:
        if not self.attach_kind:
            self.attach_kind = "inline" if self.is_inline else "file"

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(slots=True)
class EmailMessage:
    """A fully parsed message, ready to hand to :class:`ui.viewer_widget.ViewerWidget`."""

    subject: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    date: datetime | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body_html: str | None = None
    body_text: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    #: Absolute path of the file this message came from (``None`` for PST items).
    source_path: str | None = None
    #: ``/``-joined PST folder path (``None`` for standalone files).
    folder_path: str | None = None
    #: Threading / identity headers, parsed out for convenience.
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    #: ``"low"`` | ``"normal"`` | ``"high"`` when known.
    importance: str | None = None
    #: Any of ``{"read", "flagged", "answered", "forwarded"}``.
    flags: frozenset[str] = frozenset()
    #: Message size in bytes when the source reports it (PST); else ``None``.
    size: int | None = None
    #: Cryptographic envelope, detected (not verified).
    is_signed: bool = False
    is_encrypted: bool = False
    #: Verbatim on-disk bytes when available (``.eml``); ``None`` for ``.msg`` /
    #: PST items, where no single canonical byte stream exists.
    raw_source: bytes | None = None

    @property
    def display_name(self) -> str:
        return self.subject.strip() or "(no subject)"

    @property
    def visible_attachments(self) -> list[Attachment]:
        """Attachments a user would expect to see in the attachment bar."""
        return [a for a in self.attachments if not a.is_inline]

    @property
    def inline_by_cid(self) -> dict[str, Attachment]:
        return {a.content_id: a for a in self.attachments if a.content_id}


@dataclass(slots=True)
class FolderNode:
    """One folder in a PST/OST tree.

    ``backend_id`` is an opaque handle understood only by the backend that
    produced it; the UI never inspects it.
    """

    name: str
    backend_id: Any
    children: list["FolderNode"] = field(default_factory=list)
    message_count: int = 0

    def iter_descendants(self) -> "list[FolderNode]":
        out: list[FolderNode] = []
        stack = list(self.children)
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(node.children)
        return out


@dataclass(slots=True)
class MessageStub:
    """A cheap row for the message-list table - no body, no attachments."""

    backend_id: Any
    sender: str
    subject: str
    date: datetime | None
    has_attachments: bool = False
    size: int | None = None
    unread: bool = False
    flagged: bool = False

    @property
    def display_subject(self) -> str:
        return self.subject.strip() or "(no subject)"


@dataclass(slots=True)
class PstDocument:
    """An open PST/OST file: its folder tree plus the live backend handle.

    The backend is only ever touched from worker threads (see
    :mod:`utils.workers`); the GUI thread holds this object but does not call
    into ``backend`` directly.
    """

    path: str
    root: FolderNode
    backend: "PstBackend"

    @property
    def display_name(self) -> str:
        from pathlib import Path

        return Path(self.path).name


#: What :func:`parsers.loader.load` can return.
LoadResult = Union[EmailMessage, PstDocument]

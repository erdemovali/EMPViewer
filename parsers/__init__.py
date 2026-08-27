"""Format-agnostic mail parsers for EMPViewer.

Public surface:

* :mod:`parsers.models`  - dataclasses shared by every parser and the UI.
* :mod:`parsers.errors`  - typed, user-facing exceptions.
* :func:`parsers.loader.load` - dispatch a path to the right parser.
"""

from __future__ import annotations

from .errors import CorruptFileError, MissingDependencyError, ParserError
from .loader import LoadResult, load
from .models import Attachment, EmailMessage, FolderNode, MessageStub, PstDocument

__all__ = [
    "Attachment",
    "CorruptFileError",
    "EmailMessage",
    "FolderNode",
    "LoadResult",
    "MessageStub",
    "MissingDependencyError",
    "ParserError",
    "PstDocument",
    "load",
]

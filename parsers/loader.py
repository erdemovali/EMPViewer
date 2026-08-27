"""Single dispatch point: a path in, an :class:`EmailMessage` or :class:`PstDocument` out."""

from __future__ import annotations

from pathlib import Path

from .errors import CorruptFileError, ParserError, UnsupportedFormatError
from .models import LoadResult

__all__ = ["LoadResult", "load"]


def load(path: str | Path) -> LoadResult:
    """Parse *path* according to its extension.

    Returns:
        * :class:`~parsers.models.EmailMessage` for ``.eml`` and ``.msg``.
        * :class:`~parsers.models.PstDocument` for ``.pst`` and ``.ost``.

    Raises:
        :class:`~parsers.errors.ParserError` (or a subclass) for every failure
        mode - missing file, unknown extension, missing dependency, corrupt data.
        Callers only need to catch ``ParserError`` and show ``err.message``.
    """

    p = Path(path)
    suffix = p.suffix.lower()

    if not p.exists():
        raise CorruptFileError(str(p), "The file no longer exists.")
    if not p.is_file():
        raise CorruptFileError(str(p), "This path is not a file.")

    try:
        if suffix == ".eml":
            from .eml_parser import parse_eml

            return parse_eml(p)
        if suffix == ".msg":
            from .msg_parser import parse_msg

            return parse_msg(p)
        if suffix in (".pst", ".ost"):
            from .pst_parser import open_pst

            return open_pst(p)
    except ParserError:
        raise
    except Exception as exc:  # noqa: BLE001 - defensive catch-all -> typed error
        raise CorruptFileError(str(p), f"Unexpected error while parsing: {exc}") from exc

    raise UnsupportedFormatError(str(p))

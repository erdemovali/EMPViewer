"""Typed, user-facing exceptions raised by the parser layer.

Every low-level failure (a malformed file, a missing third-party library, an
unreadable PST node) is converted into one of these before it reaches the GUI so
that :class:`~PySide6.QtWidgets.QMessageBox` can show a clean, actionable message
instead of a raw traceback.
"""

from __future__ import annotations


class ParserError(Exception):
    """Base class for every error the parser layer surfaces to the user.

    Attributes:
        message: A human-readable, single-paragraph explanation suitable for a
            message box.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


class MissingDependencyError(ParserError):
    """A required third-party package is not importable.

    The message always names the exact ``pip install`` command that fixes it.
    """

    def __init__(self, package: str, *, purpose: str, pip_name: str | None = None) -> None:
        pip_name = pip_name or package
        super().__init__(
            f"The '{package}' library is required to {purpose}, but it is not "
            f"installed.\n\nInstall it with:\n\n    pip install {pip_name}"
        )
        self.package = package
        self.pip_name = pip_name


class CorruptFileError(ParserError):
    """The file exists and is readable but could not be parsed as expected."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(
            f"'{path}' could not be opened.\n\n{detail}"
        )
        self.path = path
        self.detail = detail


class UnsupportedFormatError(ParserError):
    """The file extension is not one EMPViewer knows how to open."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"'{path}' is not a supported mail file.\n\n"
            "Supported formats: .eml, .msg, .pst, .ost"
        )
        self.path = path

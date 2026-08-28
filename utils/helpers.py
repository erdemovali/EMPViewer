"""Resource-path resolution, filename hygiene, temp-file handling and OS hand-off.

Kept free of any ``ui`` import so the parser tests can use it without spinning up
Qt. The two functions that do need Qt (:func:`open_with_os`) import it lazily.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterable

#: Every extension EMPViewer can open. Reused by the loader, the drag-and-drop
#: filter and the Windows ``--register`` helper so they can never drift apart.
SUPPORTED_EXTS: frozenset[str] = frozenset(
    {".eml", ".msg", ".pst", ".ost", ".ics", ".vcf", ".mbox"}
)

#: Extensions that go through the PST/OST backend path.
PST_EXTS: frozenset[str] = frozenset({".pst", ".ost"})


# --------------------------------------------------------------------------- #
# Resource paths (PyInstaller-aware)
# --------------------------------------------------------------------------- #
def resource_path(relative: str | os.PathLike[str]) -> Path:
    """Return an absolute path to a bundled resource.

    Works both from source (``<repo>/<relative>``) and from a frozen PyInstaller
    build, where data files live under ``sys._MEIPASS``.
    """

    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    # utils/helpers.py -> repo root is one level up from this file's parent.
    return (Path(__file__).resolve().parent.parent / relative).resolve()


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) is True


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
_RESERVED_WIN = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_filename(name: str, *, fallback: str = "attachment") -> str:
    """Sanitise an arbitrary string into a safe single path component."""

    # Discard any directory components first ("../../etc/passwd" -> "passwd").
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD_CHARS.sub("_", name.strip()).strip(". ")
    if not name:
        return fallback
    stem, dot, ext = name.partition(".")
    if stem.upper() in _RESERVED_WIN:
        stem = f"_{stem}"
    name = stem + dot + ext
    return name[:200] if len(name) > 200 else name


def format_datetime(dt, *, with_tz: bool = True, style: str = "local") -> str:
    """Format a datetime for display, converted to the viewer's local time zone.

    A naive datetime is assumed to already be local; an aware one is converted.
    ``style`` is one of ``"local"`` (``2024-03-05 10:30`` + tz label),
    ``"iso"`` (``2024-03-05T10:30``) or ``"rfc"`` (``Tue, 05 Mar 2024 10:30``).
    """

    if dt is None:
        return ""
    try:
        local = dt.astimezone()
    except (ValueError, OSError, OverflowError):
        local = dt

    if style == "iso":
        try:
            return local.isoformat(timespec="minutes")
        except (TypeError, ValueError):
            return local.strftime("%Y-%m-%dT%H:%M")
    if style == "rfc":
        from email.utils import format_datetime as _rfc

        try:
            return _rfc(local).rsplit(":", 1)[0]  # drop seconds
        except (TypeError, ValueError):
            pass

    text = local.strftime("%Y-%m-%d %H:%M")
    if with_tz and local.tzinfo is not None:
        off = local.strftime("%z")  # e.g. +0300
        if len(off) == 5:
            text = f"{text} UTC{off[:3]}:{off[3:]}"
    return text


def human_size(num_bytes: int) -> str:
    """Format a byte count like ``1.4 MB``."""

    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"  # pragma: no cover - unreachable


def has_supported_extension(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def is_supported_file(path: str | os.PathLike[str]) -> bool:
    """True if *path* is an existing regular file with a supported extension."""

    p = Path(path)
    try:
        return p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    except OSError:
        return False


def is_pst_like(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in PST_EXTS


def filter_supported(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    return [str(p) for p in paths if is_supported_file(p)]


# --------------------------------------------------------------------------- #
# Session temp directory (for "open attachment with OS app")
# --------------------------------------------------------------------------- #
_session_dir: Path | None = None


def session_temp_dir() -> Path:
    """Return a per-process temp directory, created on first use and removed at exit."""

    global _session_dir
    if _session_dir is None or not _session_dir.exists():
        _session_dir = Path(tempfile.mkdtemp(prefix="empviewer-"))
        atexit.register(_cleanup_session_dir)
    return _session_dir


def _cleanup_session_dir() -> None:
    if _session_dir and _session_dir.exists():
        shutil.rmtree(_session_dir, ignore_errors=True)


def write_temp_attachment(filename: str, data: bytes) -> Path:
    """Write attachment bytes to a uniquely-named file in the session temp dir."""

    folder = session_temp_dir()
    safe = safe_filename(filename)
    target = folder / safe
    counter = 1
    while target.exists():
        target = folder / f"{Path(safe).stem} ({counter}){Path(safe).suffix}"
        counter += 1
    target.write_bytes(data)
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - best effort on exotic filesystems
        pass
    return target


# --------------------------------------------------------------------------- #
# OS hand-off
# --------------------------------------------------------------------------- #
def open_with_os(path: str | os.PathLike[str]) -> bool:
    """Open *path* with the operating system's default handler.

    Uses Qt's :class:`QDesktopServices` when a ``QApplication`` exists, falling
    back to the platform shell otherwise. Returns ``True`` on apparent success.
    """

    p = Path(path)
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
    except Exception:
        pass

    try:  # pragma: no cover - platform specific fallbacks
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(p)])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(p)])
        return True
    except Exception:
        return False

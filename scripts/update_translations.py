"""Regenerate the Qt translation catalogues.

    python scripts/update_translations.py            # lupdate + lrelease for every lang
    python scripts/update_translations.py --check     # fail if a .ts would change (CI)

Finds ``lupdate`` / ``lrelease`` from the installed PySide6 (no system Qt
needed). Source roots scanned: ui/, parsers/, utils/, main.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_DIR = ROOT / "translations"
SOURCE_DIRS = ["ui", "parsers", "utils"]
SOURCE_FILES = ["main.py"]


def _python_sources() -> list[str]:
    """Every .py file lupdate should scan (it does not recurse dirs itself)."""

    out = [str(ROOT / f) for f in SOURCE_FILES if (ROOT / f).exists()]
    for d in SOURCE_DIRS:
        out += [str(p) for p in sorted((ROOT / d).rglob("*.py")) if "__pycache__" not in p.parts]
    return out


def _tool(name: str) -> list[str]:
    """Return an argv prefix that runs the Qt *name* tool."""

    try:
        import PySide6

        exe = Path(PySide6.__file__).parent / (name + (".exe" if sys.platform.startswith("win") else ""))
        if exe.exists():
            return [str(exe)]
    except ImportError:
        pass
    # Fall back to a console script on PATH.
    return [f"pyside6-{name}"]


def _languages() -> list[str]:
    return sorted(p.stem.split("_", 1)[1] for p in TS_DIR.glob("empviewer_*.ts"))


def run(check: bool) -> int:
    lupdate = _tool("lupdate")
    lrelease = _tool("lrelease")
    srcs = _python_sources()

    def _norm(data: bytes) -> bytes:
        # lupdate writes CRLF on Windows; .gitattributes normalises .ts to LF.
        return data.replace(b"\r\n", b"\n")

    rc = 0
    for lang in _languages():
        ts = TS_DIR / f"empviewer_{lang}.ts"
        before = ts.read_bytes() if ts.exists() else b""
        subprocess.run([*lupdate, *srcs, "-ts", str(ts), "-no-obsolete", "-locations", "none"], check=True)
        after = ts.read_bytes()
        if check:
            ts.write_bytes(before)  # leave the tree untouched in CI
            if _norm(after) != _norm(before):
                print(f"::error:: {ts.name} is stale - run scripts/update_translations.py")
                rc = 1
        else:
            if _norm(after) != _norm(before):
                ts.write_bytes(_norm(after))  # keep the repo copy LF-only
            subprocess.run([*lrelease, str(ts)], check=True)
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI mode: fail if a .ts changed")
    raise SystemExit(run(ap.parse_args().check))

"""Automated PyInstaller build for EMPViewer.

Usage::

    python build.py                # platform default (Windows: onefile .exe, macOS: .app)
    python build.py --onedir       # faster cold start, folder instead of single file
    python build.py --onefile
    python build.py --clean        # wipe build/ and dist/ first
    python build.py --dmg          # macOS only: also produce a .dmg

Outputs land in ``dist/``.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
APP_NAME = "EMPViewer"
BUNDLE_ID = "com.empviewer.app"

DATA_SEP = ";" if os.name == "nt" else ":"

DOC_EXTENSIONS = ["eml", "msg", "pst", "ost"]


# --------------------------------------------------------------------------- #
# Icons
# --------------------------------------------------------------------------- #
def ensure_icons() -> None:
    """Materialise assets/app.ico + app.png from vector.svg (repo root).

    The window/taskbar icon at runtime comes straight from ``vector.svg`` via
    :mod:`utils.branding`; PyInstaller's ``--icon`` needs a real .ico/.icns, so
    we rasterise them here. Falls back to a plain coloured tile if anything
    about SVG rendering is unavailable.
    """

    ASSETS.mkdir(exist_ok=True)
    ico, icns, png = ASSETS / "app.ico", ASSETS / "app.icns", ASSETS / "app.png"
    try:
        from PySide6.QtCore import QByteArray, QRectF, Qt
        from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

        _ = QGuiApplication.instance() or QGuiApplication([])
        brand = QColor(66, 133, 244)

        def canvas(size: int) -> QImage:
            return QImage(size, size, QImage.Format.Format_ARGB32)

        svg_path = ROOT / "vector.svg"
        renderer = None
        if svg_path.exists():
            try:
                from PySide6.QtSvg import QSvgRenderer

                text = svg_path.read_text(encoding="utf-8").replace('fill="black"', f'fill="{brand.name()}"')
                renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
            except Exception:
                renderer = None

        def render(size: int) -> QImage:
            img = canvas(size)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if renderer is not None and renderer.isValid():
                vb = renderer.viewBoxF()
                if vb.width() <= 0:
                    vb = QRectF(0, 0, 50, 53)
                pad = size * 0.14
                avail = size - 2 * pad
                scale = min(avail / vb.width(), avail / vb.height())
                w, h = vb.width() * scale, vb.height() * scale
                renderer.render(p, QRectF((size - w) / 2, (size - h) / 2, w, h))
            else:
                p.setBrush(brand)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, size, size, size * 0.18, size * 0.18)
            p.end()
            return img

        render(256).save(str(png), "PNG")
        render(256).save(str(ico), "ICO")
        if sys.platform == "darwin":
            _make_icns(icns, render)
        print("Rendered app icons from", "vector.svg" if renderer else "fallback tile")
    except Exception as exc:  # pragma: no cover
        print(f"Could not render icons ({exc}); continuing without a custom icon.")


def _make_icns(icns_path: Path, render) -> None:
    """Build a proper multi-resolution .icns via macOS `iconutil`.

    Falls back to a bare 512px PNG named .icns if iconutil is unavailable (older
    PyInstaller then converts it, given Pillow).
    """

    iconutil = shutil.which("iconutil")
    if not iconutil:
        render(512).save(str(icns_path), "PNG")
        return
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "app.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            render(size).save(str(iconset / f"icon_{size}x{size}.png"), "PNG")
            render(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"), "PNG")
        try:
            subprocess.check_call([iconutil, "-c", "icns", str(iconset), "-o", str(icns_path)])
        except (subprocess.CalledProcessError, OSError):
            render(512).save(str(icns_path), "PNG")


# --------------------------------------------------------------------------- #
# PyInstaller invocation
# --------------------------------------------------------------------------- #
def pyinstaller_args(mode: str) -> list[str]:
    args: list[str] = [
        "--name", APP_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        mode,  # --onefile or --onedir
        "--add-data", f"{ASSETS}{DATA_SEP}assets",
        "--add-data", f"{ROOT / 'vector.svg'}{DATA_SEP}.",
        # extract_msg ships data files and lazily-imported submodules.
        "--collect-all", "extract_msg",
        "--collect-submodules", "extract_msg",
        # Optional / dynamically imported deps.
        "--hidden-import", "dateutil",
        "--hidden-import", "dateutil.parser",
        "--hidden-import", "compressed_rtf",
        "--hidden-import", "striprtf",
        "--hidden-import", "striprtf.striprtf",
    ]

    if sys.platform.startswith("win") and (ASSETS / "app.ico").exists():
        args += ["--icon", str(ASSETS / "app.ico")]
    if sys.platform == "darwin":
        icon = ASSETS / "app.icns"
        if icon.exists():
            args += ["--icon", str(icon)]
        args += ["--osx-bundle-identifier", BUNDLE_ID]

    # Try to bundle a PST backend if one is importable.
    for mod in ("pypff",):
        try:
            __import__(mod)
            args += ["--hidden-import", mod, "--collect-all", mod]
        except ImportError:
            pass

    args.append(str(ROOT / "main.py"))
    return args


def run_pyinstaller(mode: str) -> None:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        sys.exit("PyInstaller is not installed. Run:  pip install pyinstaller")
    print("PyInstaller", "->", mode)
    pyi.run(pyinstaller_args(mode))


# --------------------------------------------------------------------------- #
# macOS post-processing
# --------------------------------------------------------------------------- #
def patch_mac_info_plist() -> None:
    app_plist = ROOT / "dist" / f"{APP_NAME}.app" / "Contents" / "Info.plist"
    if not app_plist.exists():
        print("No .app bundle found; skipping Info.plist patch.")
        return
    with app_plist.open("rb") as fh:
        info = plistlib.load(fh)

    info["CFBundleDocumentTypes"] = [
        {
            "CFBundleTypeName": "Mail message",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": DOC_EXTENSIONS,
            "CFBundleTypeIconFile": "app.icns",
        }
    ]
    info.setdefault("LSMinimumSystemVersion", "11.0")
    info["NSHighResolutionCapable"] = True

    with app_plist.open("wb") as fh:
        plistlib.dump(info, fh)
    print("Patched Info.plist with CFBundleDocumentTypes for:", ", ".join(DOC_EXTENSIONS))


def make_windows_installer() -> None:
    """Wrap the onedir build into dist/EMPViewer-Setup.exe using Inno Setup."""

    iss = ROOT / "packaging" / "EMPViewer.iss"
    onedir = ROOT / "dist" / APP_NAME
    if not onedir.is_dir():
        print("Installer needs a --onedir build (dist/EMPViewer/ not found); skipping.")
        return
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if not iscc:
        for guess in (
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ):
            if Path(guess).exists():
                iscc = guess
                break
    if not iscc:
        print(
            "Inno Setup (iscc.exe) not found. Install it from https://jrsoftware.org/isdl.php\n"
            "then run:  iscc packaging\\EMPViewer.iss"
        )
        return
    subprocess.check_call([iscc, str(iss)])
    print("Wrote", ROOT / "dist" / f"{APP_NAME}-Setup.exe")


def make_dmg() -> None:
    app = ROOT / "dist" / f"{APP_NAME}.app"
    dmg = ROOT / "dist" / f"{APP_NAME}.dmg"
    if not app.exists():
        print("No .app to package into a DMG.")
        return
    if dmg.exists():
        dmg.unlink()
    subprocess.check_call(
        ["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(app), "-ov", "-format", "UDZO", str(dmg)]
    )
    print("Wrote", dmg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Build EMPViewer with PyInstaller.")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--onefile", action="store_const", dest="mode", const="--onefile")
    g.add_argument("--onedir", action="store_const", dest="mode", const="--onedir")
    parser.add_argument("--clean", action="store_true", help="Remove build/ and dist/ first.")
    parser.add_argument("--dmg", action="store_true", help="macOS: also build a .dmg.")
    parser.add_argument(
        "--installer",
        action="store_true",
        help="Windows: also build dist/EMPViewer-Setup.exe via Inno Setup (implies --onedir).",
    )
    ns = parser.parse_args()

    default_mode = "--onefile" if sys.platform.startswith("win") else "--onedir"
    mode = ns.mode or ("--onedir" if ns.installer else default_mode)

    if ns.clean:
        for d in ("build", "dist"):
            shutil.rmtree(ROOT / d, ignore_errors=True)
        for spec in ROOT.glob("*.spec"):
            spec.unlink()

    ensure_icons()
    run_pyinstaller(mode)

    if sys.platform == "darwin":
        patch_mac_info_plist()
        if ns.dmg:
            make_dmg()
    elif sys.platform.startswith("win") and ns.installer:
        make_windows_installer()

    dist = ROOT / "dist"
    print("\nBuild finished. Artifacts in:", dist)
    for p in sorted(dist.glob("*")):
        print("  -", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Application logo / icon, rendered from ``vector.svg`` at the repo root.

The SVG ships as a solid black glyph; :func:`make_app_icon` recolours it to the
brand accent so it stays visible on both light and dark shells, pads it, and
rasterises the common icon sizes into a single :class:`QIcon`.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

from utils.helpers import resource_path

BRAND_HEX = "#4285F4"
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


@lru_cache(maxsize=4)
def _svg_bytes(color_hex: str) -> bytes:
    path = resource_path("vector.svg")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Minimal fallback glyph so the app always has an icon.
        text = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 53">'
            '<rect width="50" height="53" rx="10" fill="COLOR"/></svg>'
        )
    text = text.replace('fill="black"', f'fill="{color_hex}"').replace("COLOR", color_hex)
    return text.encode("utf-8")


def _render(size: int, color_hex: str, *, pad_ratio: float = 0.14) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(_svg_bytes(color_hex)))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    vb = renderer.viewBoxF()
    if vb.width() <= 0 or vb.height() <= 0:
        vb = QRectF(0, 0, 50, 53)
    pad = size * pad_ratio
    avail = size - 2 * pad
    scale = min(avail / vb.width(), avail / vb.height())
    w, h = vb.width() * scale, vb.height() * scale
    target = QRectF((size - w) / 2, (size - h) / 2, w, h)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, target)
    painter.end()
    return pm


@lru_cache(maxsize=2)
def make_app_icon(color_hex: str = BRAND_HEX) -> QIcon:
    """Return the application icon as a multi-resolution :class:`QIcon`."""

    icon = QIcon()
    try:
        for s in _ICON_SIZES:
            icon.addPixmap(_render(s, color_hex))
    except Exception:
        # QtSvg missing or render failure - fall back to whatever QIcon can do
        # with the raw file (PySide6 bundles the SVG image plugin).
        icon = QIcon(str(resource_path("vector.svg")))
    return icon


def logo_pixmap(size: int = 64, color_hex: str = BRAND_HEX) -> QPixmap:
    """A single padded pixmap of the logo, for e.g. the About dialog."""

    try:
        return _render(size, color_hex)
    except Exception:
        return QPixmap(str(resource_path("vector.svg")))

"""Application logo / icon.

Primary source: the hand-designed set under ``icons/app/`` (checked in, also
bundled into the frozen app). ``EMPViewer_<size>.png`` holds the glyph at every
common icon size; :func:`make_app_icon` collects them into one multi-resolution
:class:`QIcon` for the window / dock / taskbar, and :func:`logo_pixmap` returns a
single bitmap for the About dialog.

Fallback (``icons/`` missing): down-sample ``emplogo.png`` at the repo root, and
failing even that, a plain brand-coloured tile so the app always has an icon.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

from utils.helpers import resource_path

BRAND_HEX = "#4285F4"
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
#: Exact-size PNGs shipped in ``icons/app/``.
_PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
_LOGO_FILE = "emplogo.png"


def _app_png(size: int):
    """Path to the hand-designed app glyph at ``size`` px, if it exists."""

    p = resource_path(f"icons/app/EMPViewer_{size}.png")
    return p if p.exists() else None


@lru_cache(maxsize=1)
def _has_designed_icons() -> bool:
    return _app_png(256) is not None


@lru_cache(maxsize=1)
def _source_image() -> QImage | None:
    """The raw fallback logo bitmap, loaded once. ``None`` if it can't be read."""

    img = QImage(str(resource_path(_LOGO_FILE)))
    return None if img.isNull() else img.convertToFormat(QImage.Format.Format_ARGB32)


def _smooth_downscale(img: QImage, target: int) -> QImage:
    """Halve repeatedly (smooth) down to ~2x target, then a final smooth scale.

    One-shot scaling a ~1400px source straight to 16px is muddy; progressive
    halving keeps small icons crisp.
    """

    cur = img
    while cur.width() > target * 2 and cur.height() > target * 2:
        cur = cur.scaled(
            cur.width() // 2,
            cur.height() // 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return cur.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _fallback_pixmap(size: int, color_hex: str) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor(color_hex))
    p.setPen(Qt.PenStyle.NoPen)
    r = size * 0.18
    p.drawRoundedRect(0, 0, size, size, r, r)
    p.end()
    return pm


def _render(size: int, color_hex: str, *, pad_ratio: float = 0.08) -> QPixmap:
    """A ``size``x``size`` transparent pixmap with the logo centred and padded.

    Uses the exact-size designed PNG when present; otherwise pads / down-samples
    ``emplogo.png``; otherwise a plain brand tile.
    """

    png = _app_png(size) if _has_designed_icons() else None
    if png is not None:
        pm = QPixmap(str(png))
        if not pm.isNull():
            return pm

    if _has_designed_icons():
        # Odd size (e.g. 24): scale the nearest larger designed PNG.
        bigger = next((s for s in _PNG_SIZES if s >= size), _PNG_SIZES[-1])
        src = QPixmap(str(_app_png(bigger) or ""))
        if not src.isNull():
            return src.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    src_img = _source_image()
    if src_img is None:
        return _fallback_pixmap(size, color_hex)

    pad = round(size * pad_ratio)
    inner = max(1, size - 2 * pad)
    scaled = _smooth_downscale(src_img, inner)

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    x = (size - scaled.width()) / 2
    y = (size - scaled.height()) / 2
    painter.drawImage(QRectF(x, y, scaled.width(), scaled.height()), scaled)
    painter.end()
    return pm


@lru_cache(maxsize=2)
def make_app_icon(color_hex: str = BRAND_HEX) -> QIcon:
    """Return the application icon as a multi-resolution :class:`QIcon`."""

    icon = QIcon()
    try:
        sizes = _PNG_SIZES if _has_designed_icons() else _ICON_SIZES
        for s in sizes:
            icon.addPixmap(_render(s, color_hex))
    except Exception:
        icon = QIcon(str(resource_path(_LOGO_FILE)))
    return icon


def logo_pixmap(size: int = 64, color_hex: str = BRAND_HEX) -> QPixmap:
    """A single padded pixmap of the logo, for e.g. the About dialog."""

    try:
        return _render(size, color_hex)
    except Exception:
        return QPixmap(str(resource_path(_LOGO_FILE)))

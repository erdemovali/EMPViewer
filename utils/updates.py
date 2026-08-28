"""Check GitHub Releases for a newer version.

A version check is a network request, so it is opt-in (Preferences) or manual
(Help > Check for Updates). Nothing is sent except the plain HTTPS GET.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from typing import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from utils.helpers import resource_path

RELEASES_PAGE = "https://github.com/erdemovali/EMPViewer/releases"
_API_LATEST = "https://api.github.com/repos/erdemovali/EMPViewer/releases/latest"

#: (latest_version | None, is_newer, download_url)
ResultCb = Callable[[str | None, bool, str], None]


def pick_asset(assets: list[dict]) -> str | None:
    """Best download URL for this platform from a GitHub release's assets."""

    names = [(a.get("name", ""), a.get("browser_download_url", "")) for a in assets or []]
    names = [(n.lower(), url) for n, url in names if url]
    if sys.platform.startswith("win"):
        for n, url in names:  # prefer the installer
            if "setup" in n and n.endswith(".exe"):
                return url
        for n, url in names:
            if n.endswith(".exe"):
                return url
    elif sys.platform == "darwin":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        for n, url in names:
            if n.endswith(".dmg") and arch in n:
                return url
        for n, url in names:
            if n.endswith(".dmg"):
                return url
    return None


def current_version() -> str:
    try:
        return (resource_path("VERSION").read_text(encoding="utf-8").strip()) or "0.0.0"
    except OSError:
        return "0.0.0"


def _tuple(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return _tuple(latest) > _tuple(current)


class UpdateChecker(QObject):
    """Owns the QNetworkAccessManager for the lifetime of one check."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)

    def check(self, on_result: ResultCb) -> None:
        req = QNetworkRequest(QUrl(_API_LATEST))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", b"EMPViewer-update-check")
        reply = self._nam.get(req)

        def _done() -> None:
            latest: str | None = None
            url = RELEASES_PAGE
            try:
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    data = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
                    tag = str(data.get("tag_name") or "").strip()
                    latest = tag or None
                    asset = pick_asset(data.get("assets") or [])
                    if asset:
                        url = asset
            except (ValueError, UnicodeDecodeError):
                latest = None
            reply.deleteLater()
            cur = current_version()
            newer = bool(latest) and is_newer(latest, cur)
            on_result(latest, newer, url)

        reply.finished.connect(_done)

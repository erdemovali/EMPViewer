"""Windows shell integration: file-type registration for EMPViewer.

Registers EMPViewer as a *candidate* handler for ``.eml/.msg/.pst/.ost`` so it
shows up in the "Open with" menu and in Settings -> Default apps. Per-user only
(``HKEY_CURRENT_USER``) - no administrator rights needed.

Windows 10/11 does **not** let an app silently seize an already-owned default
(e.g. ``.msg``/``.pst``, owned by Outlook). ``set_default()`` therefore just
opens the Default-apps UI so the user can confirm the switch in one click.

CLI (also works from the frozen .exe):

    EMPViewer.exe --register        # add EMPViewer as a handler (per-user)
    EMPViewer.exe --unregister      # remove it
    EMPViewer.exe --set-default     # open Settings -> Default apps
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ID = "EMPViewer"
APP_DESCRIPTION = "Viewer for .eml, .msg, .pst and .ost mail files"

# ext -> (ProgID, friendly type name)
_TYPES: dict[str, tuple[str, str]] = {
    ".eml": ("EMPViewer.eml", "E-mail Message"),
    ".msg": ("EMPViewer.msg", "Outlook Message"),
    ".pst": ("EMPViewer.pst", "Outlook Data File"),
    ".ost": ("EMPViewer.ost", "Outlook Offline Data File"),
}

_CAP_KEY = rf"Software\{APP_ID}\Capabilities"
_REGAPPS_KEY = r"Software\RegisteredApplications"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _persistent_icons_dir() -> Path | None:
    """``icons/filetypes`` at a path that stays valid after this process exits.

    * source checkout     -> ``<repo>/icons/filetypes``.
    * frozen ``--onedir`` -> ``<_MEIPASS>/icons/filetypes`` (sits next to the
      .exe, so a registry path into it keeps resolving).
    * frozen ``--onefile`` -> ``None``: ``_MEIPASS`` is a temp dir wiped on exit.
      :func:`_cache_icons` copies the icons somewhere stable instead.
    """

    if not getattr(sys, "frozen", False):
        d = _repo_root() / "icons" / "filetypes"
        return d if d.is_dir() else None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    meipass = Path(meipass).resolve()
    exe_dir = Path(sys.executable).resolve().parent
    if meipass == exe_dir or exe_dir in meipass.parents:
        d = meipass / "icons" / "filetypes"
        return d if d.is_dir() else None
    return None


def _icon_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_ID / "icons"


def _cache_icons() -> Path | None:
    """Copy the bundled per-type ``.ico`` files out of an ephemeral ``_MEIPASS``
    into a stable per-user dir, so the portable single-file .exe still gets
    distinct Explorer icons. Returns the dir, or ``None`` if nothing to copy."""

    meipass = getattr(sys, "_MEIPASS", None)
    src = Path(meipass) / "icons" / "filetypes" if meipass else None
    if not src or not src.is_dir():
        return None
    dst = _icon_cache_dir()
    dst.mkdir(parents=True, exist_ok=True)
    copied = False
    for ext in _TYPES:
        f = src / f"{ext.lstrip('.')}.ico"
        if f.exists():
            shutil.copyfile(f, dst / f.name)
            copied = True
    return dst if copied else None


def _open_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    script = _repo_root() / "main.py"
    return f'"{sys.executable}" "{script}" "%1"'


def _app_icon() -> str:
    """Icon spec for the app itself (Default Programs capability block)."""

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}",0'
    for cand in (_repo_root() / "assets" / "app.ico", _repo_root() / "icons" / "app" / "EMPViewer.ico"):
        if cand.exists():
            return f'"{cand}",0'
    return f'"{sys.executable}",0'


def _icon_for(ext: str, icons_dir: Path | None) -> str:
    """Per-extension icon spec, e.g. ``.eml`` -> ``<icons_dir>/eml.ico``.

    Falls back to the app icon when no hand-designed file is reachable.
    """

    if icons_dir:
        ico = icons_dir / f"{ext.lstrip('.')}.ico"
        if ico.exists():
            return f'"{ico}",0'
    return _app_icon()


def _notify_shell() -> None:
    try:
        import ctypes

        # SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def _require_windows(action: str) -> bool:
    if not sys.platform.startswith("win"):
        print(f"--{action} is only supported on Windows.")
        return False
    return True


# --------------------------------------------------------------------------- #
# register
# --------------------------------------------------------------------------- #
def register() -> int:
    if not _require_windows("register"):
        return 1
    import winreg

    HKCU = winreg.HKEY_CURRENT_USER
    open_cmd = _open_command()
    app_icon = _app_icon()
    icons_dir = _persistent_icons_dir() or _cache_icons()

    def sv(path: str, value: str, name: str = "") -> None:
        with winreg.CreateKey(HKCU, path) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    for ext, (prog_id, friendly) in _TYPES.items():
        # ProgID  (each extension gets its own hand-designed Explorer icon)
        sv(rf"Software\Classes\{prog_id}", friendly)
        sv(rf"Software\Classes\{prog_id}", friendly, "FriendlyTypeName")
        sv(rf"Software\Classes\{prog_id}\DefaultIcon", _icon_for(ext, icons_dir))
        sv(rf"Software\Classes\{prog_id}\shell\open", f"Open with {APP_ID}", "FriendlyAppName")
        sv(rf"Software\Classes\{prog_id}\shell\open\command", open_cmd)
        # advertise on the extension without stealing the current default
        sv(rf"Software\Classes\{ext}\OpenWithProgids", "", prog_id)

    # Default Programs capability block -> makes EMPViewer selectable in Settings
    sv(_CAP_KEY, APP_ID, "ApplicationName")
    sv(_CAP_KEY, APP_DESCRIPTION, "ApplicationDescription")
    sv(_CAP_KEY, app_icon, "ApplicationIcon")
    for ext, (prog_id, _friendly) in _TYPES.items():
        sv(rf"{_CAP_KEY}\FileAssociations", prog_id, ext)
    sv(_REGAPPS_KEY, _CAP_KEY, APP_ID)

    _notify_shell()
    print(
        "Registered EMPViewer as a handler for .eml / .msg / .pst / .ost "
        "(current user).\nMake it the default via:  EMPViewer.exe --set-default"
    )
    return 0


# --------------------------------------------------------------------------- #
# unregister
# --------------------------------------------------------------------------- #
def unregister() -> int:
    if not _require_windows("unregister"):
        return 1
    import winreg

    HKCU = winreg.HKEY_CURRENT_USER

    def rmtree(path: str) -> None:
        try:
            with winreg.OpenKey(HKCU, path) as key:
                while True:
                    try:
                        rmtree(path + "\\" + winreg.EnumKey(key, 0))
                    except OSError:
                        break
            winreg.DeleteKey(HKCU, path)
        except FileNotFoundError:
            pass

    for ext, (prog_id, _f) in _TYPES.items():
        rmtree(rf"Software\Classes\{prog_id}")
        try:
            with winreg.OpenKey(
                HKCU, rf"Software\Classes\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.DeleteValue(k, prog_id)
        except FileNotFoundError:
            pass

    rmtree(rf"Software\{APP_ID}")
    try:
        with winreg.OpenKey(HKCU, _REGAPPS_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_ID)
    except FileNotFoundError:
        pass

    # Drop the per-user icon cache left by the portable .exe (if any).
    shutil.rmtree(_icon_cache_dir(), ignore_errors=True)

    _notify_shell()
    print("Removed EMPViewer file associations for the current user.")
    return 0


# --------------------------------------------------------------------------- #
# set default (open the OS UI - Windows forbids doing this silently)
# --------------------------------------------------------------------------- #
def set_default() -> int:
    if not _require_windows("set-default"):
        return 1
    import os

    register()
    try:
        os.startfile("ms-settings:defaultapps")  # type: ignore[attr-defined]
        print(
            "Opened Settings -> Default apps. Pick EMPViewer for each mail file type, "
            "or use 'Choose defaults by file type'."
        )
    except OSError:
        print("Could not open Settings. Set defaults manually via right-click -> Open with.")
    return 0

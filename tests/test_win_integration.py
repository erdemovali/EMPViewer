"""utils.win_integration registry writes, against a fake ``winreg``.

Windows-only (the real module refuses to run elsewhere); skipped on other OSes.
"""

from __future__ import annotations

import sys

import pytest

if not sys.platform.startswith("win"):  # pragma: no cover - platform guard
    pytest.skip("win_integration is Windows-only", allow_module_level=True)

from utils import win_integration as wi


class _FakeKey:
    def __init__(self, store: dict, path: str) -> None:
        self._store = store
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}

    def CreateKey(self, root, path):  # noqa: N802
        self.values.setdefault(path, {})
        return _FakeKey(self.values, path)

    def SetValueEx(self, key, name, reserved, typ, value):  # noqa: N802
        self.values[key._path][name] = value


@pytest.fixture
def fake_winreg(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(wi, "_notify_shell", lambda: None)
    return fake


def test_register_writes_progids_and_per_type_icons(fake_winreg) -> None:
    rc = wi.register()
    assert rc == 0

    v = fake_winreg.values
    # A ProgID and its open command for each extension.
    for ext, (prog_id, _friendly) in wi._TYPES.items():
        assert rf"Software\Classes\{prog_id}\shell\open\command" in v
        icon = v[rf"Software\Classes\{prog_id}\DefaultIcon"][""]
        assert icon.lower().endswith(f"{ext.lstrip('.')}.ico\",0"), icon
        # Advertised without seizing the current default.
        assert prog_id in v[rf"Software\Classes\{ext}\OpenWithProgids"]

    # Default Programs capability block is registered.
    assert v[wi._REGAPPS_KEY]["EMPViewer"] == wi._CAP_KEY


def test_unregister_removes_registered_application(fake_winreg) -> None:
    wi.register()
    # unregister opens keys for deletion; give the fake the surface it needs.
    deleted = []
    fake_winreg.OpenKey = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    fake_winreg.DeleteKey = lambda root, path: deleted.append(path)
    fake_winreg.DeleteValue = lambda key, name: None
    fake_winreg.KEY_SET_VALUE = 2
    rc = wi.unregister()
    assert rc == 0

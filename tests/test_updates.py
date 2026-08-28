"""utils.updates - version comparison for the GitHub release check."""

from __future__ import annotations

from utils.updates import current_version, is_newer


def test_current_version_reads_the_version_file():
    v = current_version()
    assert v and v[0].isdigit()
    assert v.count(".") >= 1


def test_is_newer_handles_v_prefix_and_ordering():
    assert is_newer("v1.2.0", "1.0.0")
    assert is_newer("2.0.0", "v1.9.9")
    assert is_newer("1.0.10", "1.0.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("v1.0.0", "1.0.1")
    assert not is_newer("0.9.0", "1.0.0")


def test_is_newer_ignores_prerelease_and_build_suffixes():
    # A pre-release tag is treated as its base version (good enough for a nudge).
    assert is_newer("v1.1.0-rc1", "1.0.0")
    assert not is_newer("1.0.0-rc2", "1.0.0")
    assert not is_newer("1.0.0+build.5", "1.0.0")

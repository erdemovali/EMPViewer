"""Dispatch + helper-function tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsers.errors import CorruptFileError, UnsupportedFormatError
from parsers.loader import load
from parsers.models import EmailMessage
from utils.helpers import (
    SUPPORTED_EXTS,
    human_size,
    is_supported_file,
    safe_filename,
    write_temp_attachment,
)


def test_load_dispatches_eml(sample_eml_file: Path) -> None:
    result = load(sample_eml_file)
    assert isinstance(result, EmailMessage)
    assert result.subject == "Quarterly report"


def test_load_unknown_extension(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    with pytest.raises(UnsupportedFormatError):
        load(p)


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CorruptFileError):
        load(tmp_path / "ghost.eml")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1536, "1.5 KB"),
        (5 * 1024 * 1024, "5.0 MB"),
    ],
)
def test_human_size(raw: int, expected: str) -> None:
    assert human_size(raw) == expected


def test_safe_filename_strips_paths_and_reserved() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename('bad:name?.txt') == "bad_name_.txt"
    assert safe_filename("CON.txt").startswith("_CON")
    assert safe_filename("") == "attachment"


def test_supported_exts_constant() -> None:
    assert SUPPORTED_EXTS == frozenset({".eml", ".msg", ".pst", ".ost"})
    assert not is_supported_file("whatever.zip")


def test_write_temp_attachment_roundtrip() -> None:
    p = write_temp_attachment("report.csv", b"a,b,c")
    assert p.exists()
    assert p.read_bytes() == b"a,b,c"
    # A second write with the same name must not clobber the first.
    p2 = write_temp_attachment("report.csv", b"different")
    assert p2 != p
    assert p.read_bytes() == b"a,b,c"

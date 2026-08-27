"""Tests for the .pst / .ost backend layer.

A real traversal test needs both a PST backend (``pypff`` or ``pypst``) and a
sample store at ``tests/data/sample.pst``. Without those, only backend
selection and error handling are checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parsers.errors import CorruptFileError, ParserError
from parsers.pst_parser import available_backends, open_pst

DATA = Path(__file__).parent / "data" / "sample.pst"


def test_open_nonexistent_raises() -> None:
    with pytest.raises(CorruptFileError):
        open_pst(Path("nope-not-here.pst"))


def test_open_garbage_file(tmp_path: Path) -> None:
    junk = tmp_path / "broken.pst"
    junk.write_bytes(b"this is definitely not a PST container")
    # Either a backend rejects the bytes (CorruptFileError) or no backend is
    # installed (ParserError) - both are ParserError subclasses / ParserError.
    with pytest.raises(ParserError):
        open_pst(junk)


def test_available_backends_is_list() -> None:
    assert isinstance(available_backends(), list)


@pytest.mark.skipif(not DATA.exists(), reason="no tests/data/sample.pst fixture present")
@pytest.mark.skipif(not available_backends(), reason="no PST backend installed")
def test_roundtrip_sample() -> None:
    doc = open_pst(DATA)
    try:
        assert doc.root is not None
        folders = [doc.root, *doc.root.iter_descendants()]
        assert folders
        for folder in folders:
            stubs = doc.backend.list_messages(folder.backend_id)
            if stubs:
                full = doc.backend.get_message(stubs[0].backend_id)
                assert isinstance(full.subject, str)
                break
    finally:
        doc.backend.close()

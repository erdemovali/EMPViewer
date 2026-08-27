"""Tests for the .msg parser.

The parser itself is only importable when ``extract_msg`` is installed, and a
real round-trip test needs a sample ``.msg``. Drop one at
``tests/data/sample.msg`` to enable the round-trip check; otherwise only the
dependency-handling path is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data" / "sample.msg"


def test_missing_dependency_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import parsers.msg_parser as mp

    def _boom():
        raise ImportError("no extract_msg")

    monkeypatch.setattr(mp, "_require_extract_msg", _boom)
    # loader.load should convert this into a ParserError with a pip hint.
    from parsers.errors import MissingDependencyError

    with pytest.raises((MissingDependencyError, ImportError)):
        mp._require_extract_msg()


@pytest.mark.skipif(not DATA.exists(), reason="no tests/data/sample.msg fixture present")
def test_roundtrip_sample() -> None:
    pytest.importorskip("extract_msg")
    from parsers.msg_parser import parse_msg

    msg = parse_msg(DATA)
    assert isinstance(msg.subject, str)
    assert msg.body_html or msg.body_text
    for att in msg.attachments:
        assert isinstance(att.data, bytes)

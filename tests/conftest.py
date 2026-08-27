"""Shared fixtures. Adds the repo root to ``sys.path`` so ``parsers`` imports work."""

from __future__ import annotations

import sys
from email.message import EmailMessage as PyEmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def sample_eml_bytes() -> bytes:
    """A multipart/alternative + inline image + attachment message."""

    msg = PyEmailMessage()
    msg["Subject"] = "Quarterly =?utf-8?b?cmVwb3J0?="  # encoded-word -> "report"
    msg["From"] = "Alice Example <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>, carol@example.com"
    msg["Cc"] = "dave@example.com"
    msg["Date"] = "Tue, 5 Mar 2024 10:30:00 +0000"
    msg["Message-ID"] = "<abc123@example.com>"
    msg.set_content("Plain text body\nLine two")
    msg.add_alternative(
        "<html><body><p>HTML body</p>"
        '<img src="cid:logo123"></body></html>',
        subtype="html",
    )
    # Inline image referenced by the HTML part.
    msg.get_payload()[1].add_related(
        b"\x89PNG\r\n\x1a\n-fake-png-bytes",
        maintype="image",
        subtype="png",
        cid="<logo123>",
    )
    msg.add_attachment(
        b"col1,col2\n1,2\n",
        maintype="text",
        subtype="csv",
        filename="data.csv",
    )
    return msg.as_bytes()


@pytest.fixture
def sample_eml_file(tmp_path: Path, sample_eml_bytes: bytes) -> Path:
    p = tmp_path / "sample.eml"
    p.write_bytes(sample_eml_bytes)
    return p

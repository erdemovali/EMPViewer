"""Message-list model: sort keys and the sender/subject filter proxy."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from parsers.models import MessageStub  # noqa: E402
from ui.main_window import EmailListModel, MailFilterProxy, _SORT_ROLE  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _model() -> EmailListModel:
    m = EmailListModel()
    m.set_stubs(
        [
            MessageStub(backend_id=1, sender="Alice <a@x.com>", subject="Hello world",
                        date=datetime(2024, 1, 2, 10, 0)),
            MessageStub(backend_id=2, sender="Bob <b@y.com>", subject="Report Q3",
                        date=datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc)),
            MessageStub(backend_id=3, sender="Carol", subject="", date=None),
        ]
    )
    return m


def test_display_and_sort_roles():
    m = _model()
    assert m.index(0, 0).data(Qt.ItemDataRole.DisplayRole) == "Alice <a@x.com>"
    assert m.index(0, 1).data(Qt.ItemDataRole.DisplayRole) == "Hello world"
    assert m.index(2, 1).data(Qt.ItemDataRole.DisplayRole) == "(no subject)"

    assert m.index(1, 0).data(_SORT_ROLE) == "bob <b@y.com>"
    assert isinstance(m.index(0, 2).data(_SORT_ROLE), float)
    assert m.index(2, 2).data(_SORT_ROLE) == float("-inf")  # missing date sorts last


def test_filter_matches_sender_or_subject_case_insensitively():
    proxy = MailFilterProxy()
    proxy.setSourceModel(_model())
    assert proxy.rowCount() == 3

    proxy.set_needle("REPORT")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "Bob <b@y.com>"

    proxy.set_needle("carol")
    assert proxy.rowCount() == 1

    proxy.set_needle("nothing here")
    assert proxy.rowCount() == 0

    proxy.set_needle("")
    assert proxy.rowCount() == 3


def test_sort_by_date_descending_puts_newest_first():
    proxy = MailFilterProxy()
    proxy.setSourceModel(_model())
    proxy.sort(2, Qt.SortOrder.DescendingOrder)
    senders = [proxy.index(r, 0).data() for r in range(proxy.rowCount())]
    assert senders == ["Bob <b@y.com>", "Alice <a@x.com>", "Carol"]

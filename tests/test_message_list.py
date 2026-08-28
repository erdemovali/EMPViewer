"""Message-list model: columns, sort keys, unread/attachment cues, filter proxy."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from parsers.models import MessageStub  # noqa: E402
from ui.main_window import (  # noqa: E402
    COL_ATTACH,
    COL_DATE,
    COL_SENDER,
    COL_SIZE,
    COL_SUBJECT,
    EmailListModel,
    MailFilterProxy,
    thread_key,
    _SORT_ROLE,
)

_app = QApplication.instance() or QApplication([])


def _model() -> EmailListModel:
    m = EmailListModel()
    m.set_stubs(
        [
            MessageStub(backend_id=1, sender="Alice <a@x.com>", subject="Hello world",
                        date=datetime(2024, 1, 2, 10, 0), unread=True),
            MessageStub(backend_id=2, sender="Bob <b@y.com>", subject="Report Q3",
                        date=datetime(2024, 3, 5, 9, 0, tzinfo=timezone.utc),
                        has_attachments=True, size=2048),
            MessageStub(backend_id=3, sender="Carol", subject="", date=None),
        ]
    )
    return m


def test_display_and_sort_roles():
    m = _model()
    assert m.index(0, COL_SENDER).data(Qt.ItemDataRole.DisplayRole) == "Alice <a@x.com>"
    assert m.index(0, COL_SUBJECT).data(Qt.ItemDataRole.DisplayRole) == "Hello world"
    assert m.index(2, COL_SUBJECT).data(Qt.ItemDataRole.DisplayRole) == "(no subject)"

    assert m.index(1, COL_SENDER).data(_SORT_ROLE) == "bob <b@y.com>"
    assert isinstance(m.index(0, COL_DATE).data(_SORT_ROLE), float)
    assert m.index(2, COL_DATE).data(_SORT_ROLE) == float("-inf")  # missing date sorts last


def test_attachment_and_size_columns():
    m = _model()
    assert m.index(1, COL_ATTACH).data(Qt.ItemDataRole.DisplayRole) == "\U0001F4CE"
    assert m.index(0, COL_ATTACH).data(Qt.ItemDataRole.DisplayRole) == ""
    assert m.index(1, COL_SIZE).data(Qt.ItemDataRole.DisplayRole) == "2.0 KB"
    assert m.index(0, COL_SIZE).data(Qt.ItemDataRole.DisplayRole) == ""
    assert m.index(1, COL_ATTACH).data(_SORT_ROLE) == 1
    assert m.index(1, COL_SIZE).data(_SORT_ROLE) == 2048


def test_unread_rows_render_bold():
    m = _model()
    font = m.index(0, COL_SUBJECT).data(Qt.ItemDataRole.FontRole)
    assert isinstance(font, QFont) and font.bold()
    assert m.index(1, COL_SUBJECT).data(Qt.ItemDataRole.FontRole) is None


def test_filter_matches_sender_or_subject_case_insensitively():
    proxy = MailFilterProxy()
    proxy.setSourceModel(_model())
    assert proxy.rowCount() == 3

    proxy.set_needle("REPORT")
    assert proxy.rowCount() == 1
    assert proxy.index(0, COL_SENDER).data() == "Bob <b@y.com>"

    proxy.set_needle("carol")
    assert proxy.rowCount() == 1

    proxy.set_needle("nothing here")
    assert proxy.rowCount() == 0

    proxy.set_needle("")
    assert proxy.rowCount() == 3


def test_sort_by_date_descending_puts_newest_first():
    proxy = MailFilterProxy()
    proxy.setSourceModel(_model())
    proxy.sort(COL_DATE, Qt.SortOrder.DescendingOrder)
    senders = [proxy.index(r, COL_SENDER).data() for r in range(proxy.rowCount())]
    assert senders == ["Bob <b@y.com>", "Alice <a@x.com>", "Carol"]


def test_thread_key_strips_reply_prefixes():
    assert thread_key("Re: Re: Weekly report") == "weekly report"
    assert thread_key("FW: Fwd: Lunch") == "lunch"
    assert thread_key("YNT: Toplantı") == "toplantı"  # Turkish "Re:"
    assert thread_key("Plain subject") == "plain subject"


def test_group_by_conversation_keeps_replies_together():
    from datetime import datetime as _dt

    m = EmailListModel()
    m.set_stubs([
        MessageStub(backend_id=1, sender="a", subject="Re: Budget", date=_dt(2024, 2, 1)),
        MessageStub(backend_id=2, sender="b", subject="Apples", date=_dt(2024, 3, 1)),
        MessageStub(backend_id=3, sender="c", subject="Budget", date=_dt(2024, 1, 1)),
        MessageStub(backend_id=4, sender="d", subject="Apples", date=_dt(2024, 1, 15)),
    ])
    proxy = MailFilterProxy()
    proxy.setSourceModel(m)
    proxy.set_group(True)
    proxy.sort(COL_DATE)
    subjects = [proxy.index(r, COL_SUBJECT).data() for r in range(proxy.rowCount())]
    # "Apples" rows contiguous; "Budget" + "Re: Budget" contiguous; newest first inside.
    assert subjects == ["Apples", "Apples", "Re: Budget", "Budget"]

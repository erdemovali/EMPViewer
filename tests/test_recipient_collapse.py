"""Header recipients collapse to a "+N more" link instead of filling the pane."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from parsers.models import EmailMessage  # noqa: E402
from ui.viewer_widget import ViewerWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

_N = ViewerWidget.RECIPIENTS_COLLAPSED
_TOGGLE = ViewerWidget._TOGGLE_URL


def _many(n: int) -> list[str]:
    return [f"person{i}@example.com" for i in range(n)]


def test_short_list_is_shown_whole_with_no_link() -> None:
    html = ViewerWidget._recipient_html(_many(_N), expanded=False)
    assert _TOGGLE not in html
    assert "person0@example.com" in html
    assert f"person{_N - 1}@example.com" in html


def test_long_list_is_trimmed_to_a_link() -> None:
    names = _many(_N + 12)
    html = ViewerWidget._recipient_html(names, expanded=False)

    assert _TOGGLE in html
    assert "12 more" in html
    assert names[_N - 1] in html          # the last one still shown
    assert names[_N] not in html          # the first one hidden


def test_expanded_shows_everyone_and_offers_show_less() -> None:
    names = _many(_N + 12)
    html = ViewerWidget._recipient_html(names, expanded=True)

    assert all(n in html for n in names)
    assert _TOGGLE in html                # the "show less" affordance


def test_expanding_a_short_list_adds_no_show_less() -> None:
    # Nothing was hidden, so there is nothing to collapse back to.
    html = ViewerWidget._recipient_html(_many(_N), expanded=True)
    assert _TOGGLE not in html


def test_recipient_names_are_escaped() -> None:
    html = ViewerWidget._recipient_html(['"<b>Ann</b>" <a@b.c>'], expanded=False)
    assert "<b>Ann</b>" not in html
    assert "&lt;" in html


def test_toggle_round_trips_and_resets_between_messages() -> None:
    viewer = ViewerWidget()
    crowded = EmailMessage(subject="Announcement", sender="boss@example.com",
                           to=_many(_N + 20))
    small = EmailMessage(subject="Note", sender="a@b.c", to=["x@y.z"])

    viewer.set_message(crowded)
    collapsed = viewer.lbl_meta.text()
    assert "20 more" in collapsed

    viewer._on_meta_link(_TOGGLE)
    assert _many(_N + 20)[-1] in viewer.lbl_meta.text()

    viewer._on_meta_link(_TOGGLE)
    assert viewer.lbl_meta.text() == collapsed

    # An unrelated link must not toggle anything.
    viewer._on_meta_link("https://example.com")
    assert viewer.lbl_meta.text() == collapsed

    # Opening another message and coming back starts collapsed again.
    viewer._on_meta_link(_TOGGLE)
    viewer.set_message(small)
    viewer.set_message(crowded)
    assert viewer.lbl_meta.text() == collapsed

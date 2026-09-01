"""Session restore: reading the saved path list and deciding how each comes back.

The window itself is never built here - there is no pytest-qt in this project and
no test constructs a MainWindow - so the restore logic lives in module-level
helpers that can be exercised directly.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import (  # noqa: E402
    RESTORE_EAGER,
    RESTORE_LAZY,
    _jsonable,
    plan_restore,
    session_paths,
)

_app = QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# session_paths
# --------------------------------------------------------------------------- #
def test_session_paths_handles_a_list() -> None:
    assert session_paths(["a.pst", "b.eml"]) == ["a.pst", "b.eml"]


def test_session_paths_handles_the_single_string_qsettings_returns() -> None:
    # QSettings collapses a one-element list to a bare string on round-trip.
    assert session_paths("only.pst") == ["only.pst"]


def test_session_paths_handles_empty_and_missing() -> None:
    assert session_paths(None) == []
    assert session_paths([]) == []
    assert session_paths("") == []


# --------------------------------------------------------------------------- #
# plan_restore
# --------------------------------------------------------------------------- #
def test_plan_restore_defers_stores_and_opens_messages(tmp_path) -> None:
    pst = tmp_path / "mail.pst"
    pst.write_bytes(b"!BDN")
    ost = tmp_path / "cache.ost"
    ost.write_bytes(b"!BDN")
    eml = tmp_path / "note.eml"
    eml.write_text("Subject: hi\n\nbody\n", encoding="utf-8")
    msg = tmp_path / "note.msg"
    msg.write_bytes(b"\xd0\xcf\x11\xe0")
    folder = tmp_path / "scanned"
    folder.mkdir()

    plan = dict(plan_restore([str(pst), str(ost), str(eml), str(msg), str(folder)]))

    # Big stores and directories wait to be clicked; single messages are cheap.
    assert plan[str(pst)] == RESTORE_LAZY
    assert plan[str(ost)] == RESTORE_LAZY
    assert plan[str(folder)] == RESTORE_LAZY
    assert plan[str(eml)] == RESTORE_EAGER
    assert plan[str(msg)] == RESTORE_EAGER


def test_plan_restore_drops_paths_that_are_gone(tmp_path) -> None:
    alive = tmp_path / "alive.eml"
    alive.write_text("Subject: hi\n\nbody\n", encoding="utf-8")
    gone = tmp_path / "deleted.pst"

    assert plan_restore([str(gone), str(alive)]) == [(str(alive), RESTORE_EAGER)]


def test_plan_restore_preserves_order_and_deduplicates(tmp_path) -> None:
    a = tmp_path / "a.eml"
    a.write_text("Subject: a\n\n", encoding="utf-8")
    b = tmp_path / "b.pst"
    b.write_bytes(b"!BDN")

    plan = plan_restore([str(b), str(a), str(b)])
    assert [p for p, _ in plan] == [str(b), str(a)]


def test_plan_restore_ignores_blank_entries() -> None:
    assert plan_restore(["", None]) == []


# --------------------------------------------------------------------------- #
# The saved selection travels as a search-hit target
# --------------------------------------------------------------------------- #
def test_last_target_survives_the_json_round_trip() -> None:
    # _open_search_hit is handed this dict verbatim, so whatever we persist must
    # come back with the same shape - backend ids included.
    target = {
        "kind": "pst",
        "path": r"C:\mail\archive.pst",
        "folder": "Inbox",
        "bid": _jsonable((0x8082, 0x200C4)),
    }
    back = json.loads(json.dumps(target))

    assert back == target
    assert back["bid"] == [0x8082, 0x200C4]
    # _on_folder_loaded compares _jsonable(stub.backend_id) against this value.
    assert _jsonable((0x8082, 0x200C4)) == back["bid"]

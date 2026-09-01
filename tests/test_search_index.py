"""utils.search_index: query parsing + FTS5 search."""

from __future__ import annotations

from datetime import datetime

from utils.search_index import SearchIndex, parse_query


def _idx() -> SearchIndex:
    ix = SearchIndex()
    ix.add({"kind": "pst", "id": 1}, source="a.pst", sender="Alice <alice@x.com>",
            recipients="bob@x.com", subject="Quarterly invoice", body="the invoice is overdue",
            folder="Inbox", date=datetime(2024, 3, 5, 10, 0), has_attachments=True)
    ix.add({"kind": "pst", "id": 2}, source="a.pst", sender="Carl <carl@y.com>",
            recipients="alice@x.com", subject="Lunch?", body="wanna grab lunch",
            folder="Inbox", date=datetime(2024, 1, 2, 12, 0), has_attachments=False)
    ix.add({"kind": "file", "path": "n.eml"}, source="n.eml", sender="Dana",
            recipients="", subject="Notes", body="invoice numbers for reference",
            folder="", date=datetime(2023, 12, 1), has_attachments=False)
    return ix


def test_parse_query_fields_and_filters() -> None:
    q = parse_query('invoice from:alice has:attach after:2024-01-01 subject:"year end"')
    assert "sender : \"alice\"" in q.match
    assert "subject : \"year end\"" in q.match
    assert '"invoice"' in q.match
    assert q.has_attach is True
    assert q.after == "2024-01-01"
    assert q.empty is False


def test_free_term_matches_body_and_subject() -> None:
    ix = _idx()
    hits = ix.search("invoice")
    subjects = {h.subject for h in hits}
    assert subjects == {"Quarterly invoice", "Notes"}


def test_from_filter_and_has_attach() -> None:
    ix = _idx()
    hits = ix.search("from:alice has:attach")
    assert len(hits) == 1
    assert hits[0].target == {"kind": "pst", "id": 1}
    assert hits[0].has_attach is True


def test_date_range() -> None:
    ix = _idx()
    hits = ix.search("after:2024-02-01")
    assert [h.subject for h in hits] == ["Quarterly invoice"]
    hits = ix.search("before:2023-12-31")
    assert [h.subject for h in hits] == ["Notes"]


def test_empty_query_returns_nothing() -> None:
    assert _idx().search("   ") == []


def test_remove_source_and_body_sweep() -> None:
    ix = SearchIndex()
    tgt = {"kind": "pst", "id": 9}
    ix.add(tgt, source="b.pst", subject="Hello", body="")
    assert ix.bodies_missing("b.pst") == [tgt]
    ix.set_body(tgt, "now it has a body about penguins")
    assert ix.bodies_missing("b.pst") == []
    assert [h.subject for h in ix.search("penguins")] == ["Hello"]
    ix.remove_source("b.pst")
    assert ix.count() == 0


def test_calls_after_close_are_silent_no_ops() -> None:
    # Indexing runs on a worker thread that can outlive the window: shutdown
    # waits only a couple of seconds, and walking a big store takes far longer.
    # A late write must not raise "Cannot operate on a closed database".
    ix = _idx()
    ix.close()
    assert ix.closed is True

    ix.add({"kind": "pst", "id": 3}, source="a.pst", subject="late arrival")
    ix.set_body({"kind": "pst", "id": 1}, "late body")
    ix.commit()
    ix.remove_source("a.pst")

    assert ix.search("invoice") == []
    assert ix.bodies_missing("a.pst") == []
    assert ix.count() == 0
    ix.close()  # idempotent

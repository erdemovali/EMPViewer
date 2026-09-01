"""A small SQLite FTS5 index over the messages that are currently open.

One instance lives for the process; :class:`ui.main_window.MainWindow` feeds it
message stubs as folders load and (via a background sweep) their bodies, then
queries it for the search panel.

Query syntax accepted by :meth:`SearchIndex.search`::

    invoice overdue            free terms (all must match, FTS5)
    from:alice                 sender contains "alice"
    to:bob                     any recipient contains "bob"
    subject:"year end"         quoted phrase in the subject
    has:attach                 only messages with attachments
    after:2024-01-01           on/after this date
    before:2024-03-31          on/before this date
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Hit:
    target: dict
    sender: str
    subject: str
    folder: str
    date: datetime | None
    has_attach: bool
    snippet: str = ""


@dataclass(slots=True)
class _Query:
    match: str = ""
    has_attach: bool = False
    after: str | None = None
    before: str | None = None
    empty: bool = field(default=True)


_FIELD_ALIASES = {"from": "sender", "to": "recipients", "cc": "recipients",
                  "subject": "subject", "body": "body"}
_TOKEN_RE = re.compile(r'(-?\w+:)?("[^"]*"|\S+)')


def _fts_quote(term: str) -> str:
    term = term.strip('"')
    term = term.replace('"', '""')
    return f'"{term}"'


def parse_query(text: str) -> _Query:
    q = _Query()
    match_parts: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        key = (m.group(1) or "").rstrip(":").lower()
        raw = m.group(2)
        val = raw.strip('"')
        if key == "has" and val.lower() in ("attach", "attachment", "attachments"):
            q.has_attach = True
        elif key == "after" and val:
            q.after = val
        elif key == "before" and val:
            q.before = val
        elif key in _FIELD_ALIASES and val:
            match_parts.append(f"{_FIELD_ALIASES[key]} : {_fts_quote(val)}")
        elif val:
            match_parts.append(_fts_quote(val))
    q.match = " AND ".join(match_parts)
    q.empty = not (q.match or q.has_attach or q.after or q.before)
    return q


class SearchIndex:
    _COLUMNS = "sender, recipients, subject, body"

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        #: Set by :meth:`close`. Background indexing runs on a worker thread and
        #: can outlive the window that owns this index - shutdown only waits a
        #: couple of seconds, and a large store takes far longer than that to
        #: walk. Every call below turns into a no-op once this is set, so a
        #: late write stops the indexer quietly instead of raising
        #: ``ProgrammingError: Cannot operate on a closed database``.
        self._closed = False
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5("
            " sender, recipients, subject, body,"
            " folder UNINDEXED, doc_key UNINDEXED, source UNINDEXED,"
            " date_iso UNINDEXED, has_attach UNINDEXED,"
            " tokenize = 'unicode61 remove_diacritics 2')"
        )
        self._db.commit()

    # -- writes ---------------------------------------------------------- #
    def add(
        self,
        target: dict,
        *,
        source: str,
        sender: str = "",
        recipients: str = "",
        subject: str = "",
        body: str = "",
        folder: str = "",
        date: datetime | None = None,
        has_attachments: bool = False,
    ) -> None:
        key = json.dumps(target, sort_keys=True)
        with self._lock:
            if self._closed:
                return
            self._db.execute("DELETE FROM messages WHERE doc_key = ?", (key,))
            self._db.execute(
                "INSERT INTO messages"
                " (sender, recipients, subject, body, folder, doc_key, source,"
                "  date_iso, has_attach)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (sender, recipients, subject, body, folder, key, source,
                 date.isoformat() if date else "", 1 if has_attachments else 0),
            )

    def set_body(self, target: dict, body: str) -> None:
        key = json.dumps(target, sort_keys=True)
        with self._lock:
            if self._closed:
                return
            self._db.execute(
                "UPDATE messages SET body = ? WHERE doc_key = ?", (body, key)
            )

    def commit(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._db.commit()

    def remove_source(self, source: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._db.execute("DELETE FROM messages WHERE source = ?", (source,))
            self._db.commit()

    def bodies_missing(self, source: str, limit: int = 200) -> list[dict]:
        with self._lock:
            if self._closed:
                return []
            rows = self._db.execute(
                "SELECT doc_key FROM messages WHERE source = ? AND body = '' LIMIT ?",
                (source, limit),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # -- reads --------------------------------------------------------- #
    def count(self) -> int:
        with self._lock:
            if self._closed:
                return 0
            return int(self._db.execute("SELECT count(*) FROM messages").fetchone()[0])

    @property
    def closed(self) -> bool:
        return self._closed

    def search(self, text: str, *, limit: int = 500) -> list[Hit]:
        q = parse_query(text)
        if q.empty:
            return []

        where: list[str] = []
        params: list = []
        select_snip = "''"
        order = "date_iso DESC"
        if q.match:
            where.append("messages MATCH ?")
            params.append(q.match)
            select_snip = "snippet(messages, 3, char(171), char(187), ' … ', 12)"
            order = "rank"
        if q.has_attach:
            where.append("has_attach = 1")
        if q.after:
            where.append("date_iso >= ?")
            params.append(q.after)
        if q.before:
            where.append("date_iso <= ?")
            params.append(q.before + "￿")  # inclusive of the whole day

        sql = (
            f"SELECT doc_key, sender, subject, folder, date_iso, has_attach, {select_snip}"
            f" FROM messages WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            if self._closed:
                return []
            try:
                rows = self._db.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []  # malformed FTS expression - treat as no results

        hits: list[Hit] = []
        for key, sender, subject, folder, date_iso, has_attach, snip in rows:
            dt = None
            if date_iso:
                try:
                    dt = datetime.fromisoformat(date_iso)
                except ValueError:
                    dt = None
            hits.append(Hit(
                target=json.loads(key), sender=sender or "", subject=subject or "",
                folder=folder or "", date=dt, has_attach=bool(has_attach),
                snippet=snip or "",
            ))
        return hits

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass

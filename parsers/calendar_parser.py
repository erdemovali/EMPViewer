"""``.ics`` (iCalendar) parsing -> a rendered invite card.

The result is a normal :class:`~parsers.models.EmailMessage` so the existing
viewer shows it with no special-casing: subject = event summary, sender =
organiser, date = start time, body = an HTML card.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from . import _ical
from .errors import CorruptFileError
from .models import EmailMessage


def _esc(text: str) -> str:
    return _html.escape(text or "")


def _clean_cal_user(value: str) -> str:
    """``"mailto:alice@x.com"`` / ``"MAILTO:alice@x.com"`` -> ``"alice@x.com"``."""

    v = (value or "").strip()
    return v[7:] if v[:7].lower() == "mailto:" else v


def _fmt_when(start, end) -> str:
    from utils.helpers import format_datetime

    if start is None:
        return ""
    s = format_datetime(start)
    if end is not None:
        e = format_datetime(end)
        if end.date() == start.date():
            e = e.split(" ", 1)[-1]  # same day -> time only
        return f"{s} → {e}"
    return s


def _events(lines: list[str]) -> list[dict]:
    events: list[dict] = []
    cur: dict | None = None
    for line in lines:
        name, params, value = _ical.split_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {"attendees": []}
        elif name == "END" and value.upper() == "VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is None:
            continue
        elif name == "SUMMARY":
            cur["summary"] = _ical.unescape_text(value)
        elif name == "LOCATION":
            cur["location"] = _ical.unescape_text(value)
        elif name == "DESCRIPTION":
            cur["description"] = _ical.unescape_text(value)
        elif name == "DTSTART":
            cur["start"] = _ical.parse_dt(value)
        elif name == "DTEND":
            cur["end"] = _ical.parse_dt(value)
        elif name == "ORGANIZER":
            cur["organizer"] = params.get("CN") or _clean_cal_user(value)
        elif name == "ATTENDEE":
            cur["attendees"].append(params.get("CN") or _clean_cal_user(value))
        elif name == "RRULE":
            cur["rrule"] = value
        elif name == "STATUS":
            cur["status"] = value
    return events


def _card_html(events: list[dict], method: str) -> str:
    rows: list[str] = []
    for ev in events:
        parts = [f"<h2 style='margin:0 0 8px'>\U0001F4C5 {_esc(ev.get('summary') or '(untitled event)')}</h2>"]
        when = _fmt_when(ev.get("start"), ev.get("end"))
        if when:
            parts.append(f"<p><b>When:</b> {_esc(when)}"
                         + (f" &nbsp;<i>({_esc(ev['rrule'])})</i>" if ev.get("rrule") else "")
                         + "</p>")
        if ev.get("location"):
            parts.append(f"<p><b>Where:</b> {_esc(ev['location'])}</p>")
        if ev.get("organizer"):
            parts.append(f"<p><b>Organiser:</b> {_esc(ev['organizer'])}</p>")
        if ev.get("attendees"):
            parts.append(f"<p><b>Attendees:</b> {_esc(', '.join(ev['attendees']))}</p>")
        if ev.get("status"):
            parts.append(f"<p><b>Status:</b> {_esc(ev['status'])}</p>")
        if ev.get("description"):
            parts.append("<hr><pre style='white-space:pre-wrap;font-family:inherit'>"
                         + _esc(ev["description"]) + "</pre>")
        rows.append(
            "<div style='border:1px solid #d0d7de;border-radius:10px;padding:14px;margin:0 0 12px'>"
            + "".join(parts) + "</div>"
        )
    banner = ""
    if method:
        banner = (f"<p style='color:#57606a'>Calendar method: <b>{_esc(method)}</b></p>")
    return ("<div style='font-family:sans-serif;padding:16px;max-width:680px'>"
            + banner + "".join(rows) + "</div>")


def parse_ics_bytes(raw: bytes, *, source_path: str | None = None) -> EmailMessage:
    text = raw.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text.upper():
        raise CorruptFileError(source_path or "<bytes>", "Not an iCalendar file.")
    lines = _ical.unfold(text)
    method = ""
    for line in lines:
        name, _p, value = _ical.split_line(line)
        if name == "METHOD":
            method = value
            break
    events = _events(lines)
    if not events:
        raise CorruptFileError(source_path or "<bytes>", "No calendar events found.")

    first = events[0]
    return EmailMessage(
        subject=first.get("summary") or "Calendar event",
        sender=first.get("organizer") or "",
        to=first.get("attendees", []),
        date=first.get("start"),
        body_html=_card_html(events, method),
        source_path=source_path,
        raw_source=raw,
    )


def parse_ics(path: str | Path) -> EmailMessage:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise CorruptFileError(str(p), f"Could not read the file: {exc}") from exc
    return parse_ics_bytes(raw, source_path=str(p))

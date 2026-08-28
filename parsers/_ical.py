"""Minimal iCalendar / vCard line parser (stdlib only).

Both formats share the same content-line grammar (RFC 5545 / RFC 6350):

    NAME;PARAM=value;PARAM=value:VALUE

with long lines folded by starting the continuation with a space or tab, and
TEXT values escaping ``\\n \\, \\; \\\\``.
"""

from __future__ import annotations

from datetime import datetime


def unfold(text: str) -> list[str]:
    """Undo RFC line folding -> a list of logical lines."""

    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return [ln for ln in out if ln]


def split_line(line: str) -> tuple[str, dict[str, str], str]:
    """``"DTSTART;TZID=Europe/Istanbul:20240305T103000"`` ->
    ``("DTSTART", {"TZID": "Europe/Istanbul"}, "20240305T103000")``."""

    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name, params, value


_UNESCAPE = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}


def unescape_text(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(_UNESCAPE.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_dt(value: str) -> datetime | None:
    """Parse an iCalendar DATE / DATE-TIME value (with or without trailing Z)."""

    v = value.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%dT%H%MZ", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None
